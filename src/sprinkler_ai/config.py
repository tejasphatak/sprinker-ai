from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Zone:
    number: int
    name: str
    plant: str = ""
    sun: str = ""
    soil: str = ""
    slope_percent: float = 0.0
    notes: str = ""
    precipitation_rate_mm_per_hour: float = 0.0  # 0 = use global default
    soil_sensor_channel: int = 0  # WH51 channel (1-8); 0 = no sensor on this zone


@dataclass
class Location:
    latitude: float
    longitude: float
    timezone: str
    # Free-form descriptors used to make the AI planner location-aware. None of
    # these need to be precise — they shape the system prompt, not your address.
    region: str = ""              # e.g. "Midwest USA", "South-east England", "Coastal NSW"
    climate: str = ""             # e.g. "humid continental", "Mediterranean", "tropical"
    hardiness_zone: str = ""      # USDA / RHS zone, e.g. "6b", "H4"


@dataclass
class Notifications:
    discord_webhook_url: str = ""


@dataclass
class Bot:
    """Defaults for the Discord lawn-diagnosis bot's appearance."""
    name: str = "Sahadev"
    avatar_url: str = ""


@dataclass
class CameraTarget:
    """Maps a Nest camera (matched by room-name keyword) to a label and zones."""
    room_keyword: str             # case-insensitive substring of the camera's room
    label: str                    # short slug used in vision prompt + cache key
    covers_zones: list[int] = field(default_factory=list)


@dataclass
class Config:
    location: Location
    zones: list[Zone]
    model: str = "sonnet"
    site_notes: str = ""
    grass_type: str = ""          # e.g. "cool-season fescue", "Bermuda", "St. Augustine"
    soil_type_default: str = ""   # e.g. "clay loam", "sandy loam"
    camera_descriptions: dict[str, str] = field(default_factory=dict)
    camera_targets: list[CameraTarget] = field(default_factory=list)
    invisible_zones_note: str = ""  # zones cameras can't see — passed to vision prompt
    notifications: Notifications = field(default_factory=Notifications)
    bot: Bot = field(default_factory=Bot)
    rainbird_host: str = ""
    rainbird_password: str = ""
    ecowitt_host: str = ""
    claude_bin: str = "claude"
    gemini_bin: str = "gemini"
    gemini_model: str = "pro"
    precipitation_rate_mm_per_hour: float = 38.0  # ~1.5 in/hr spray heads

    @classmethod
    def load(cls, config_path: Path | None = None, env_path: Path | None = None) -> "Config":
        load_dotenv(env_path or REPO_ROOT / ".env")
        path = config_path or REPO_ROOT / "config.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run `sprinkler-ai-init` to create one, or copy "
                "config.example.yaml to config.yaml and edit it."
            )
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        loc = Location(**raw["location"])
        zones = [Zone(**z) for z in raw["zones"]]
        notifications = Notifications(**raw.get("notifications", {}))
        bot = Bot(**raw.get("bot", {}))
        camera_descriptions = {
            (k or "").lower(): (v or "").strip()
            for k, v in (raw.get("camera_descriptions") or {}).items()
        }
        camera_targets = [
            CameraTarget(
                room_keyword=str(t["room_keyword"]).lower(),
                label=str(t["label"]),
                covers_zones=[int(z) for z in t.get("covers_zones") or []],
            )
            for t in raw.get("camera_targets") or []
        ]
        return cls(
            location=loc,
            zones=zones,
            model=raw.get("model", "sonnet"),
            site_notes=(raw.get("site_notes") or "").strip(),
            grass_type=(raw.get("grass_type") or "").strip(),
            soil_type_default=(raw.get("soil_type_default") or "").strip(),
            camera_descriptions=camera_descriptions,
            camera_targets=camera_targets,
            invisible_zones_note=(raw.get("invisible_zones_note") or "").strip(),
            notifications=notifications,
            bot=bot,
            rainbird_host=os.environ.get("RAINBIRD_HOST", ""),
            rainbird_password=os.environ.get("RAINBIRD_PASSWORD", ""),
            ecowitt_host=os.environ.get("ECOWITT_HOST", raw.get("ecowitt_host", "")),
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            gemini_bin=os.environ.get("GEMINI_BIN", "gemini"),
            gemini_model=raw.get("gemini_model", "pro"),
            precipitation_rate_mm_per_hour=float(raw.get("precipitation_rate_mm_per_hour", 38.0)),
        )
