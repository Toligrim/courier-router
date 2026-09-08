from courier_router.domain import Operation, Payment, RouteSolution, RouteVisit, Stop, TimeWindow
from courier_router.report import itinerary_text, route_json


def test_reports_expose_soft_window_lateness():
    stop = Stop(1, Operation.DELIVERY, 42, "", "", "addr", "", TimeWindow(600, 610, "10:00-10:10"), Payment(), service_min=0)
    visit = RouteVisit(0, 625, 625, 600, 5000, late_by_min=15)
    sol = RouteSolution(
        [visit], 5000, 600, 0, 0,
        warnings=["Строгий поиск маршрута не успел найти решение за отведённое время"],
        used_soft_windows=True, total_late_min=15, solver_status=4,
    )

    data = route_json([stop], sol, [])
    assert data["summary"]["used_soft_windows"] is True
    assert data["summary"]["total_late_min"] == 15
    assert data["summary"]["solver_status"] == 4
    assert data["visits"][0]["late_by_min"] == 15

    text = itinerary_text("2026-09-08", [stop], sol, "depot", 600, "open")
    assert "не успел найти решение" in text
    assert "Все временные окна одновременно выполнить невозможно" not in text
    assert "Ожидаемое опоздание: 15 мин" in text
