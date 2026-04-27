"""Interactive setup — writes config.yaml and .env from scratch.

Run via `sprinkler-ai-init`. Idempotent: refuses to overwrite existing files unless
you pass --force. Prints a summary of secret-files created so you remember to never
commit them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── tiny prompt helpers ──────────────────────────────────────────────────────

def _ask(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        v = input(f"  {label}{suffix}: ").strip()
        if not v and default:
            return default
        if not v and required:
            print("    (required — please enter a value)")
            continue
        return v


def _ask_yn(label: str, default_yes: bool = False) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        v = input(f"  {label} {suffix}: ").strip().lower()
        if not v:
            return default_yes
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False


def _ask_float(label: str, default: float | None = None, required: bool = False) -> float | None:
    while True:
        s = _ask(label, str(default) if default is not None else "",
                 required=required and default is None)
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            print("    (need a number — try again)")


def _ask_int(label: str, default: int | None = None, required: bool = False) -> int | None:
    while True:
        s = _ask(label, str(default) if default is not None else "",
                 required=required and default is None)
        if not s:
            return default
        try:
            return int(s)
        except ValueError:
            print("    (need a whole number — try again)")


def _section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 70 - len(title)))


# ── content collectors ──────────────────────────────────────────────────────

def collect_location() -> dict:
    _section("Location")
    print("  Used for the weather forecast and to tell the AI planner about your climate.")
    print("  Tip: get lat/lon from Google Maps (right-click → coordinates), or use a city")
    print("  centroid — precision to 0.01 (~1 km) is plenty.")
    return {
        "latitude":  _ask_float("Latitude (e.g. 38.74)", required=True),
        "longitude": _ask_float("Longitude (e.g. -90.76)", required=True),
        "timezone":  _ask("Timezone (e.g. America/Chicago, Europe/London)",
                          default="America/Chicago", required=True),
        "region":    _ask("Region label (free-form, e.g. 'Midwest USA', 'South-east England')"),
        "climate":   _ask("Climate descriptor (e.g. 'humid continental', 'Mediterranean')"),
        "hardiness_zone": _ask("USDA / RHS hardiness zone (e.g. '6b', 'H4')"),
    }


def collect_site_profile() -> dict:
    _section("Site profile")
    print("  Tells the AI what to expect — disease watch list and seasonal advice key off this.")
    return {
        "grass_type": _ask(
            "Primary grass type (e.g. 'cool-season fescue', 'Bermuda', 'St. Augustine')"),
        "soil_type_default": _ask(
            "Default soil type (e.g. 'clay loam', 'sandy loam')", default="clay loam"),
        "site_notes": _ask(
            "Site notes — free-form, fed to the AI as background. House orientation, slope,"
            " neighbor shadows, anything noteworthy. Leave blank to skip"),
    }


def collect_zones() -> list[dict]:
    _section("Sprinkler zones")
    print("  One entry per Rainbird zone. Numbers should match the controller.")
    zones: list[dict] = []
    n = _ask_int("How many zones do you have?", default=4, required=True)
    for i in range(1, (n or 0) + 1):
        print(f"\n  Zone {i}:")
        z: dict = {
            "number": i,
            "name":  _ask(f"  Name (e.g. 'Front lawn')", default=f"Zone {i}", required=True),
            "plant": _ask(f"  Plant type (e.g. 'fescue', 'flower bed')"),
            "sun":   _ask(f"  Sun exposure (e.g. 'full sun', 'partial shade')"),
            "soil":  _ask(f"  Soil ('clay loam', 'mulched', 'amended', etc.) — blank = use default"),
        }
        slope = _ask_float(f"  Slope % (0 if flat, ~5 mild, 10+ steep)", default=0.0)
        if slope:
            z["slope_percent"] = slope
        notes = _ask(f"  Per-zone notes (optional)")
        if notes:
            z["notes"] = notes
        rate = _ask_float(f"  Precipitation rate mm/hr (blank = use global default)")
        if rate:
            z["precipitation_rate_mm_per_hour"] = rate
        ch = _ask_int(f"  Soil-sensor channel 1-8 (0 or blank if no sensor)")
        if ch:
            z["soil_sensor_channel"] = ch
        zones.append(z)
    return zones


def collect_secrets() -> dict[str, str]:
    _section("Rainbird controller (required)")
    print("  IP and password for the LNK Wi-Fi adapter. Find via your router's DHCP")
    print("  client list, or in the Rainbird app under Settings → System Status.")
    secrets = {
        "RAINBIRD_HOST": _ask("Rainbird IP", required=True),
        "RAINBIRD_PASSWORD": _ask("Rainbird password", required=True),
    }

    _section("Claude / Gemini CLIs")
    print("  The planner shells out to your local `claude` (and optionally `gemini`) CLIs.")
    print("  Both must be installed and authenticated (run them once interactively first).")
    secrets["CLAUDE_BIN"] = _ask("Path to `claude` binary", default="claude")
    secrets["GEMINI_BIN"] = _ask("Path to `gemini` binary (optional, fallback)", default="gemini")

    _section("Optional services")
    if _ask_yn("Add Ecowitt soil-moisture gateway (GW1200 + WH51 probes)?"):
        secrets["ECOWITT_HOST"] = _ask("Ecowitt gateway local IP", required=True)

    if _ask_yn("Add Discord webhook for daily-plan notifications?"):
        secrets["DISCORD_WEBHOOK_URL"] = _ask(
            "Discord webhook URL (Server → Integrations → Webhooks)", required=True)

    if _ask_yn("Add Discord bot for !diagnose photo replies?"):
        secrets["DISCORD_BOT_TOKEN"] = _ask("Discord bot token", required=True)
        ch = _ask("Restrict bot to a single channel ID (blank = listen everywhere)")
        if ch:
            secrets["DISCORD_DIAGNOSE_CHANNEL_ID"] = ch

    if _ask_yn("Add Nest cameras for lawn-vision analysis (advanced — see docs/nest-setup.md)?"):
        secrets["NEST_PROJECT_ID"]    = _ask("NEST_PROJECT_ID", required=True)
        secrets["NEST_CLIENT_ID"]     = _ask("NEST_CLIENT_ID", required=True)
        secrets["NEST_CLIENT_SECRET"] = _ask("NEST_CLIENT_SECRET", required=True)
        secrets["NEST_REFRESH_TOKEN"] = _ask("NEST_REFRESH_TOKEN", required=True)

    return secrets


def collect_camera_targets(secrets: dict[str, str], zones: list[dict]) -> tuple[list[dict], dict]:
    if not secrets.get("NEST_PROJECT_ID"):
        return [], {}
    _section("Camera → zone mapping")
    print("  For each Nest camera you want to use, give a substring of its 'Room' name")
    print("  (as shown in the Google Home app) and which zones it covers.")
    targets: list[dict] = []
    descriptions: dict[str, str] = {}
    while _ask_yn("Add a camera mapping?"):
        room = _ask("  Room-name keyword (e.g. 'backyard', 'front')", required=True).lower()
        label = _ask("  Short label (used in vision prompts, e.g. 'backyard-zones-4-5')",
                     default=f"{room}-cam", required=True)
        zones_csv = _ask("  Zones it covers (comma-separated, e.g. '4,5')")
        covers = [int(s) for s in zones_csv.split(",") if s.strip().isdigit()]
        targets.append({"room_keyword": room, "label": label, "covers_zones": covers})
        desc = _ask("  Property-boundary description for this camera (blank to skip)")
        if desc:
            descriptions[label] = desc
    return targets, descriptions


def collect_bot() -> dict:
    _section("Bot persona (Discord)")
    print("  Optional — sets the username/avatar for outgoing webhook + bot messages.")
    print("  Default name 'Sahadev' is a wink at the weather-watching character in")
    print("  the 2004 film Swades. Pick anything.")
    name = _ask("Bot display name", default="Sahadev")
    avatar = _ask("Avatar image URL (must be publicly fetchable; blank to skip)")
    return {"name": name, "avatar_url": avatar}


# ── writers ──────────────────────────────────────────────────────────────────

def write_config(path: Path, location: dict, profile: dict, zones: list[dict],
                 camera_targets: list[dict], camera_descriptions: dict,
                 bot: dict, secrets: dict[str, str]) -> None:
    config: dict = {
        "location": location,
        "model": "sonnet",
        "gemini_model": "pro",
        "precipitation_rate_mm_per_hour": 38,
        "grass_type": profile["grass_type"],
        "soil_type_default": profile["soil_type_default"],
        "site_notes": profile["site_notes"],
        "bot": bot,
        "notifications": {
            "discord_webhook_url": secrets.get("DISCORD_WEBHOOK_URL", ""),
        },
        "zones": zones,
    }
    if camera_targets:
        config["camera_targets"] = camera_targets
    if camera_descriptions:
        config["camera_descriptions"] = camera_descriptions
    path.write_text(
        "# sprinkler-ai config — auto-generated by `sprinkler-ai-init`. Edit freely.\n"
        "# This file is gitignored. Don't commit it (zone descriptions can be PII).\n\n"
        + yaml.safe_dump(config, sort_keys=False)
    )


def write_env(path: Path, secrets: dict[str, str]) -> None:
    lines = [
        "# sprinkler-ai secrets — auto-generated by `sprinkler-ai-init`.",
        "# This file is gitignored. NEVER commit it.",
        "",
    ]
    keys_in_order = [
        "RAINBIRD_HOST", "RAINBIRD_PASSWORD", "CLAUDE_BIN", "GEMINI_BIN",
        "ECOWITT_HOST", "DISCORD_BOT_TOKEN", "DISCORD_DIAGNOSE_CHANNEL_ID",
        "NEST_PROJECT_ID", "NEST_CLIENT_ID", "NEST_CLIENT_SECRET", "NEST_REFRESH_TOKEN",
    ]
    for k in keys_in_order:
        if k in secrets:
            lines.append(f"{k}={secrets[k]}")
    path.write_text("\n".join(lines) + "\n")


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Interactive setup for sprinkler-ai.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing config.yaml / .env")
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config.yaml")
    p.add_argument("--env", type=Path, default=REPO_ROOT / ".env")
    args = p.parse_args()

    print("\n  sprinkler-ai — first-run setup")
    print("  ──────────────────────────────")
    print("  This walks you through location, zones, controller, and optional services.")
    print("  Press Enter to accept defaults shown in [brackets]. Ctrl-C to abort.\n")

    if args.config.exists() and not args.force:
        print(f"  ✗ {args.config} already exists. Re-run with --force to overwrite.")
        return 1
    if args.env.exists() and not args.force:
        print(f"  ✗ {args.env} already exists. Re-run with --force to overwrite.")
        return 1

    location = collect_location()
    profile = collect_site_profile()
    zones = collect_zones()
    bot = collect_bot()
    secrets = collect_secrets()
    camera_targets, camera_descriptions = collect_camera_targets(secrets, zones)

    write_config(args.config, location, profile, zones, camera_targets,
                 camera_descriptions, bot, secrets)
    write_env(args.env, secrets)

    print(f"\n  ✓ Wrote {args.config}")
    print(f"  ✓ Wrote {args.env}  (secrets — gitignored, never commit)")
    print("\n  Next steps:")
    print("    1. Test the plan without irrigating:  sprinkler-ai --dry-run")
    print("    2. Once it looks good, install the systemd timers — see README.md.")
    print("    3. (Optional) Start the Discord bot:  sprinkler-ai-bot")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
