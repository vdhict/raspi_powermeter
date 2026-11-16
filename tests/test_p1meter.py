import json
import threading
import urllib.error
import urllib.request

import pytest

from p1meter import battery, store
from p1meter.config import Config
from p1meter.reader import Aggregator, split_telegrams
from p1meter.telegram import Reading, TelegramError, build, crc16, parse
from p1meter.web import make_server

# Example telegram from the DSMR 5.0 P1 companion standard (shortened).
DSMR5 = (
    b"/ISk5\\2MT382-1000\r\n"
    b"\r\n"
    b"1-3:0.2.8(50)\r\n"
    b"0-0:1.0.0(101209113020W)\r\n"
    b"1-0:1.8.1(123456.789*kWh)\r\n"
    b"1-0:1.8.2(123456.789*kWh)\r\n"
    b"1-0:2.8.1(123456.789*kWh)\r\n"
    b"1-0:2.8.2(123456.789*kWh)\r\n"
    b"0-0:96.14.0(0002)\r\n"
    b"1-0:1.7.0(01.193*kW)\r\n"
    b"1-0:2.7.0(00.000*kW)\r\n"
    b"1-0:21.7.0(01.111*kW)\r\n"
    b"1-0:41.7.0(02.222*kW)\r\n"
    b"1-0:61.7.0(03.333*kW)\r\n"
    b"1-0:22.7.0(04.444*kW)\r\n"
    b"1-0:42.7.0(05.555*kW)\r\n"
    b"1-0:62.7.0(06.666*kW)\r\n"
    b"0-1:24.2.1(101209112500W)(12785.123*m3)\r\n"
    b"!"
)


def with_crc(body: bytes) -> bytes:
    return body + f"{crc16(body):04X}\r\n".encode()


def test_crc16_known_value():
    assert crc16(b"123456789") == 0xBB3D  # CRC-16/ARC check value


def test_parse_dsmr5():
    r = parse(with_crc(DSMR5))
    assert r.import_w == pytest.approx(1193)
    assert r.export_w == 0
    assert r.import_kwh == pytest.approx(246913.578)
    assert r.export_kwh == pytest.approx(246913.578)
    assert r.l1_w == pytest.approx(1111 - 4444)
    assert r.l3_w == pytest.approx(3333 - 6666)
    assert r.tariff == 2


def test_parse_rejects_corruption():
    corrupted = with_crc(DSMR5).replace(b"01.193", b"09.193")
    with pytest.raises(TelegramError):
        parse(corrupted)


def test_parse_without_crc_dsmr2():
    old = b"/XMX5XMXABCE\r\n\r\n1-0:1.8.1(00100.000*kWh)\r\n1-0:1.8.2(00050.5*kWh)\r\n1-0:1.7.0(0000.42*kW)\r\n!\r\n"
    r = parse(old)
    assert r.import_w == pytest.approx(420)
    assert r.import_kwh == pytest.approx(150.5)
    assert r.l1_w is None


def test_parse_incomplete():
    with pytest.raises(TelegramError):
        parse(b"1-0:1.7.0(0000.42*kW)\r\n")
    with pytest.raises(TelegramError):
        parse(b"/X\r\n0-0:96.14.0(0002)\r\n!\r\n")


def test_build_roundtrip():
    r = parse(build(Reading(250, 0, 100.5, 20.25, l1_w=-300)))
    assert (r.import_w, r.export_w, r.l1_w) == (250, 0, -300)
    assert r.import_kwh == pytest.approx(100.5)
    assert r.export_kwh == pytest.approx(20.25)


def test_split_telegrams_resyncs():
    good = with_crc(DSMR5)
    lines = [b"1-0:1.7.0(half)\r\n", b"!ABCD\r\n"] + good.splitlines(keepends=True) + [b""]
    stream = iter(lines)
    gen = split_telegrams(lambda: next(stream))
    assert next(gen) == good
    assert next(gen) == b""  # timeout is passed on


