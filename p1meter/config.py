from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    device: str = "/dev/ttyUSB0"
    serial_mode: str = "auto"  # auto, dsmr5 (115200 8N1) or dsmr2 (9600 7E1)
    database: str = "data/p1meter.db"
    bucket_seconds: int = 10
    host: str = "0.0.0.0"
    port: int = 8080
    # Defaults for the battery calculator in the dashboard.
    import_price: float = 0.25  # EUR per kWh bought
    export_price: float = 0.05  # EUR per kWh sold (net of feed-in costs)
    battery_power_w: float = 800.0
    battery_efficiency: float = 0.85


def load(path: str | Path | None) -> Config:
    config = Config()
    if path is None:
        return config
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    known = {f.name: f.type for f in fields(Config)}
    for key, value in data.items():
        if key not in known:
            raise SystemExit(f"Onbekende instelling in {path}: {key}")
        setattr(config, key, type(getattr(config, key))(value))
    if config.serial_mode not in ("auto", "dsmr5", "dsmr2"):
        raise SystemExit(f"serial_mode moet auto, dsmr5 of dsmr2 zijn, niet {config.serial_mode!r}")
    return config
