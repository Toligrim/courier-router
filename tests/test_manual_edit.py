import json
from types import SimpleNamespace

import pytest

from courier_router import cli
from courier_router.domain import Operation, Payment, Stop, TimeWindow
from courier_router.optimizer import sequence_route


def _stop(row, win=None, svc=10):
    return Stop(source_row=row, operation=Operation.DELIVERY, order_no=row, phone="",
               district="", address_raw=f"ул Тестовая, д {row}", access="", window=win,
               payment=Payment(raw=""), comment="", service_min=svc)


def test_sequence_route_accumulates_eta_wait_and_return_leg():
    stops = [_stop(1), _stop(2, TimeWindow(10 * 60, 12 * 60)), _stop(3)]
    sol = sequence_route(stops, [600, 900, 1200], [5000, 8000, 10000],
                         depart_min=9 * 60, end_mode="depot", return_leg=(700, 6000))
    assert [v.arrival_min for v in sol.visits] == [9 * 60 + 10, 10 * 60, 10 * 60 + 30]
    assert sol.total_distance_m == 5000 + 8000 + 10000 + 6000
    assert sol.total_travel_sec == 600 + 900 + 1200 + 700
    assert sol.total_wait_sec == 1500
    assert sol.feasible and not sol.used_soft_windows


def test_sequence_route_flags_missed_window_without_dropping_stop():
    stops = [_stop(1, TimeWindow(9 * 60, 9 * 60 + 30))]
    sol = sequence_route(stops, [3600], [4000], depart_min=9 * 60, end_mode="open")
    assert sol.visits[0].late_by_min == 30
    assert sol.used_soft_windows and sol.feasible
    assert any("вручную" in w for w in sol.warnings)


class _FakeRouter:
    def matrix(self, coords):
        n = len(coords)
        dur = [[0 if i == j else 300 for j in range(n)] for i in range(n)]
        dist = [[0 if i == j else 1000 for j in range(n)] for i in range(n)]
        return dur, dist

    def geometry(self, coords):
        return list(coords)


class _StubStore:
    def __init__(self):
        self.aliases = {}

    def put_alias(self, address, district, lat, lon, normalized_address="", note=""):
        self.aliases[(address, district)] = (lat, lon, note)


@pytest.fixture
def run_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "get_router", lambda c: _FakeRouter())
    monkeypatch.setattr(cli, "get_geocoder", lambda c: None)
    monkeypatch.setattr(cli, "depot", lambda c, g: SimpleNamespace(
        lat=59.85, lon=30.32, normalized_address="ул Костюшко, д 2"))
    monkeypatch.setattr(cli, "render_map", lambda *a, **k: None)

    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps(
        {"date": "2026-09-10", "depart": "10:00", "end": "depot", "uploaded_by": "tolya"}))

    def stop_obj(row, lat, lon):
        return {
            "source_row": row, "operation": "delivery", "order_no": 100 + row, "phone": "",
            "district": "Невский", "address_raw": f"ул Тестовая, д {row}",
            "address_normalized": f"г Санкт-Петербург, ул Тестовая, д {row}",
            "address_display": f"г Санкт-Петербург, ул Тестовая, д {row}",
            "lat": lat, "lon": lon, "geocoder": "dadata", "geocode_precision": "exact",
            "geocode_confidence": 0.99, "window": None, "payment": "", "comment": "",
            "access": "", "service_min": 10, "coord_status": "ok", "coord_note": "",
            "warnings": [],
        }

    route = {
        "feasible": True,
        "summary": {"total_distance_m": 3000},
        "visits": [
            {"sequence": 1, "arrival_min": 610, "departure_min": 620,
             "travel_sec_from_prev": 300, "distance_m_from_prev": 1000,
             "late_by_min": 0, "stop": stop_obj(1, 59.90, 30.40)},
            {"sequence": 2, "arrival_min": 640, "departure_min": 650,
             "travel_sec_from_prev": 300, "distance_m_from_prev": 1000,
             "late_by_min": 0, "stop": stop_obj(2, 59.92, 30.45)},
            {"sequence": 3, "arrival_min": 700, "departure_min": 710,
             "travel_sec_from_prev": 300, "distance_m_from_prev": 1000,
             "late_by_min": 0, "stop": stop_obj(3, 59.95, 30.50)},
        ],
        "geometry": [[59.85, 30.32]],
        "warnings": [],
    }
    (folder / "route.json").write_text(json.dumps(route, ensure_ascii=False))
    (folder / "itinerary.txt").write_text("original itinerary")
    return folder


def test_recompute_route_reorders_and_deletes(run_folder):
    store = _StubStore()
    cli.recompute_route(cli.Config(), run_folder, order_rows=[3, 1],
                        deleted_rows={2}, coord_overrides={}, store=store)
    route = json.loads((run_folder / "route.json").read_text())
    assert route["manually_edited"] is True
    rows = [v["stop"]["source_row"] for v in route["visits"]]
    assert rows == [3, 1]
    assert (run_folder / "route.original.json").exists()
    # оригинал сохранён нетронутым
    original = json.loads((run_folder / "route.original.json").read_text())
    assert [v["stop"]["source_row"] for v in original["visits"]] == [1, 2, 3]


def test_recompute_route_applies_and_pins_manual_coordinate(run_folder):
    store = _StubStore()
    cli.recompute_route(cli.Config(), run_folder, order_rows=[1, 2, 3],
                        deleted_rows=set(), coord_overrides={2: (59.775504, 30.077392)},
                        store=store)
    route = json.loads((run_folder / "route.json").read_text())
    moved = next(v["stop"] for v in route["visits"] if v["stop"]["source_row"] == 2)
    assert moved["lat"] == pytest.approx(59.775504)
    assert moved["lon"] == pytest.approx(30.077392)
    assert ("ул Тестовая, д 2", "Невский") in store.aliases


def test_restore_route_reverts_manual_edits(run_folder):
    store = _StubStore()
    cli.recompute_route(cli.Config(), run_folder, order_rows=[3, 2, 1],
                        deleted_rows=set(), coord_overrides={}, store=store)
    assert cli.restore_route(run_folder) is True
    route = json.loads((run_folder / "route.json").read_text())
    assert [v["stop"]["source_row"] for v in route["visits"]] == [1, 2, 3]
    assert not (run_folder / "route.original.json").exists()
    assert (run_folder / "itinerary.txt").read_text() == "original itinerary"
