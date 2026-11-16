"""Home-battery simulation on measured grid import/export.

The battery follows the "zero on the meter" strategy every plug-in battery
with a P1 dongle uses: charge with whatever would otherwise be exported,
discharge to cover whatever would otherwise be imported, both limited by the
inverter power and the state of charge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Battery:
    capacity_kwh: float  # usable capacity
    charge_w: float = 800.0
    discharge_w: float = 800.0
    efficiency: float = 0.85  # round trip, AC to AC


@dataclass
class Result:
    battery: Battery
    hours: float = 0.0  # measured time covered by the data
    import_kwh: float = 0.0  # without battery
    export_kwh: float = 0.0
    import_after_kwh: float = 0.0  # with battery
    export_after_kwh: float = 0.0
    charged_kwh: float = 0.0  # taken from the surplus (AC side)
    discharged_kwh: float = 0.0  # delivered to the house (AC side)
    soc_kwh: float = field(default=0.0, repr=False)

    @property
    def cycles(self) -> float:
        cap = self.battery.capacity_kwh
        return self.discharged_kwh / cap if cap else 0.0

    def saving(self, import_price: float, export_price: float) -> float:
        """Money saved over the measured period.

        Every kWh discharged avoids buying one; every kWh charged is one that
        can no longer be sold. The difference between the two volumes is the
        conversion loss.
        """
        return self.discharged_kwh * import_price - self.charged_kwh * export_price

    def as_dict(self, import_price: float, export_price: float) -> dict:
        days = self.hours / 24 if self.hours else 0.0
        saving = self.saving(import_price, export_price)
        return {
            "capacity_kwh": self.battery.capacity_kwh,
            "import_kwh": round(self.import_kwh, 3),
            "export_kwh": round(self.export_kwh, 3),
            "import_after_kwh": round(self.import_after_kwh, 3),
            "export_after_kwh": round(self.export_after_kwh, 3),
            "charged_kwh": round(self.charged_kwh, 3),
            "discharged_kwh": round(self.discharged_kwh, 3),
            "cycles": round(self.cycles, 2),
            "saving": round(saving, 2),
            "saving_per_year": round(saving / days * 365, 2) if days else 0.0,
            "days": round(days, 2),
        }


def simulate(
    samples: Iterable[tuple[float, float, float]], batteries: list[Battery]
) -> list[Result]:
    """Run all batteries over `samples` of (seconds, import_w, export_w).

    A sample may contain both import and export (averages over an interval
    in which the direction changed); both are handled.
    """
    results = [Result(b) for b in batteries]
    for seconds, import_w, export_w in samples:
        hours = seconds / 3600.0
        for r in results:
            b = r.battery
            eta = b.efficiency**0.5  # same loss charging and discharging
            r.hours += hours
            r.import_kwh += import_w * hours / 1000
            r.export_kwh += export_w * hours / 1000

            charge = 0.0
            if export_w > 0 and hours > 0:
                room_w = (b.capacity_kwh - r.soc_kwh) * 1000 / (eta * hours)
                charge = max(0.0, min(export_w, b.charge_w, room_w))
                r.soc_kwh += charge * eta * hours / 1000

            discharge = 0.0
            if import_w > 0 and hours > 0:
                stock_w = r.soc_kwh * 1000 * eta / hours
                discharge = max(0.0, min(import_w, b.discharge_w, stock_w))
                r.soc_kwh -= discharge / eta * hours / 1000

            r.charged_kwh += charge * hours / 1000
            r.discharged_kwh += discharge * hours / 1000
            r.import_after_kwh += (import_w - discharge) * hours / 1000
            r.export_after_kwh += (export_w - charge) * hours / 1000
    return results
