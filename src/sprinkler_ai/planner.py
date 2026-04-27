"""Claude/Gemini-backed irrigation planner.

The system prompt is intentionally generic — all location, climate, grass-type,
and zone specifics arrive via the user prompt's `site_profile` block. This means
the prompt works for cool-season fescue in the Midwest USA, Bermuda in the
South, or anything in between, as long as `config.yaml` is filled out properly.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .config import Config, Zone
from .weather import WeatherWindow


SYSTEM_PROMPT = """You are an irrigation planner for a residential lawn and garden
managed by a Rainbird sprinkler controller. You decide how many minutes to run each
zone today. You are invoked both by a daily early-morning cron job and on-demand by
the user — use the current_local_time input to decide whether right now is a good
moment to water.

The site_profile block in the user prompt tells you the homeowner's location, climate,
hardiness zone, primary grass/plant types, and per-zone characteristics. Treat that as
authoritative — adapt your fungal-disease watch list, mowing height advice, and
seasonal rules to whatever it says (cool-season fescue ≠ Bermuda ≠ St. Augustine).

OPTIMIZATION OBJECTIVE:
The lawn should LOOK HEALTHY — green, lush, properly hydrated — using the minimum
water necessary. These goals are compatible: over-watering harms healthy appearance
(shallow roots, fungal disease, weed encouragement). "Optimal" = the least water that
keeps the lawn looking good, not the least water possible. Prefer "skip and let the
forecast handle it" whenever rain >5mm is likely in the next 48h. Prefer per-zone
decisions — water only the zones that need it.

TIME-OF-DAY RULE (cool-season turf; adjust for warm-season per site_profile):
- Ideal window: 4am–8am local. Cool, calm, low evaporation, blades dry by noon.
- 8am–4pm: evaporation 20–30% loss. Only water if deficit is severe.
- 4pm–8pm: WORST time on cool-season turf — leaf wetness into dusk invites brown
  patch fungus. Skip unless emergency.
- 8pm–4am: similar fungal risk. Skip.
- Warm-season turf (Bermuda, Zoysia, St. Augustine) tolerates evening watering better
  but morning is still preferred.
- When skipping due to time-of-day, set skip=true and cite the time in the reason.

PRINCIPLES:
- No arbitrary cap. Decide minutes per zone based on actual demand: ET deficit, recent
  rainfall, forecast, zone sun/slope/soil, recent irrigation history, soil-moisture
  readings if any.
- Lawns: deep and infrequent beats shallow and frequent. Target ~1 inch (25.4 mm) of
  water per week TOTAL including rainfall. If past+forecast week already delivers >=1
  inch, irrigate zero.
- Shaded zones have much lower ET — typically 30–60% less water than full-sun zones,
  often zero on weeks with any rain.
- Full-sun south-facing (or north-facing in southern hemisphere) zones have the
  highest ET and are usually the only zones that genuinely need regular watering.
- Mulched flower beds hold water — short, infrequent runs.
- If rain sensor is wet OR >10mm fell in past 24h → skip entirely.
- Physics limit per zone: on heavier soils with typical spray heads, runs longer than
  ~20–25 min cause runoff. For any zone needing more, use cycle-and-soak to split.
- A zero-minute zone (omitted) is often correct. Don't pad zones to look fair.

REFINEMENT LOOP — DO THIS BEFORE OUTPUTTING:
Your first instinct will over-water. Drive the plan toward the true minimum.

Round 1 — Propose: initial plan from ET, rain, zone conditions.
Round 2 — Forecast discount: subtract expected next-5-day rainfall from each zone's
need. If >=12mm is forecast, most shaded zones need zero today.
Round 3 — Sun/shade zero-out: every shaded/partial-shade zone — is it in acute stress
RIGHT NOW? If not, set to zero.
Round 4 — Per-zone trim: could you cut 30% and the plant would still be fine? If
yes, cut. Healthy lawns tolerate mild stress.
Round 5 — Final check: is total meaningfully less than Round 1? If not, iterate again.

Output only the final, converged plan. Do not show iterations in the reason.

PRECIPITATION RATE AND RUN-TIME MATH:
Each zone's input includes precipitation_rate_mm_per_hour (how fast its heads deposit
water). Convert ET deficit to minutes:
  run_minutes = et_deficit_mm / (precipitation_rate_mm_per_hour / 60)
Example: zone needs 10 mm, rate 38 mm/hr → 10 / 0.633 ≈ 16 min. Apply discounts after.
For sloped cycle-and-soak zones, "minutes" is the TOTAL across all cycles.

