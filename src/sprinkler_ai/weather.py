from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import httpx


@dataclass
class DailyWeather:
    date: str
    precipitation_mm: float
    precipitation_probability_max: int
    et0_mm: float
    temp_max_c: float
    temp_min_c: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WeatherWindow:
    past: list[DailyWeather]
    forecast: list[DailyWeather]

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "past_days_actual": [d.to_dict() for d in self.past],
            "forecast_days": [d.to_dict() for d in self.forecast],
            "past_precip_total_mm": round(sum(d.precipitation_mm for d in self.past), 2),
            "forecast_precip_total_mm": round(sum(d.precipitation_mm for d in self.forecast), 2),
            "past_et0_total_mm": round(sum(d.et0_mm for d in self.past), 2),
        }


def fetch_weather(latitude: float, longitude: float, timezone: str,
                  past_days: int = 3, forecast_days: int = 5) -> WeatherWindow:
    """Open-Meteo forecast API — no auth needed, free for personal use."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "past_days": past_days,
        "forecast_days": forecast_days,
        "daily": ",".join([
            "precipitation_sum",
            "precipitation_probability_max",
            "et0_fao_evapotranspiration",
            "temperature_2m_max",
            "temperature_2m_min",
        ]),
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    daily = data["daily"]
    days: list[DailyWeather] = []
    for i, date in enumerate(daily["time"]):
        days.append(DailyWeather(
            date=date,
            precipitation_mm=float(daily["precipitation_sum"][i] or 0),
            precipitation_probability_max=int(daily["precipitation_probability_max"][i] or 0),
            et0_mm=float(daily["et0_fao_evapotranspiration"][i] or 0),
            temp_max_c=float(daily["temperature_2m_max"][i] or 0),
            temp_min_c=float(daily["temperature_2m_min"][i] or 0),
        ))

    return WeatherWindow(past=days[:past_days], forecast=days[past_days:])
