"""Nest camera integration — OAuth + WebRTC frame grab + Claude Vision analysis.

Requires env vars: NEST_PROJECT_ID, NEST_CLIENT_ID, NEST_CLIENT_SECRET, NEST_REFRESH_TOKEN.
Battery Nest Cams don't support RTSP — only WebRTC live streams. We open a stream,
grab one frame, decode to JPEG, then stop.

Optional dependency. Install with `pip install -e ".[vision]"` if you want this.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class NestDevice:
    name: str
    device_type: str
    room: str
    traits: list[str]


class NestClient:
    def __init__(self, project_id: str, client_id: str, client_secret: str,
                 refresh_token: str):
        self.project_id = project_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: str | None = None

    @classmethod
    def from_env(cls) -> "NestClient":
        return cls(
            project_id=os.environ["NEST_PROJECT_ID"],
            client_id=os.environ["NEST_CLIENT_ID"],
            client_secret=os.environ["NEST_CLIENT_SECRET"],
            refresh_token=os.environ["NEST_REFRESH_TOKEN"],
        )

    async def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(OAUTH_TOKEN_URL, data={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            })
            r.raise_for_status()
            self._access_token = r.json()["access_token"]
        return self._access_token

    async def list_devices(self) -> list[NestDevice]:
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(
                f"{SDM_BASE}/enterprises/{self.project_id}/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()
        devices: list[NestDevice] = []
        for d in data.get("devices", []):
            parent = (d.get("parentRelations") or [{}])[0]
            devices.append(NestDevice(
                name=d["name"],
                device_type=d.get("type", ""),
                room=parent.get("displayName", ""),
                traits=list((d.get("traits") or {}).keys()),
            ))
        return devices

    async def _execute(self, device_name: str, command: str,
                       params: dict[str, Any]) -> dict[str, Any]:
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{SDM_BASE}/{device_name}:executeCommand",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"command": command, "params": params},
            )
            r.raise_for_status()
            return r.json().get("results", {})

    async def snapshot(self, device_name: str, timeout_s: float = 15.0) -> bytes:
        """Grab one JPEG frame from the camera via WebRTC. Returns JPEG bytes."""
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from google_nest_sdm.webrtc_util import (
            _add_foundation_to_candidates,
            fix_sdp_answer,
        )

        pc = RTCPeerConnection()
        pc.addTransceiver("audio", direction="recvonly")
        pc.addTransceiver("video", direction="recvonly")
        pc.createDataChannel("nest-unused")  # forces 'application' m-line

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        result = await self._execute(
            device_name,
            "sdm.devices.commands.CameraLiveStream.GenerateWebRtcStream",
            {"offerSdp": pc.localDescription.sdp},
        )
        answer_sdp = result["answerSdp"]
        media_session_id = result.get("mediaSessionId")

        video_track = None

        async def on_track_handler(t):
            nonlocal video_track
            if t.kind == "video":
                video_track = t

        pc.on("track", lambda t: asyncio.create_task(on_track_handler(t)))

        answer_sdp = fix_sdp_answer(pc.localDescription.sdp, answer_sdp)
        answer_sdp = _add_foundation_to_candidates(answer_sdp)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

        try:
            for _ in range(int(timeout_s)):
                if video_track is not None:
                    break
                await asyncio.sleep(1)
            if video_track is None:
                raise TimeoutError("video track never arrived")
            frame = await asyncio.wait_for(video_track.recv(), timeout=timeout_s)
            pil_img = frame.to_image()
            buf = BytesIO()
            pil_img.save(buf, format="JPEG", quality=85)
            jpeg = buf.getvalue()
        finally:
            if media_session_id:
                try:
                    await self._execute(
                        device_name,
                        "sdm.devices.commands.CameraLiveStream.StopWebRtcStream",
                        {"mediaSessionId": media_session_id},
                    )
                except Exception:
                    pass
            await pc.close()

        return jpeg


VISION_SYSTEM_PROMPT = """You are analyzing snapshots from home security cameras to
assess lawn and garden health. The cameras capture the lawn directly and incidentally.

PROPERTY BOUNDARY (very important):
You are ONLY assessing the homeowner's property. The images will likely show
neighboring houses, driveways, and lawns in the same frame. You MUST ignore those
entirely — do not comment on them, do not include them in overall_health, do not
generate observations about them, do not use them as a reference baseline. If a
patch of grass, a tree, or any feature you're about to comment on is on a neighbor's
lot, skip it silently.

Anchor the boundary using whatever the homeowner provides in the image's per-frame
description (patio, deck, fire pit, driveway, sidewalk strip, fence line). Sprinkler
heads visible in the lawn typically belong to this property. If you are uncertain
whether a region is on the homeowner's lot, assume it is NOT and skip it.

Some zones may be invisible to any camera (the homeowner will say so in the
zone_assumptions block). Do not flag anything about invisible zones.

