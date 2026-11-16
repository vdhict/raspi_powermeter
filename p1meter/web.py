"""Dashboard and JSON API, standard library only."""

from __future__ import annotations

import json
import logging
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import battery, store
from .config import Config
from .reader import Aggregator

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_POINTS = 1500
RESOLUTIONS = (10, 30, 60, 300, 900, 3600, 21600, 86400)
DEFAULT_CAPACITIES = (1, 2, 3, 4, 5, 6, 8, 10, 15)


class BadRequest(Exception):
    pass


def make_server(config: Config, aggregator: Aggregator | None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "p1meter"

        def log_message(self, fmt, *args):  # keep the journal quiet
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path.startswith("/api/") or url.path == "/export.csv":
                    db = store.connect(config.database)
                    try:
                        self.route(url.path, query, db)
                    finally:
                        db.close()
                else:
                    self.static(url.path)
            except BadRequest as exc:
                self.send_json({"error": str(exc)}, 400)
            except BrokenPipeError:
                pass
            except Exception:
                log.exception("Fout bij %s", self.path)
                self.send_json({"error": "interne fout"}, 500)

        # -- helpers ---------------------------------------------------

        def send_json(self, payload, status=200):
            self.send_body(json.dumps(payload).encode(), "application/json", status)

        def send_body(self, body: bytes, content_type: str, status=200, extra=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def static(self, path: str):
            name = "index.html" if path == "/" else path.lstrip("/")
            file = (STATIC / name).resolve()
            if STATIC.resolve() not in file.parents or not file.is_file():
                self.send_json({"error": "niet gevonden"}, 404)
                return
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send_body(file.read_bytes(), ctype)

        # -- API -------------------------------------------------------

        def route(self, path, query, db):
            if path == "/api/live":
                self.send_json(live(aggregator))
            elif path == "/api/config":
                span = store.span(db)
                self.send_json(
                    {
                        "import_price": config.import_price,
                        "export_price": config.export_price,
                        "battery_power_w": config.battery_power_w,
                        "battery_efficiency": config.battery_efficiency,
                        "first": span[0] if span else None,
                        "last": span[1] if span else None,
                    }
                )
            elif path == "/api/series":
                start, end = period(query, db)
                res = resolution(start, end)
                rows = store.series(db, start, end, res)
                self.send_json(
                    {
                        "resolution": res,
                        "import_kwh": round(sum(r["import_w"] * r["seconds"] for r in rows) / 3.6e6, 3),
                        "export_kwh": round(sum(r["export_w"] * r["seconds"] for r in rows) / 3.6e6, 3),
                        "points": [[r["t"], round(r["import_w"]), round(r["export_w"])] for r in rows],
                    }
                )
            elif path == "/api/days":
                self.send_json(
                    [
                        {k: (round(r[k], 3) if isinstance(r[k], float) else r[k]) for k in r.keys()}
                        for r in store.days(db)
                    ]
                )
            elif path == "/api/battery":
                self.send_json(battery_report(config, query, db))
            elif path == "/export.csv":
                start, end = period(query, db)
                lines = ["tijd_utc,seconden,afname_w,teruglevering_w,l1_w,l2_w,l3_w,afname_kwh,teruglevering_kwh"]
                for r in db.execute(
                    "SELECT datetime(ts,'unixepoch'), seconds, import_w, export_w, l1_w, l2_w, l3_w,"
                    " import_kwh, export_kwh FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
                    (start, end),
                ):
                    lines.append(",".join("" if v is None else str(v) for v in r))
                self.send_body(
                    ("\n".join(lines) + "\n").encode(),
                    "text/csv; charset=utf-8",
                    extra=[("Content-Disposition", 'attachment; filename="p1meter.csv"')],
                )
            else:
                self.send_json({"error": "niet gevonden"}, 404)

    return ThreadingHTTPServer((config.host, config.port), Handler)


def number(query: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(query.get(key, default))
    except ValueError:
        raise BadRequest(f"{key} is geen getal") from None
    if not low <= value <= high:
        raise BadRequest(f"{key} moet tussen {low:g} en {high:g} liggen")
    return value


def period(query: dict, db) -> tuple[int, int]:
    """Requested [start, end) in unix seconds; defaults to all data."""
    span = store.span(db) or (0, 0)
    start = int(number(query, "start", span[0], 0, 2**40))
    end = int(number(query, "end", span[1] + 1, 0, 2**40))
    if end < start:
        raise BadRequest("end ligt voor start")
    return start, end


def resolution(start: int, end: int) -> int:
    for res in RESOLUTIONS:
        if (end - start) / res <= MAX_POINTS:
            return res
    return RESOLUTIONS[-1]


def live(aggregator: Aggregator | None) -> dict:
    latest = aggregator.latest() if aggregator else None
    if latest is None:
        return {"available": False}
    ts, r = latest
    return {
        "available": True,
        "age": round(time.time() - ts, 1),
        "import_w": r.import_w,
        "export_w": r.export_w,
        "net_w": r.net_w,
        "phases_w": [r.l1_w, r.l2_w, r.l3_w],
        "import_kwh": round(r.import_kwh, 3),
        "export_kwh": round(r.export_kwh, 3),
    }


def battery_report(config: Config, query: dict, db) -> dict:
    start, end = period(query, db)
    power = number(query, "power", config.battery_power_w, 1, 100000)
    efficiency = number(query, "efficiency", config.battery_efficiency, 0.3, 1.0)
    import_price = number(query, "import_price", config.import_price, 0, 10)
    export_price = number(query, "export_price", config.export_price, -10, 10)
    try:
        capacities = [float(c) for c in query["capacities"].split(",")] if "capacities" in query else list(DEFAULT_CAPACITIES)
    except ValueError:
        raise BadRequest("capacities moet een lijst getallen zijn") from None
    if not capacities or len(capacities) > 30 or any(not 0 < c <= 1000 for c in capacities):
        raise BadRequest("ongeldige capacities")

    # One-minute averages keep a year of data fast on a Pi while preserving
    # import and export that occur within the same minute.
    rows = store.series(db, start, end, 60)
    results = battery.simulate(
        ((r["seconds"], r["import_w"], r["export_w"]) for r in rows),
        [battery.Battery(c, power, power, efficiency) for c in capacities],
    )
    days = [d for d in store.days(db, start, end) if d["coverage"] > 0.9]
    return {
        "power_w": power,
        "efficiency": efficiency,
        "import_price": import_price,
        "export_price": export_price,
        "days": round(results[0].hours / 24, 2),
        "import_kwh": round(results[0].import_kwh, 2),
        "export_kwh": round(results[0].export_kwh, 2),
        # What an unlimited battery could shift on each complete day.
        "daily_shiftable_kwh": [round(min(d["import_kwh"], d["export_kwh"]), 2) for d in days],
        "results": [r.as_dict(import_price, export_price) for r in results],
    }