def test_aggregator_buckets():
    rows = []
    agg = Aggregator(rows.append, 10)
    agg.add(Reading(100, 0, 1.0, 0.0), now=1000)
    agg.add(Reading(300, 0, 1.1, 0.0), now=1005)
    agg.add(Reading(0, 500, 1.1, 0.2, l1_w=-500), now=1012)
    assert rows == [(1000, 10, 200, 0, None, None, None, 1.1, 0.0)]
    agg.close()
    assert rows[1] == (1010, 10, 0, 500, -500, None, None, 1.1, 0.2)
    assert agg.latest()[1].export_w == 500


def test_battery_shifts_surplus_to_evening():
    hour = 3600
    # 2 h of 1000 W surplus, then 4 h of 500 W demand
    samples = [(hour, 0, 1000)] * 2 + [(hour, 500, 0)] * 4
    (r,) = battery.simulate(samples, [battery.Battery(10, 800, 800, efficiency=1.0)])
    assert r.charged_kwh == pytest.approx(1.6)  # limited to 800 W
    assert r.export_after_kwh == pytest.approx(0.4)
    assert r.discharged_kwh == pytest.approx(1.6)
    assert r.import_after_kwh == pytest.approx(0.4)
    assert r.saving(0.25, 0.05) == pytest.approx(1.6 * 0.20)


def test_battery_capacity_and_losses():
    samples = [(3600, 0, 800)] * 5 + [(3600, 800, 0)] * 5
    (r,) = battery.simulate(samples, [battery.Battery(2, 800, 800, efficiency=0.81)])
    assert r.charged_kwh == pytest.approx(2 / 0.9)  # 2 kWh stored at 90% charge efficiency
    assert r.discharged_kwh == pytest.approx(2 * 0.9)
    assert r.discharged_kwh / r.charged_kwh == pytest.approx(0.81)
    assert r.cycles == pytest.approx(0.9)
    # energy is conserved on the grid side
    assert r.import_kwh - r.import_after_kwh == pytest.approx(r.discharged_kwh)
    assert r.export_kwh - r.export_after_kwh == pytest.approx(r.charged_kwh)


def test_bigger_battery_never_saves_less():
    samples = [(60, 0, 1500)] * 300 + [(60, 600, 0)] * 900
    results = battery.simulate(samples, [battery.Battery(c) for c in (1, 2, 5, 10)])
    savings = [r.saving(0.25, 0.05) for r in results]
    assert savings == sorted(savings)


@pytest.fixture
def server(tmp_path):
    config = Config(database=str(tmp_path / "t.db"), host="127.0.0.1", port=0)
    writer = store.Writer(config.database)
    agg = Aggregator(writer.add, 10)
    base = 1_750_000_000 // 86400 * 86400
    for i in range(360):  # one hour: first half export, second half import
        first = i < 180
        agg.add(Reading(0 if first else 600, 1000 if first else 0, 10.0, 5.0), now=base + i * 10)
    agg.close()
    writer.close()
    srv = make_server(config, agg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as response:
        return response.status, response.read()


def test_api(server):
    live = json.loads(get(server + "/api/live")[1])
    assert live["available"] and live["net_w"] == 600

    series = json.loads(get(server + "/api/series")[1])
    assert series["resolution"] == 10 and len(series["points"]) == 360
    assert series["export_kwh"] == pytest.approx(0.5)
    assert series["import_kwh"] == pytest.approx(0.3)

    days = json.loads(get(server + "/api/days")[1])
    assert sum(d["export_kwh"] for d in days) == pytest.approx(0.5)

    report = json.loads(get(server + "/api/battery?capacities=1&efficiency=1&power=800")[1])
    result = report["results"][0]
    assert result["charged_kwh"] == pytest.approx(0.4)
    assert result["discharged_kwh"] == pytest.approx(0.3)

    status, body = get(server + "/export.csv")
    assert status == 200 and len(body.splitlines()) == 361


def test_static_and_errors(server):
    assert b"<title>" in get(server + "/")[1]
    assert get(server + "/app.js")[0] == 200
    for path in ("/../config.py", "/nope", "/api/nope", "/api/battery?power=abc", "/api/series?start=5&end=1"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(server + path)
        assert exc.value.code in (400, 404)
