from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .history import append_entry, recent_entries
from .notify import send as notify, send_plan
from .planner import Plan, make_plan
from .rainbird_client import RainbirdClient
from .weather import WeatherWindow, fetch_weather

VISION_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "vision_cache.json"
VISION_CACHE_MAX_AGE_HOURS = 28


def _load_vision_cache() -> dict | None:
    if not VISION_CACHE_PATH.exists():
        return None
    try:
        cached = json.loads(VISION_CACHE_PATH.read_text())
        captured_at = datetime.fromisoformat(cached["captured_at"])
        age_hours = (datetime.now(timezone.utc) - captured_at).total_seconds() / 3600
        if age_hours > VISION_CACHE_MAX_AGE_HOURS:
            print(f"      vision cache is {age_hours:.1f}h old (>{VISION_CACHE_MAX_AGE_HOURS}h) — skipping")
            return None
        print(f"      vision cache age: {age_hours:.1f}h (captured {captured_at.strftime('%Y-%m-%d %H:%M %Z')})")
        return cached["vision"]
    except Exception as e:
        print(f"      vision cache unreadable: {e}")
        return None


def _save_vision_cache(vision: dict) -> None:
    VISION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VISION_CACHE_PATH.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "vision": vision,
    }, indent=2))


async def _do_vision_snapshot(config: Config, weather: WeatherWindow) -> dict | None:
    """Capture camera snapshots and run vision analysis. Returns result or None."""
    if not os.environ.get("NEST_REFRESH_TOKEN"):
        return None
    if not config.camera_targets:
        print("      vision: NEST_REFRESH_TOKEN set but no camera_targets in config.yaml — skipping")
        return None
    try:
        from .nest import NestClient, analyze_images
        from zoneinfo import ZoneInfo
        nest = NestClient.from_env()
        devs = await nest.list_devices()
        targets = {}
        for target in config.camera_targets:
            for d in devs:
                if target.room_keyword in d.room.lower() and target.label not in targets:
                    targets[target.label] = d
                    break

        images: dict[str, bytes] = {}
        for label, dev in targets.items():
            print(f"      capturing {label}...")
            images[label] = await nest.snapshot(dev.name, timeout_s=20)
            await asyncio.sleep(3)

        if not images:
            return None

        past_mm = sum(d.precipitation_mm for d in weather.past)
        fcast_mm = sum(d.precipitation_mm for d in weather.forecast)
        zone_lines = []
        for z in config.zones:
            zone_lines.append(
                f"Zone {z.number} {z.name}: sun={z.sun[:80]}, slope={z.slope_percent}%"
            )
        if config.invisible_zones_note:
            zone_lines.append(config.invisible_zones_note)
        zones_brief = "\n".join(zone_lines)
        now = datetime.now(ZoneInfo(config.location.timezone))
        return analyze_images(
            claude_bin=config.claude_bin,
            images=images,
            weather_summary=f"past 3d {past_mm:.1f}mm, forecast 5d {fcast_mm:.1f}mm",
            time_context=now.strftime("%Y-%m-%d %H:%M %Z (%A)"),
            zone_assumptions=zones_brief,
            camera_descriptions=config.camera_descriptions,
            invisible_zones_note=config.invisible_zones_note,
            model=config.model,
            gemini_bin=config.gemini_bin,
            gemini_model=config.gemini_model,
        )
    except Exception as e:
        import traceback
        print(f"      vision capture/analysis failed: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        return None


async def _run_vision_snapshot() -> int:
    """Standalone vision capture — saves result to cache. Run mid-morning."""
    config = Config.load()
    print(f"[1/2] Fetching weather for vision context...")
    weather = fetch_weather(
        config.location.latitude,
        config.location.longitude,
        config.location.timezone,
    )
    print(f"[2/2] Capturing camera snapshots + vision analysis...")
    vision = await _do_vision_snapshot(config, weather)
    if vision is None:
        print("      vision: not configured or capture failed")
        return 1
    usable = vision.get("view_usable", True)
    health = vision.get("overall_health", "unknown")
    obs_count = len(vision.get("observations", []))
    print(f"      vision: usable={usable}, health={health}, {obs_count} observations")
    _save_vision_cache(vision)
    print(f"      saved to {VISION_CACHE_PATH}")
    return 0


def _print_plan(plan: Plan) -> None:
    print(f"\nDecision: {'SKIP' if plan.skip else 'WATER'}")
    print(f"Reason: {plan.reason}")
    if plan.zones:
        print(f"\nPer-zone plan (total {plan.total_minutes()} min water):")
        for z in plan.zones:
            if z.cycles > 1:
                print(f"  zone {z.zone}: {z.minutes} min total "
                      f"= {z.cycles}×{z.per_cycle_minutes()} min with {z.soak_minutes} min soak — {z.reason}")
            else:
                print(f"  zone {z.zone}: {z.minutes} min — {z.reason}")
    if plan.recommendations:
        print(f"\nRecommendations:")
        for r in plan.recommendations:
            print(f"  [{r.priority}] {r.action} — {r.reason}")
    print()


async def _run(dry_run: bool) -> int:
    config = Config.load()
    bot_name = config.bot.name
    avatar_url = config.bot.avatar_url

    print(f"[1/5] Fetching weather for {config.location.latitude},{config.location.longitude}...")
    weather = fetch_weather(
        config.location.latitude,
        config.location.longitude,
        config.location.timezone,
    )
    past_total = sum(d.precipitation_mm for d in weather.past)
    forecast_total = sum(d.precipitation_mm for d in weather.forecast)
    print(f"      Past {len(weather.past)}d rain: {past_total:.1f}mm, "
          f"forecast {len(weather.forecast)}d: {forecast_total:.1f}mm")

    if dry_run and not (config.rainbird_host and config.rainbird_password):
        print("[2/5] Skipping Rainbird (dry-run, no credentials configured)")
        rain_wet = False
    else:
        print("[2/5] Reading rain sensor from Rainbird...")
        async with RainbirdClient(config.rainbird_host, config.rainbird_password) as rb:
            rain_wet = await rb.rain_sensor_wet()
        print(f"      Rain sensor wet: {rain_wet}")
        if rain_wet:
            append_entry({
                "action": "skip",
                "reason": "rain sensor wet",
                "weather": weather.to_prompt_dict(),
            })
            notify(config.notifications.discord_webhook_url,
                   "Sprinkler skipped", "Rain sensor is wet — no watering today.",
                   tags="umbrella", bot_name=bot_name, avatar_url=avatar_url)
            print("Rain sensor wet — exiting without calling Claude.")
            return 0

    print("[2.5/5] Reading Ecowitt soil sensors...")
    soil_readings: dict[int, int] = {}
    if config.ecowitt_host:
        try:
            from .ecowitt import fetch_snapshot
            snapshot = await fetch_snapshot(config.ecowitt_host)
            by_ch = snapshot.by_channel()
            for z in config.zones:
                if z.soil_sensor_channel and z.soil_sensor_channel in by_ch:
                    soil_readings[z.number] = by_ch[z.soil_sensor_channel].moisture_pct
            if soil_readings:
                summary = ", ".join(f"zone {k}: {v}%" for k, v in sorted(soil_readings.items()))
                print(f"      soil: {summary}")
            else:
                print("      soil: gateway reachable but no WH51 channels matched config")
        except Exception as e:
            print(f"      Ecowitt fetch failed: {e} — continuing without soil data")
    else:
        print("      Ecowitt not configured (ecowitt_host empty) — skipping")

    print("[3/5] Loading recent history...")
    history = recent_entries(days=14)
    print(f"      {len(history)} entries in last 14 days")

    print("[3.5/5] Loading vision cache (captured at daylight by separate timer)...")
    vision = _load_vision_cache()
    if vision is None:
        print("      vision: no fresh cache — continuing without "
              "(run `sprinkler-ai --vision-snapshot` at daylight)")
    elif not vision:
        print("      vision: cache empty — see stderr for cause")
    else:
        usable = vision.get("view_usable", True)
        health = vision.get("overall_health", "unknown")
        obs_count = len(vision.get("observations", []))
        print(f"      vision: usable={usable}, health={health}, {obs_count} observations")

    print(f"[4/5] Asking Claude ({config.model}) for plan...")
    plan = make_plan(config, weather, rain_wet, history, vision=vision,
                    soil_readings=soil_readings or None)
    _print_plan(plan)

    zone_names = {z.number: z.name for z in config.zones}
    send_plan(
        config.notifications.discord_webhook_url,
        plan,
        zone_names=zone_names,
        soil_readings=soil_readings or None,
        dry_run=dry_run,
        bot_name=bot_name,
        avatar_url=avatar_url,
    )

    if dry_run:
        print("[5/5] Dry-run — not executing.")
        append_entry({
            "action": "dry_run",
            "plan": {
                "skip": plan.skip,
                "reason": plan.reason,
                "zones": [asdict(z) for z in plan.zones],
            },
            "weather": weather.to_prompt_dict(),
        })
        return 0

    if plan.skip or not plan.zones:
        append_entry({
            "action": "skip",
            "reason": plan.reason,
            "weather": weather.to_prompt_dict(),
        })
        return 0

    print(f"[5/5] Executing {len(plan.zones)} zone(s) via Rainbird...")
    async with RainbirdClient(config.rainbird_host, config.rainbird_password) as rb:
        for z in plan.zones:
            per_cycle = z.per_cycle_minutes()
            for i in range(z.cycles):
                print(f"      zone {z.zone} cycle {i+1}/{z.cycles}: {per_cycle} min (irrigating...)")
                await rb.irrigate(z.zone, per_cycle)
                await asyncio.sleep(per_cycle * 60 + 10)
                if i < z.cycles - 1 and z.soak_minutes > 0:
                    print(f"      zone {z.zone} soak: waiting {z.soak_minutes} min")
                    await asyncio.sleep(z.soak_minutes * 60)

    append_entry({
        "action": "watered",
        "reason": plan.reason,
        "zones": [asdict(z) for z in plan.zones],
        "total_minutes": plan.total_minutes(),
        "weather": weather.to_prompt_dict(),
    })
    notify(config.notifications.discord_webhook_url,
           "Sprinkler done",
           f"Watered {plan.total_minutes()} min across {len(plan.zones)} zones.",
           tags="white_check_mark", bot_name=bot_name, avatar_url=avatar_url)
    print("Done.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Daily AI-planned Rainbird irrigation.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan without calling Rainbird irrigation.")
    p.add_argument("--vision-snapshot", action="store_true",
                   help="Capture camera snapshots + vision analysis and save to cache. "
                        "Run via a separate mid-morning systemd timer.")
    args = p.parse_args()
    try:
        if args.vision_snapshot:
            return asyncio.run(_run_vision_snapshot())
        return asyncio.run(_run(args.dry_run))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        append_entry({"action": "error", "error": str(e)})
        try:
            config = Config.load()
            notify(config.notifications.discord_webhook_url,
                   "Sprinkler error",
                   f"Script failed: {e}",
                   priority="high", tags="rotating_light",
                   bot_name=config.bot.name, avatar_url=config.bot.avatar_url)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