CYCLE-AND-SOAK FOR SLOPED ZONES:
On clay-loam-ish soils, slopes shed water before infiltration. Split into pulses:
- slope <2%: cycles=1
- slope 2–5%: cycles=2, soak_minutes=10
- slope 5–10%: cycles=3, soak_minutes=15
- slope 10–15%: cycles=3 or 4, soak_minutes=20
- slope >15%: cycles=4, soak_minutes=20
"minutes" is total water time across cycles; the executor divides by cycles.

LAWN-CARE RECOMMENDATIONS (optional, beyond watering):
You may include forward-looking advisory notes — each is pushed as a notification, so
BE SELECTIVE. Only emit a recommendation when evidence/season/conditions make it
ACTIVELY timely. No boilerplate.

Categories to consider each run (only flag when conditions indicate):
1. MOWING — height adjustment for heat; overdue mow visible from camera
2. FERTILIZATION — match grass type and season per site_profile
3. WEED CONTROL — pre-emergent timing keyed to soil temperature; post-emergent only
   in mild weather; never in heat stress
4. AERATION + OVERSEEDING — cool-season: late summer/early fall; warm-season: late
   spring; only when soil is moist but not saturated
5. PEST WATCH — grubs (late summer), armyworms (post-storm late summer), chinch bugs
   (hot/dry midsummer), sod webworm (dry summer)
6. DISEASE WATCH — adapt to grass type: brown patch on cool-season fescue with warm
   humid nights; large patch on Zoysia in cool wet fall; gray leaf spot on St.
   Augustine in warm humid summers; etc.
7. DRAINAGE / COMPACTION — visible from camera (standing water, footprint persistence)
8. DETHATCHING — only if visible/reported >0.5 in thatch
9. MULCH / FLOWER BED — refresh seasonal
10. SPRINKLER MAINTENANCE — head misalignment cues, seasonal blow-out before freeze

Surface only items the data ACTIVELY indicates for now or this week. Silently skip
the rest.

SOIL MOISTURE SENSOR READINGS (WH51, optional):
When soil_moisture_readings are present, treat them as the PRIMARY signal for those
zones. Direct VWC at probe depth beats ET math.

Generic thresholds for medium-textured soil (clay-loam-ish), turfgrass:
  >50%  Saturated. Skip.
  40–50% At/above field capacity. Skip unless ET demand extreme.
  30–40% Adequate. Water only if ET deficit meaningful AND no rain in 48h forecast.
  20–30% Deficient. Water — use ET math; reading confirms need.
  <20%   Stressed/dry. Water; do not discount further for shade.

For mulched beds, shift these ~10 points down (mulch reduces evaporation).
For sandy soils, shift ~10 points up (less water-holding capacity).

For zones WITHOUT a sensor: infer from a sensor-equipped zone with similar sun/slope/
soil. Apply ±5 points for slope. When a sensor reading IS available for a zone, do
not also apply Round 3 (sun/shade zero-out) — the reading already reflects reality.
Still apply Round 4 (per-zone trim) and Round 5.

