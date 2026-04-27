"""Ecowitt GW1200/GW2000 local-API client for WH51 soil-moisture probes.

Probes report volumetric water content (VWC %) at ~4–6 inch depth. The planner
uses these as the primary signal for the zones they cover, and infers nearby
zones by similarity. Optional — the system runs fine without sensors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


@dataclass
class SoilReading:
    channel: int
    moisture_pct: int
    battery_v: float | None = None


@dataclass
class EcowittSnapshot:
    soil: list[SoilReading]
    captured_at: str

    def by_channel(self) -> dict[int, SoilReading]:
        return {r.channel: r for r in self.soil}


async def fetch_snapshot(host: str, timeout: float = 10.0) -> EcowittSnapshot:
    """Query GW1200 local API (port 80) and return current WH51 readings."""
    url = f"http://{host}/get_livedata_info"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    soil: list[SoilReading] = []
    for entry in data.get("ch_soil", []):
        try:
            channel = int(entry["channel"])
            raw_humidity = str(entry.get("humidity", "0")).rstrip("%")
            moisture_pct = int(raw_humidity)
            try:
                battery_v = float(entry["voltage"])
            except (KeyError, TypeError, ValueError):
                battery_v = None
            soil.append(SoilReading(channel=channel, moisture_pct=moisture_pct, battery_v=battery_v))
        except (KeyError, ValueError):
            continue

    return EcowittSnapshot(
        soil=soil,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )
