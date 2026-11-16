"""Command line: `python -m p1meter run|demo|test|report`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from . import battery, simulator, store
from .config import load
from .reader import Aggregator, serial_readings

log = logging.getLogger("p1meter")


def cmd_run(args, config) -> None:
    """Log the meter and serve the dashboard."""
    from .web import make_server

    writer = store.Writer(config.database)
    aggregator = Aggregator(writer.add, config.bucket_seconds)
    readings = simulator.live_readings() if args.simulate else serial_readings(config.device, config.serial_mode)

    def pump():
        for reading in readings:
            aggregator.add(reading)

    threading.Thread(target=pump, daemon=True, name="reader").start()
    server = make_server(config, aggregator)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    log.info("Dashboard op http://%s:%d", config.host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        aggregator.close()
        writer.close()


def cmd_test(args, config) -> None:
    """Print readings from the P1 port, to check the cable."""
    for i, r in enumerate(serial_readings(config.device, config.serial_mode)):
        print(
            f"afname {r.import_w:6.0f} W  teruglevering {r.export_w:6.0f} W  "
            f"standen {r.import_kwh:.3f} / {r.export_kwh:.3f} kWh"
        )
        if i + 1 >= args.count:
            break


def cmd_demo(args, config) -> None:
    """Fill the database with simulated history."""
    writer = store.Writer(config.database, batch=5000)
    aggregator = Aggregator(writer.add, config.bucket_seconds)
    for ts, reading in simulator.history(args.days, config.bucket_seconds):
        aggregator.add(reading, ts)
    aggregator.close()
    writer.close()
    print(f"{args.days:g} dagen gesimuleerde data geschreven naar {config.database}")


def cmd_report(args, config) -> None:
    """Battery sizing table on the terminal."""
    db = store.connect(config.database)
    span = store.span(db)
    if span is None:
        sys.exit("Nog geen metingen in de database.")
    start = span[1] - int(args.days * 86400) if args.days else span[0]
    rows = store.series(db, start, span[1] + 1, 60)
    results = battery.simulate(
        ((r["seconds"], r["import_w"], r["export_w"]) for r in rows),
        [battery.Battery(c, args.power, args.power, config.battery_efficiency) for c in args.capacities],
    )
    base = results[0]
    print(
        f"Periode: {base.hours / 24:.1f} dagen gemeten  |  afname {base.import_kwh:.1f} kWh, "
        f"teruglevering {base.export_kwh:.1f} kWh"
    )
    print(
        f"Batterij: {args.power:.0f} W, rendement {config.battery_efficiency:.0%}  |  "
        f"prijs afname EUR {config.import_price:.2f}, teruglevering EUR {config.export_price:.2f} per kWh\n"
    )
    print(f"{'kWh':>5} {'afname':>9} {'terug':>9} {'verschoven':>11} {'cycli':>6} {'besparing':>10} {'per jaar':>9}")
    for r in results:
        d = r.as_dict(config.import_price, config.export_price)
        print(
            f"{d['capacity_kwh']:5g} {d['import_after_kwh']:9.1f} {d['export_after_kwh']:9.1f} "
            f"{d['discharged_kwh']:11.1f} {d['cycles']:6.1f} {d['saving']:10.2f} {d['saving_per_year']:9.0f}"
        )
    print("\n'per jaar' rekent het gemeten patroon door naar 365 dagen; meet dus over meerdere seizoenen.")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="p1meter", description=__doc__)
    parser.add_argument("-c", "--config", help="pad naar config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="meter loggen en dashboard serveren")
    p.add_argument("--simulate", action="store_true", help="gesimuleerde meter i.p.v. de P1-poort")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("test", help="metingen van de P1-poort tonen")
    p.add_argument("-n", "--count", type=int, default=10)
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("demo", help="database vullen met gesimuleerde data")
    p.add_argument("--days", type=float, default=14)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("report", help="batterij-advies in de terminal")
    p.add_argument("--days", type=float, help="alleen de laatste N dagen")
    p.add_argument("--power", type=float, default=800)
    p.add_argument("--capacities", type=float, nargs="+", default=[1, 2, 3, 5, 8, 10])
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args.func(args, load(args.config))


if __name__ == "__main__":
    main()
