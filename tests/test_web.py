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


def _client_logged_in(monkeypatch, tmp_path, username="tolya"):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(web, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(web, "USERS_PATH", tmp_path / "users.json")
    web._save_users({username: web._hash_password("pw12345678")})
    monkeypatch.setenv("WEB_HTTPS_ONLY", "0")
    client = TestClient(web.create_app("test-secret"))
    r = client.post("/login", data={"username": username, "password": "pw12345678"},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


def test_delete_own_route(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path)
    folder = web._user_runs_root("tolya") / "abc123def456"
    _write_run(folder, "tolya")
    assert folder.exists()

    r = client.post("/routes/abc123def456/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert not folder.exists()


def test_cannot_delete_another_users_route(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path, "tolya")
    victim = web._user_runs_root("partner") / "partn0000001"
    _write_run(victim, "partner")

    r = client.post("/routes/partn0000001/delete", follow_redirects=False)
    assert r.status_code == 303
    assert victim.exists()          # чужой маршрут не тронут


def test_delete_unknown_route_is_noop(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path)
    r = client.post("/routes/doesnotexist9/delete", follow_redirects=False)
    assert r.status_code == 303


def test_delete_requires_login(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(web, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(web, "USERS_PATH", tmp_path / "users.json")
    client = TestClient(web.create_app("test-secret"))
    r = client.post("/routes/whatever0001/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
