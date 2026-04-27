from __future__ import annotations

import aiohttp
from pyrainbird.async_client import create_controller


class RainbirdClient:
    """Async wrapper around pyrainbird 6.x. Tested against ESP-Me / ESP-TM2."""

    def __init__(self, host: str, password: str):
        self.host = host
        self.password = password
        self._session: aiohttp.ClientSession | None = None
        self._controller = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        self._controller = await create_controller(self._session, self.host, self.password)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.close()

    async def rain_sensor_wet(self) -> bool:
        return bool(await self._controller.get_rain_sensor_state())

    async def available_zones(self) -> list[int]:
        stations = await self._controller.get_available_stations()
        return sorted(stations.stations.active_set)

    async def irrigate(self, zone: int, minutes: int) -> None:
        await self._controller.irrigate_zone(zone, minutes)

    async def stop(self) -> None:
        await self._controller.stop_irrigation()
