"""Parsing of DSMR P1 telegrams (Dutch/Belgian smart meters).

A telegram starts with a line beginning with '/', ends with a line beginning
with '!' and (DSMR 4+) four hex digits holding a CRC16 over everything from
'/' up to and including '!'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class TelegramError(ValueError):
    pass


@dataclass
class Reading:
    """One meter reading. Power in watts, energy counters in kWh."""

    import_w: float
    export_w: float
    import_kwh: float  # total of both tariffs
    export_kwh: float
    # Net power per phase (import minus export); None when the meter is
    # single-phase or does not report it.
    l1_w: float | None = None
    l2_w: float | None = None
    l3_w: float | None = None
    tariff: int | None = None

    @property
    def net_w(self) -> float:
        return self.import_w - self.export_w


def crc16(data: bytes) -> int:
    """CRC16/ARC as specified by DSMR (poly x16+x15+x2+1, LSB first, init 0)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)(\(.*\))\s*$")
_VALUE = re.compile(r"\(([^()]*)\)")


def _number(raw: str) -> float:
    return float(raw.split("*", 1)[0])


def parse(telegram: bytes, verify_crc: bool = True) -> Reading:
    """Parse a complete telegram (from '/' through the '!' line)."""
    start = telegram.find(b"/")
    end = telegram.rfind(b"!")
    if start < 0 or end < start:
        raise TelegramError("incomplete telegram")

    crc_text = telegram[end + 1 : end + 5].strip()
    if verify_crc and crc_text:
        try:
            expected = int(crc_text, 16)
        except ValueError as exc:
            raise TelegramError(f"unreadable CRC {crc_text!r}") from exc
        actual = crc16(telegram[start : end + 1])
        if actual != expected:
            raise TelegramError(f"CRC mismatch ({actual:04X} != {expected:04X})")

    values: dict[str, list[str]] = {}
    for line in telegram[start:end].decode("ascii", errors="replace").splitlines():
        match = _LINE.match(line.strip())
        if match:
            values[match.group(1)] = _VALUE.findall(match.group(2))

    def get(obis: str) -> float | None:
        # Channel prefix differs between meters ("1-0:" is the norm).
        for key in (f"1-0:{obis}", f"1-1:{obis}"):
            if key in values and values[key]:
                try:
                    return _number(values[key][-1])
                except ValueError as exc:
                    raise TelegramError(f"bad value for {key}") from exc
        return None

    def total(*obis_codes: str) -> float | None:
        found = [v for v in map(get, obis_codes) if v is not None]
        return sum(found) if found else None

    import_kw = get("1.7.0")
    export_kw = get("2.7.0")
    # 1.8.1/1.8.2 are the two tariffs; some (Belgian/older) meters only
    # report the tariff-less total 1.8.0.
    import_kwh = total("1.8.1", "1.8.2") or get("1.8.0")
    export_kwh = total("2.8.1", "2.8.2") or get("2.8.0")

    if import_kw is None or import_kwh is None:
        raise TelegramError("telegram has no electricity readings")

    def phase(imp: str, exp: str) -> float | None:
        i, e = get(imp), get(exp)
        if i is None and e is None:
            return None
        return ((i or 0.0) - (e or 0.0)) * 1000.0

    tariff = values.get("0-0:96.14.0")
    return Reading(
        import_w=import_kw * 1000.0,
        export_w=(export_kw or 0.0) * 1000.0,
        import_kwh=import_kwh,
        export_kwh=export_kwh or 0.0,
        l1_w=phase("21.7.0", "22.7.0"),
        l2_w=phase("41.7.0", "42.7.0"),
        l3_w=phase("61.7.0", "62.7.0"),
        tariff=int(tariff[0]) if tariff and tariff[0].isdigit() else None,
    )


def build(reading: Reading, timestamp: str = "260101120000W") -> bytes:
    """Build a valid DSMR 5 telegram from a reading (simulator and tests)."""
    t1 = reading.import_kwh * 0.45
    t2 = reading.import_kwh - t1
    r1 = reading.export_kwh * 0.3
    r2 = reading.export_kwh - r1
    lines = [
        "/SIM5\\P1METER-SIMULATOR",
        "",
        "1-3:0.2.8(50)",
        f"0-0:1.0.0({timestamp})",
        f"1-0:1.8.1({t1:010.3f}*kWh)",
        f"1-0:1.8.2({t2:010.3f}*kWh)",
        f"1-0:2.8.1({r1:010.3f}*kWh)",
        f"1-0:2.8.2({r2:010.3f}*kWh)",
        f"0-0:96.14.0({reading.tariff or 2:04d})",
        f"1-0:1.7.0({reading.import_w / 1000:06.3f}*kW)",
        f"1-0:2.7.0({reading.export_w / 1000:06.3f}*kW)",
    ]
    for obis_in, obis_out, value in (
        ("21.7.0", "22.7.0", reading.l1_w),
        ("41.7.0", "42.7.0", reading.l2_w),
        ("61.7.0", "62.7.0", reading.l3_w),
    ):
        if value is not None:
            lines.append(f"1-0:{obis_in}({max(value, 0) / 1000:06.3f}*kW)")
            lines.append(f"1-0:{obis_out}({max(-value, 0) / 1000:06.3f}*kW)")
    body = ("\r\n".join(lines) + "\r\n!").encode("ascii")
    return body + f"{crc16(body):04X}\r\n".encode("ascii")
