"""Discord webhook notifier — daily plan, errors, rain-skip, recommendations.

Bot username and avatar come from `config.bot` so each user gets their own persona.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

import httpx


_TAGS_TO_EMOJI: dict[str, str] = {
    "droplet": "💧",
    "umbrella": "🌂",
    "seedling": "🌱",
    "white_check_mark": "✅",
    "rotating_light": "🚨",
}

_PRIORITY_TO_COLOR: dict[str, int] = {
    "high":    0xE74C3C,
    "default": 0x3498DB,
    "low":     0x95A5A6,
    "min":     0x95A5A6,
}


def _post(webhook_url: str, payload: dict, bot_name: str = "", avatar_url: str = "") -> None:
    decorations: dict[str, Any] = {}
    if bot_name:
        decorations["username"] = bot_name
    if avatar_url:
        decorations["avatar_url"] = avatar_url
    try:
        httpx.post(webhook_url, json={**decorations, **payload}, timeout=10.0)
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)


def send(webhook_url: str, title: str, message: str, priority: str = "default",
         tags: str = "droplet", bot_name: str = "", avatar_url: str = "") -> None:
    """Simple fire-and-forget embed — error / done / rain-sensor-skip."""
    if not webhook_url:
        return
    emoji = _TAGS_TO_EMOJI.get(tags, "")
    full_title = f"{emoji} {title}".strip() if emoji else title
    color = _PRIORITY_TO_COLOR.get(priority, _PRIORITY_TO_COLOR["default"])
    _post(webhook_url, {
        "embeds": [{
            "title": full_title,
            "description": message,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }, bot_name=bot_name, avatar_url=avatar_url)


def send_plan(
    webhook_url: str,
    plan: object,
    zone_names: dict[int, str],
    soil_readings: dict[int, int] | None = None,
    dry_run: bool = False,
    bot_name: str = "",
    avatar_url: str = "",
) -> None:
    """Rich daily plan embed with per-zone fields, soil footer, and recommendations."""
    if not webhook_url:
        return

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%a %b %-d")
    embeds = []

    if plan.skip or not plan.zones:  # type: ignore[union-attr]
        embeds.append({
            "title": f"🌂  Skipping Today · {date_str}",
            "description": plan.reason,  # type: ignore[union-attr]
            "color": 0x95A5A6,
            "footer": _soil_footer(soil_readings),
            "timestamp": now.isoformat(),
        })
    else:
        dry_tag = "  *(dry-run)*" if dry_run else ""
        fields = []
        for z in plan.zones:  # type: ignore[union-attr]
            name = zone_names.get(z.zone, f"Zone {z.zone}")
            if z.cycles > 1:
                timing = (
                    f"**{z.minutes} min total** — "
                    f"{z.cycles} × {z.per_cycle_minutes()} min + {z.soak_minutes} min soak"
                )
            else:
                timing = f"**{z.minutes} min**"
            value = f"{timing}\n{z.reason[:220]}" if z.reason else timing
            fields.append({
                "name": f"Zone {z.zone}  ·  {name}",
                "value": value,
                "inline": False,
            })

        soil_line = ""
        if soil_readings:
            soil_line = "\n**Soil moisture** — " + "  ·  ".join(
                f"Zone {k}: **{v}%**" for k, v in sorted(soil_readings.items())
            )

        embeds.append({
            "title": f"💧  Sprinkler Plan · {date_str}{dry_tag}",
            "description": plan.reason,  # type: ignore[union-attr]
            "color": 0x2ECC71 if not dry_run else 0xF39C12,
            "fields": fields,
            "footer": {
                "text": (
                    f"Total: {plan.total_minutes()} min across {len(plan.zones)} zone(s)"  # type: ignore[union-attr]
                    + ("  ·  dry-run, not executed" if dry_run else "")
                )
            },
            "timestamp": now.isoformat(),
        })
        if soil_line:
            embeds[0]["fields"].append({  # type: ignore[index]
                "name": "​",
                "value": soil_line,
                "inline": False,
            })

    if plan.recommendations:  # type: ignore[union-attr]
        _pri_color = {"high": 0xE74C3C, "medium": 0xF39C12, "low": 0x3498DB}
        _pri_dot   = {"high": "🔴", "medium": "🟡", "low": "🔵"}
        rec_fields = [
            {
                "name": f"{_pri_dot.get(r.priority, '•')}  {r.action}",
                "value": r.reason[:300],
                "inline": False,
            }
            for r in plan.recommendations  # type: ignore[union-attr]
        ]
        embeds.append({
            "title": "🌱  Lawn Care",
            "color": _pri_color.get(plan.recommendations[0].priority, 0x3498DB),  # type: ignore[union-attr]
            "fields": rec_fields,
            "timestamp": now.isoformat(),
        })

    _post(webhook_url, {"embeds": embeds}, bot_name=bot_name, avatar_url=avatar_url)


def _soil_footer(soil_readings: dict[int, int] | None) -> dict | None:
    if not soil_readings:
        return None
    parts = "  ·  ".join(f"Zone {k}: {v}%" for k, v in sorted(soil_readings.items()))
    return {"text": f"Soil moisture  ·  {parts}"}