Context you receive with each request:
- Time of day and weather conditions at capture time (lighting and wetness hints)
- Which zone(s) are visible in each image
- What the sprinkler planner currently assumes about those zones

Your job: provide concise, actionable observations in JSON. Prioritize *useful* signal
over description. Specifically flag:
- Visible drought stress (graying, browning, blade curling, thinning)
- Over-watering signs (standing water, yellowing from saturation, fungus spots)
- Broken-sprinkler signs (unusually dry or wet patch inconsistent with surrounding)
- Weeds obvious from above (crabgrass patches, dandelions, etc.)
- Mow-needed (visibly long)
- Anything physically blocking assessment (person, vehicle, furniture covering the
  lawn, heavy dew or glare, night-time darkness, snow cover — output view_usable=false)

Discount known non-issues:
- Shadows from buildings/trees (geometric, not health)
- Normal patio furniture or static objects
- Wetness right after a rain or recent watering (user will tell you via context)
- Darker color in morning shade vs afternoon sun (lighting, not stress)

Output a single JSON object, no prose, no code fence:
{
  "view_usable": true | false,
  "view_block_reason": "explain only if view_usable=false",
  "overall_health": "healthy" | "minor_issues" | "stress" | "unknown",
  "observations": [
    {"zone_hint": "<which zone or area>", "note": "<specific observation>",
     "severity": "info"|"watch"|"act"}
  ],
  "recommendations_for_irrigation_plan": [
    "<free-form note the sprinkler planner should consider today>"
  ]
}
"""


def analyze_images(claude_bin: str, images: dict[str, bytes],
                   weather_summary: str, time_context: str,
                   zone_assumptions: str,
                   camera_descriptions: dict[str, str] | None = None,
                   invisible_zones_note: str = "",
                   model: str = "sonnet",
                   gemini_bin: str = "gemini",
                   gemini_model: str = "pro") -> dict:
    """Pipe snapshots to `claude -p` (or `gemini -p`) for lawn-health analysis.

    `images` maps a short label to JPEG bytes. `camera_descriptions` maps the same
    labels to user-authored property-boundary anchors. Returns parsed JSON, or {} on
    failure (never raises).
    """
    if not images:
        return {}
    camera_descriptions = camera_descriptions or {}
    with tempfile.TemporaryDirectory(prefix="sprinkler-vision-") as tmpdir:
        tmp_paths: dict[str, Path] = {}
        for label, data in images.items():
            p = Path(tmpdir) / f"{label}.jpg"
            p.write_bytes(data)
            tmp_paths[label] = p

        image_blocks = []
        for label, path in tmp_paths.items():
            desc = camera_descriptions.get(label) or camera_descriptions.get(label.split("-")[0], "")
            if desc:
                image_blocks.append(
                    f"- {label}: @{path}\n  HOMEOWNER'S PROPERTY DESCRIPTION FOR THIS FRAME:\n    {desc.replace(chr(10), chr(10) + '    ')}"
                )
            else:
                image_blocks.append(f"- {label}: @{path}")
        image_lines = "\n".join(image_blocks)

        invisible_block = (
            f"\nINVISIBLE ZONES (no camera coverage): {invisible_zones_note}\n"
            if invisible_zones_note else ""
        )

        user_prompt = f"""{VISION_SYSTEM_PROMPT}

Capture time: {time_context}
Weather at capture: {weather_summary}

Zone assumptions (what the sprinkler planner currently believes):
{zone_assumptions}
{invisible_block}
Images (each is labeled; homeowner-authored property descriptions are authoritative
for determining the property boundary):
{image_lines}

Analyze the lawn on the homeowner's property in each image and return the JSON."""

        import sys

        def try_claude() -> str:
            result = subprocess.run(
                [claude_bin, "-p", "--output-format", "json", "--model", model,
                 "--no-session-persistence"],
                input=user_prompt, capture_output=True, text=True, timeout=180,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr.strip()}")
            resp = json.loads(result.stdout)
            return resp.get("result", "").strip()

        def try_gemini() -> str:
            result = subprocess.run(
                [gemini_bin, "-p", "Analyze the lawn on the homeowner's property in each image and return JSON.",
                 "--output-format", "json", "--model", gemini_model, "--approval-mode", "yolo"],
                input=user_prompt, capture_output=True, text=True, timeout=180,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                raise RuntimeError(f"gemini CLI exit {result.returncode}: {result.stderr.strip()}")
            resp = json.loads(result.stdout)
            return resp.get("response", "").strip()

        try:
            raw = try_claude()
        except Exception as e:
            print(f"vision: claude failed, falling back to gemini: {e}", file=sys.stderr)
            try:
                raw = try_gemini()
            except Exception as ge:
                print(f"vision: gemini fallback also failed: {ge}", file=sys.stderr)
                return {}

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"vision: JSON parse failed: {e}; "
                  f"first 300 chars of result: {raw[:300]!r}", file=sys.stderr)
            return {}
