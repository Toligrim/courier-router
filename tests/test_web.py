import json

import pytest

from courier_router import web


def test_password_hash_roundtrip():
    encoded = web._hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert web._verify_password("correct horse battery staple", encoded)
    assert not web._verify_password("wrong password", encoded)


def test_web_app_can_be_created_with_explicit_secret():
    app = web.create_app("test-secret")
    assert app.title == "Courier Router Web"


def _write_run(folder, owner: str, day: str = "2026-09-08", review: bool = False):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(
        json.dumps({"date": day, "depart": "10:00", "uploaded_by": owner}),
        encoding="utf-8",
    )
    (folder / "route.json").write_text(
        json.dumps({
            "summary": {"total_distance_m": 12345},
            "visits": [{
                "sequence": 1,
                "stop": {"coord_status": "review" if review else "ok"},
            }],
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


def test_route_history_counts_coordinate_reviews(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "RUNS_ROOT", tmp_path / "runs")
    folder = web._user_runs_root("tolya") / "review001"
    _write_run(folder, "tolya", review=True)

    runs = web._list_runs("tolya")
    assert runs[0]["review_count"] == 1


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


def test_login_page_is_mobile_friendly():
    page = web._login_page()
    assert 'autocomplete="username"' in page
    assert 'autocomplete="current-password"' in page
    assert 'autocapitalize="none"' in page
    assert 'viewport-fit=cover' in page


def test_home_page_has_dropzone_progress_overlay_and_disabled_submit(monkeypatch):
    monkeypatch.setattr(web, "_list_runs", lambda user: [])
    page = web._home_page("tolya")

    assert 'id="drop-zone"' in page
    assert 'id="selected-file"' in page
    assert 'id="build-submit" type="submit" disabled' in page
    assert 'id="build-overlay"' in page
    assert "Строю маршрут, это займёт до минуты" in page
    assert "input.files" in page
    assert "pageshow" in page
    assert "prefers-color-scheme: dark" in page
    assert "safe-area-inset-bottom" in page


def test_home_page_surfaces_error_line_and_keeps_full_details(monkeypatch):
    monkeypatch.setattr(web, "_list_runs", lambda user: [])
    page = web._home_page("tolya", "headless failed\nERROR: Yandex timeout\ntrace line")

    assert '<div class="error-summary">ERROR: Yandex timeout</div>' in page
    assert '<summary>Подробности</summary>' in page
    assert "headless failed" in page
    assert "trace line" in page


def _sample_route(review: bool = True):
    return {
        "feasible": True,
        "summary": {
            "total_distance_m": 12345,
            "total_travel_sec": 3660,
            "total_wait_sec": 300,
        },
        "geometry": [[59.93, 30.31], [59.94, 30.32]],
        "visits": [{
            "sequence": 1,
            "arrival_min": 600,
            "departure_min": 610,
            "travel_sec_from_prev": 900,
            "distance_m_from_prev": 5000,
            "late_by_min": 0,
            "stop": {
                "order_no": "42",
                "operation": "pickup",
                "phone": "+7 900 000-00-00",
                "payment": "Карта",
                "window": "10:00-12:00",
                "comment": "Позвонить заранее",
                "address_raw": "СПб, Невский 1",
                "address_normalized": "г Санкт-Петербург, Невский проспект, д 1",
                "lat": 59.94,
                "lon": 30.32,
                "coord_status": "review" if review else "ok",
                "coord_note": "точка требует ручной сверки" if review else "",
                "warnings": [],
            },
        }],
    }


def test_route_page_has_mobile_list_map_switch_sticky_navigation_and_menu_delete():
    meta = {"date": "2026-09-09", "depart": "10:00", "end": "open", "uploaded_by": "tolya"}
    page = web._route_page("tolya", "abc123", meta, _sample_route())

    assert 'data-route-view="list"' in page
    assert 'data-route-switch="list"' in page
    assert 'data-route-switch="map"' in page
    assert "map.invalidateSize" in page
    assert "map.setView(marker.getLatLng(), 16" in page
    assert "scrollIntoView" in page
    assert "🕒 Доставка " in page  # окно доставки прямо в попапе маркера
    assert "data-open-list=" in page
    assert 'class="bottom-action"' in page
    assert "🧭 Открыть в Навигаторе" in page
    assert '<summary class="icon-btn" aria-label="Действия с маршрутом">···</summary>' in page
    assert 'class="menu-danger" type="submit">Удалить маршрут</button>' in page
    assert "Маршрут текстом" not in page


def test_route_page_keeps_coordinate_review_separate_from_address_and_simplifies_metrics():
    meta = {"date": "2026-09-09", "depart": "10:00", "end": "open", "uploaded_by": "tolya"}
    page = web._route_page("tolya", "abc123", meta, _sample_route())

    assert "г Санкт-Петербург, Невский проспект, д 1" in page
    assert "в таблице: СПб, Невский 1" in page
    assert "⚠ точка требует ручной сверки" in page
    assert "проверить координаты" not in page  # без отдельной пилюли — только одна строка
    assert 'class="marker-num ${operation}${reviewClass}"' in page
    assert 'class="metric-label">Пробег</span>' in page
    assert 'class="metric-label">Время в пути</span>' in page
    assert "Движение / ожидание" not in page
    assert "1 точка, из них 1 на сверку" in page


def test_route_page_exposes_per_stop_edit_controls():
    meta = {"date": "2026-09-09", "depart": "10:00", "end": "open", "uploaded_by": "tolya"}
    page = web._route_page("tolya", "abc123", meta, _sample_route())
    # номер точки — кнопка «переставить», отдельная кнопка ✎ на карточке
    assert "data-pos-open" in page
    assert "data-pos-input" in page
    assert "data-edit-toggle" in page
    assert 'data-edit="remove"' in page
    assert "data-coord-input" in page
    assert 'data-run-id="abc123"' in page
    # никакого глобального режима правки со стрелками и панелью «Сохранить»
    assert 'data-edit="start"' not in page
    assert 'data-edit="up"' not in page
    assert 'class="edit-bar"' not in page


def test_route_page_shows_reset_only_when_manually_edited():
    meta = {"date": "2026-09-09", "depart": "10:00", "end": "open", "uploaded_by": "tolya"}
    plain = web._route_page("tolya", "abc123", meta, _sample_route())
    assert "Сбросить ручные правки" not in plain
    edited = _sample_route()
    edited["manually_edited"] = True
    page = web._route_page("tolya", "abc123", meta, edited)
    assert "Сбросить ручные правки" in page
    assert "отредактирован вручную" in page


def test_edit_route_recomputes_for_owner(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path)
    folder = web._user_runs_root("tolya") / "editablerun1"
    _write_run(folder, "tolya")
    calls = {}

    def fake_recompute(c, fold, order, deleted, coords, store=None):
        calls["args"] = (fold, order, deleted, coords)
        (fold / "route.json").write_text('{"manually_edited": true, "visits": []}', encoding="utf-8")

    monkeypatch.setattr(web, "recompute_route", fake_recompute)
    r = client.post("/routes/editablerun1/edit",
                    json={"order": [3, 1], "deleted": [2], "coords": {"1": [59.9, 30.4]}})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert calls["args"][1] == [3, 1]
    assert calls["args"][2] == {2}
    assert calls["args"][3] == {1: (59.9, 30.4)}


def test_edit_route_rejects_out_of_region_coord(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path)
    folder = web._user_runs_root("tolya") / "editablerun2"
    _write_run(folder, "tolya")
    monkeypatch.setattr(web, "recompute_route", lambda *a, **k: pytest.fail("must not recompute"))
    r = client.post("/routes/editablerun2/edit",
                    json={"order": [1], "deleted": [], "coords": {"1": [12.3, 45.6]}})
    assert r.status_code == 400


def test_edit_route_blocks_other_users(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path, "tolya")
    victim = web._user_runs_root("partner") / "partnrun0001"
    _write_run(victim, "partner")
    monkeypatch.setattr(web, "recompute_route", lambda *a, **k: pytest.fail("must not recompute"))
    r = client.post("/routes/partnrun0001/edit", json={"order": [1], "deleted": [], "coords": {}})
    assert r.status_code == 404


def test_reset_route_restores_backup(monkeypatch, tmp_path):
    client = _client_logged_in(monkeypatch, tmp_path)
    folder = web._user_runs_root("tolya") / "resetrun0001"
    _write_run(folder, "tolya")
    (folder / "route.original.json").write_text('{"feasible": true, "visits": [1,2,3]}', encoding="utf-8")
    (folder / "itinerary.original.txt").write_text("orig", encoding="utf-8")
    r = client.post("/routes/resetrun0001/reset", follow_redirects=False)
    assert r.status_code == 303
    assert json.loads((folder / "route.json").read_text())["visits"] == [1, 2, 3]
    assert not (folder / "route.original.json").exists()


def test_route_page_handles_infeasible_route_without_map():
    meta = {"date": "2026-09-09", "depart": "10:00", "end": "open", "uploaded_by": "tolya"}
    page = web._route_page(
        "tolya", "abc123", meta,
        {"feasible": False, "warnings": ["Временные окна несовместимы"]},
    )

    assert "Маршрут не построен" in page
    assert "Временные окна несовместимы" in page
    assert 'id="map"' not in page
    assert 'href="/">← К загрузке</a>' in page
