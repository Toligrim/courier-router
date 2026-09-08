import json

from courier_router import web


def test_password_hash_roundtrip():
    encoded = web._hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert web._verify_password("correct horse battery staple", encoded)
    assert not web._verify_password("wrong password", encoded)


def test_web_app_can_be_created_with_explicit_secret():
    app = web.create_app("test-secret")
    assert app.title == "Courier Router Web"


def _write_run(folder, owner: str, day: str = "2026-09-08"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(
        json.dumps({"date": day, "depart": "10:00", "uploaded_by": owner}),
        encoding="utf-8",
    )
    (folder / "route.json").write_text(
        json.dumps({
            "summary": {"total_distance_m": 12345},
            "visits": [{"sequence": 1}],
        }),
        encoding="utf-8",
    )
    (folder / "itinerary.txt").write_text("route", encoding="utf-8")


def test_route_history_is_isolated_per_user(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "RUNS_ROOT", tmp_path / "runs")

    tolya = web._user_runs_root("tolya") / "tolya001"
    partner = web._user_runs_root("partner") / "partn001"
    _write_run(tolya, "tolya")
    _write_run(partner, "partner")

    assert [run["id"] for run in web._list_runs("tolya")] == ["tolya001"]
    assert [run["id"] for run in web._list_runs("partner")] == ["partn001"]
    assert web._find_run_folder("tolya", "partn001") is None
    assert web._find_run_folder("partner", "tolya001") is None
    assert web._find_run_folder("tolya", "tolya001") == tolya


def test_legacy_v1_route_is_visible_only_to_recorded_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "RUNS_ROOT", tmp_path / "runs")

    legacy = web.RUNS_ROOT / "legacy001"
    _write_run(legacy, "tolya")

    assert [run["id"] for run in web._list_runs("tolya")] == ["legacy001"]
    assert web._list_runs("partner") == []
    assert web._find_run_folder("tolya", "legacy001") == legacy
    assert web._find_run_folder("partner", "legacy001") is None
