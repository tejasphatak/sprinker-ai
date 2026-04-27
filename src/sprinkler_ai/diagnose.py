"""Single-photo lawn diagnostic — Claude vision with a turf-health prompt.

Used by the Discord bot when the homeowner posts a close-up photo with `!diagnose`.
The system prompt is generic; the per-site context (grass type, climate, region) is
injected from config.yaml so the model adapts its disease/pest watch list.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


DIAGNOSE_SYSTEM_PROMPT = """You are a turf-health diagnostician looking at a close-up
photo of a residential lawn or landscaping bed. The homeowner will provide their
location, climate, and primary grass/plant types in the SITE block below — adapt your
disease/pest watch list to whatever they specify (cool-season fescue and Bermuda have
different problems; humid Southeast and arid Southwest have different problems).

Generic things to look for on most home lawns:
- Fungal disease — pattern depends on grass + climate. Cool-season turf in humid
  summers: brown patch, dollar spot, pythium, red thread, leaf spot. Warm-season turf:
  large patch (Zoysia), gray leaf spot (St. Augustine), spring dead spot (Bermuda).
- Drought stress — blue-gray tint, blade curling/folding, footprints staying visible.
- Over-watering — yellowing from base, spongy feel hints, moss, fungus spots.
- Insect damage — irregular brown patches that pull up easily (grubs at roots),
  yellowing strips near concrete (chinch bugs in southern lawns), chewed blades
  (armyworm/sod webworm), sawdust-like piles (mole crickets in southern turf).
- Thatch — thick brown layer at soil line, >0.5 inch is a problem.
- Compaction — thin turf in high-traffic areas, water pooling, weeds thriving
  (dandelion, plantain, knotweed love compacted soil).
- Weeds — crabgrass (light-green wide-bladed clumps), dandelions, clover, nutsedge
  (triangular stems, taller than surrounding lawn), violets, ground ivy, and
  region-specific invaders the homeowner mentions.
- Nutrient deficiency — yellowing (N), purpling (P, often cold soil), pale uniformly
  (low Fe).
- Mechanical — mower scalping, dog urine spots (dark-green ring around dead center),
  dull-blade tearing (frayed blade tips).

Use the photo as primary evidence. If the homeowner added a caption, incorporate it
but don't let it override what you see. If the photo is not a lawn (indoor, sky,
person, etc.) or is too blurry/dark to assess, set view_usable=false and explain.

Output a single JSON object, no prose, no code fence:
{
  "view_usable": true | false,
  "view_block_reason": "explain only if view_usable=false",
  "headline": "one short sentence — the main takeaway the homeowner should read first",
  "grass_condition": "healthy" | "minor_issues" | "stressed" | "severe" | "unknown",
  "findings": [
    {"issue": "<what you see>",
     "evidence": "<what in the photo made you conclude this>",
     "severity": "info" | "watch" | "act"}
  ],
  "actions": [
    {"action": "<specific next step>",
     "priority": "high" | "medium" | "low",
     "when": "now" | "this week" | "this season"}
  ],
  "uncertainty": "anything you couldn't tell from the photo alone"
}

Keep findings tight — 1 to 4 items, only what's actually visible. Same for actions:
1 to 4 concrete steps, not a lecture. The homeowner has AI-driven irrigation, so
watering-amount advice should be framed as 'the scheduler should consider X' rather
than 'you should water more'."""


def diagnose_photo(claude_bin: str, jpeg_bytes: bytes, caption: str = "",
                   model: str = "sonnet",
                   gemini_bin: str = "gemini",
                   gemini_model: str = "pro",
                   site_profile: dict | None = None) -> dict:
    """Run Claude vision on a single close-up lawn photo. Returns parsed JSON.

    Falls back to Gemini if the Claude CLI call fails. Returns {} on total failure.
    site_profile (optional) injects region/climate/grass-type — tailors the diagnosis.
    """
    with tempfile.TemporaryDirectory(prefix="sprinkler-diagnose-") as tmpdir:
        img_path = Path(tmpdir) / "photo.jpg"
        img_path.write_bytes(jpeg_bytes)

        site_block = ""
        if site_profile:
            parts = []
            for k in ("region", "climate", "hardiness_zone", "primary_grass_type",
                      "default_soil_type"):
                v = site_profile.get(k)
                if v:
                    parts.append(f"  {k}: {v}")
            if parts:
                site_block = "SITE:\n" + "\n".join(parts) + "\n"

        caption_block = f"\nHomeowner's caption: {caption.strip()}\n" if caption.strip() else ""
        user_prompt = f"""{DIAGNOSE_SYSTEM_PROMPT}

{site_block}
Photo to diagnose: @{img_path}
{caption_block}
Analyze the photo and return the JSON described above."""

        def try_claude() -> str:
            result = subprocess.run(
                [claude_bin, "-p", "--output-format", "json", "--model", model,
                 "--no-session-persistence"],
                input=user_prompt, capture_output=True, text=True, timeout=180,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr.strip()}")
            return json.loads(result.stdout).get("result", "").strip()

        def try_gemini() -> str:
            result = subprocess.run(
                [gemini_bin, "-p", "Diagnose this lawn photo and return JSON.",
                 "--output-format", "json", "--model", gemini_model, "--approval-mode", "yolo"],
                input=user_prompt, capture_output=True, text=True, timeout=180,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                raise RuntimeError(f"gemini CLI exit {result.returncode}: {result.stderr.strip()}")
            return json.loads(result.stdout).get("response", "").strip()

        try:
            raw = try_claude()
        except Exception as e:
            print(f"diagnose: claude failed, falling back to gemini: {e}", file=sys.stderr)
            try:
                raw = try_gemini()
            except Exception as ge:
                print(f"diagnose: gemini fallback also failed: {ge}", file=sys.stderr)
                return {}

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"diagnose: JSON parse failed: {e}; "
                  f"first 300 chars: {raw[:300]!r}", file=sys.stderr)
            return {}
