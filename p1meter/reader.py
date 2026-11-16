"""Reading telegrams from the P1 port and aggregating them into buckets."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterator

from .telegram import Reading, TelegramError, parse

log = logging.getLogger(__name__)

# (baudrate, bytesize, parity, seconds to wait for a first valid telegram)
SERIAL_MODES = {
    "dsmr5": (115200, 8, "N", 15),  # DSMR 4 and 5: 115200 8N1
    "dsmr2": (9600, 7, "E", 25),  # DSMR 2 and 3: 9600 7E1
}


def split_telegrams(read: Callable[[], bytes]) -> Iterator[bytes]:
    """Yield complete raw telegrams from a function returning serial lines.

    `read` returns b"" on timeout; the generator then yields b"" as well so
    the caller can notice that nothing is coming in.
    """
    buffer: list[bytes] = []
    while True:
        line = read()
        if not line:
            yield b""
            continue
        if line.startswith(b"/"):
            buffer = [line]
        elif buffer:
            buffer.append(line)
            if line.startswith(b"!"):
                yield b"".join(buffer)
                buffer = []
            elif len(buffer) > 200:  # garbage, never saw the end marker
                buffer = []


def serial_readings(device: str, mode: str = "auto") -> Iterator[Reading]:
    """Yield readings from a P1 cable forever, reconnecting on errors.

    With mode "auto" both serial settings are tried until telegrams arrive.
    """
    import serial  # imported here so tests and the simulator do not need it

    candidates = list(SERIAL_MODES) if mode == "auto" else [mode]
    index = 0
    while True:
        name = candidates[index % len(candidates)]
        baud, bytesize, parity, patience = SERIAL_MODES[name]
        try:
            with serial.Serial(
                device, baudrate=baud, bytesize=bytesize, parity=parity, stopbits=1, timeout=2
            ) as port:
                log.info("Luisteren op %s (%s, %d baud)", device, name, baud)
                last_ok = time.monotonic()
                locked = False
                for raw in split_telegrams(port.readline):
                    if raw:
                        try:
                            reading = parse(raw)
                        except TelegramError as exc:
                            log.debug("Telegram overgeslagen: %s", exc)
                        else:
                            if not locked:
                                log.info("Telegrammen ontvangen (%s)", name)
                                locked = True
                            last_ok = time.monotonic()
                            yield reading
                    # Give up on this setting only when it never worked, or
                    # when the meter has been silent for a long time.
                    silent = time.monotonic() - last_ok
                    if silent > (120 if locked else patience):
                        log.warning("Geen geldige telegrammen (%s), opnieuw proberen", name)
                        break
        except (serial.SerialException, OSError) as exc:
            log.warning("Seriële poort %s niet beschikbaar: %s", device, exc)
            time.sleep(5)
        index += 1


class Aggregator:
    """Averages readings into fixed time buckets and keeps the live value."""

    def __init__(self, on_bucket: Callable[[tuple], None], bucket_seconds: int = 10):
        self._on_bucket = on_bucket
        self._size = bucket_seconds
        self._bucket: int | None = None
        self._readings: list[Reading] = []
        self._lock = threading.Lock()
        self._latest: tuple[float, Reading] | None = None

    def add(self, reading: Reading, now: float | None = None) -> None:
        now = time.time() if now is None else now
        bucket = int(now) // self._size * self._size
        with self._lock:
            self._latest = (now, reading)
            if self._bucket is not None and bucket != self._bucket:
                self._emit()
            self._bucket = bucket
            self._readings.append(reading)

    def close(self) -> None:
        with self._lock:
            self._emit()

    def latest(self) -> tuple[float, Reading] | None:
        with self._lock:
            return self._latest

    def _emit(self) -> None:
        if not self._readings:
            return
        rs = self._readings
        n = len(rs)

        def phase(attr: str) -> float | None:
            vals = [getattr(r, attr) for r in rs if getattr(r, attr) is not None]
            return sum(vals) / len(vals) if vals else None

        self._on_bucket(
            (
                self._bucket,
                self._size,
                sum(r.import_w for r in rs) / n,
                sum(r.export_w for r in rs) / n,
                phase("l1_w"),
                phase("l2_w"),
                phase("l3_w"),
                rs[-1].import_kwh,
                rs[-1].export_kwh,
            )
        )
        self._readings = []
