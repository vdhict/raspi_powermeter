"""A fake household with solar panels, for trying things out without a meter."""

from __future__ import annotations

import math
import random
import time
from datetime import datetime
from typing import Iterator

from .telegram import Reading


class Household:
    def __init__(self, seed: int = 1, solar_wp: float = 3500.0):
        self._rng = random.Random(seed)
        self._solar_wp = solar_wp
        self._import_kwh = 12345.0
        self._export_kwh = 4321.0
        self._appliance_w = 0.0
        self._appliance_until = 0.0
        self._cloud = 0.7
        self._last: float | None = None

    def reading(self, now: float) -> Reading:
        rng = self._rng
        local = datetime.fromtimestamp(now)
        hour = local.hour + local.minute / 60 + local.second / 3600

        load = 180 + 40 * math.sin(now / 900)  # fridge, standby, ...
        load += 350 * math.exp(-((hour - 7.5) ** 2) / 1.5)  # breakfast
        load += 700 * math.exp(-((hour - 19.0) ** 2) / 5.0)  # evening
        if now >= self._appliance_until:
            self._appliance_w = 0.0
            # kettle, oven, washing machine: mostly during waking hours
            if 6.5 < hour < 23 and rng.random() < 0.004:
                self._appliance_w = rng.choice([900, 1400, 2000, 2200])
                self._appliance_until = now + rng.uniform(120, 2400)
        load += self._appliance_w + rng.gauss(0, 15)

        day_of_year = local.timetuple().tm_yday
        season = 0.55 - 0.45 * math.cos(2 * math.pi * (day_of_year + 10) / 365)
        half_day = 4.0 + 4.5 * season  # hours between sunrise and solar noon
        elevation = math.cos((hour - 13.2) / half_day * math.pi / 2)
        self._cloud = min(1.0, max(0.1, self._cloud + rng.gauss(0, 0.02)))
        solar = self._solar_wp * season * self._cloud * elevation if abs(hour - 13.2) < half_day else 0.0

        net = load - max(solar, 0.0)
        if self._last is not None:
            hours = (now - self._last) / 3600
            self._import_kwh += max(net, 0) * hours / 1000
            self._export_kwh += max(-net, 0) * hours / 1000
        self._last = now
        return Reading(
            import_w=round(max(net, 0.0)),
            export_w=round(max(-net, 0.0)),
            import_kwh=self._import_kwh,
            export_kwh=self._export_kwh,
            l1_w=round(net),
            tariff=2 if 7 <= hour < 23 and local.weekday() < 5 else 1,
        )


def live_readings(interval: float = 1.0) -> Iterator[Reading]:
    house = Household()
    while True:
        yield house.reading(time.time())
        time.sleep(interval)


def history(days: float, step: int = 10) -> Iterator[tuple[float, Reading]]:
    """Readings for the past `days` days, one per `step` seconds."""
    house = Household()
    end = int(time.time()) // step * step
    for ts in range(end - int(days * 86400), end, step):
        yield ts, house.reading(ts)