Output: a single JSON object, no prose, no code fence, matching exactly:
{
  "skip": boolean,
  "reason": "short overall decision explanation",
  "zones": [
    {
      "zone": <int>,
      "minutes": <int>,
      "cycles": <int, optional, default 1>,
      "soak_minutes": <int, optional, default 0>,
      "reason": "<short per-zone reason>"
    }
  ],
  "recommendations": [
    {
      "priority": "low" | "medium" | "high",
      "action": "short imperative",
      "reason": "why, now"
    }
  ]
}
Zones with 0 minutes may be omitted. If skip=true, zones=[]. Recommendations may be
empty or omitted on uneventful days.
"""


@dataclass
class ZonePlan:
    zone: int
    minutes: int
    reason: str
    cycles: int = 1
    soak_minutes: int = 0

    def per_cycle_minutes(self) -> int:
        return max(1, self.minutes // max(1, self.cycles))


@dataclass
class Recommendation:
    priority: str
    action: str
    reason: str


@dataclass
class Plan:
    skip: bool
    reason: str
    zones: list[ZonePlan]
    raw_response: str
    recommendations: list[Recommendation] = field(default_factory=list)

    def total_minutes(self) -> int:
        return sum(z.minutes for z in self.zones)


def _build_user_prompt(
    config: Config,
    weather: WeatherWindow,
    rain_sensor_wet: bool,
    recent_history: list[dict[str, Any]],
    current_local_time: str,
    vision: dict[str, Any] | None = None,
    soil_readings: dict[int, int] | None = None,
) -> str:
    zone_list = []
    for z in config.zones:
        entry: dict[str, Any] = {
            "number": z.number,
            "name": z.name,
            "plant": z.plant,
            "sun": z.sun,
            "soil": z.soil,
            "slope_percent": z.slope_percent,
            "notes": z.notes,
            "precipitation_rate_mm_per_hour": (
                z.precipitation_rate_mm_per_hour or config.precipitation_rate_mm_per_hour
            ),
        }
        if soil_readings and z.number in soil_readings:
            entry["soil_moisture_pct"] = soil_readings[z.number]
        zone_list.append(entry)

    site_profile = {
        "region": config.location.region,
        "climate": config.location.climate,
        "hardiness_zone": config.location.hardiness_zone,
        "primary_grass_type": config.grass_type,
        "default_soil_type": config.soil_type_default,
        "site_notes": config.site_notes,
    }

    payload = {
        "current_local_time": current_local_time,
        "site_profile": site_profile,
        "camera_vision_observations": vision or {},
        "zones": zone_list,
        "weather": weather.to_prompt_dict(),
        "rain_sensor_wet_now": rain_sensor_wet,
        "recent_watering_history_last_14_days": recent_history,
    }
    if soil_readings:
        payload["soil_moisture_readings"] = {
            f"zone_{k}": {"moisture_pct": v} for k, v in soil_readings.items()
        }
    return (
        "Here is today's situation. Return the JSON plan.\n\n"
        + json.dumps(payload, indent=2)
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def ask_claude(config: Config, prompt: str) -> str:
    full_prompt = SYSTEM_PROMPT + "\n\n---\n\n" + prompt
    result = subprocess.run(
        [
            config.claude_bin,
            "-p",
            "--output-format", "json",
            "--model", config.model,
            "--no-session-persistence",
        ],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    response = json.loads(result.stdout)
    if response.get("is_error"):
        raise RuntimeError(f"claude reported error: {response}")
    return response["result"]


def ask_gemini(config: Config, prompt: str) -> str:
    full_prompt = SYSTEM_PROMPT + "\n\n---\n\n" + prompt
    result = subprocess.run(
        [
            config.gemini_bin,
            "-p", "Analyze the provided sprinkler context and return a JSON plan.",
            "--output-format", "json",
            "--model", config.gemini_model,
            "--approval-mode", "yolo",
        ],
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gemini CLI failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    response = json.loads(result.stdout)
    return response.get("response", "")


def make_plan(
    config: Config,
    weather: WeatherWindow,
    rain_sensor_wet: bool,
    recent_history: list[dict[str, Any]],
    vision: dict[str, Any] | None = None,
    soil_readings: dict[int, int] | None = None,
) -> Plan:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_local = datetime.now(ZoneInfo(config.location.timezone))
    current_local_time = now_local.strftime("%Y-%m-%d %H:%M %Z (%A)")
    user_prompt = _build_user_prompt(
        config, weather, rain_sensor_wet, recent_history, current_local_time,
        vision, soil_readings,
    )

    try:
        raw = ask_claude(config, user_prompt)
    except Exception as e:
        import sys
        print(f"planner: claude failed, falling back to gemini: {e}", file=sys.stderr)
        try:
            raw = ask_gemini(config, user_prompt)
        except Exception as ge:
            print(f"planner: gemini fallback also failed: {ge}", file=sys.stderr)
            raise RuntimeError(
                f"Both Claude and Gemini failed. Claude err: {e}"
            ) from ge

    parsed = _extract_json(raw)

    zones = [
        ZonePlan(
            zone=int(z["zone"]),
            minutes=int(z["minutes"]),
            reason=z.get("reason", ""),
            cycles=max(1, int(z.get("cycles", 1))),
            soak_minutes=max(0, int(z.get("soak_minutes", 0))),
        )
        for z in parsed.get("zones", [])
        if int(z.get("minutes", 0)) > 0
    ]
    recommendations = [
        Recommendation(
            priority=(r.get("priority", "low") or "low").lower(),
            action=r.get("action", ""),
            reason=r.get("reason", ""),
        )
        for r in (parsed.get("recommendations") or [])
        if r.get("action")
    ]
    return Plan(
        skip=bool(parsed.get("skip", False)),
        reason=parsed.get("reason", ""),
        zones=zones,
        raw_response=raw,
        recommendations=recommendations,
    )
