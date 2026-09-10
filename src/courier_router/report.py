from __future__ import annotations
import json
import re
from datetime import date
from pathlib import Path
from .domain import RouteSolution, Stop
from .geocode import apartment_of, strip_apartment

def hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"

def fmt_km(m: int) -> str:
    return f"{m/1000:.1f}".replace(".", ",")

def display_address(s: Stop) -> str:
    """Адрес для курьера: чистый адрес до дома (как его вернул геокодер) плюс
    номер квартиры из таблицы. В геокодирование квартира не уходит, но курьеру
    она нужна."""
    base = ((s.geo.normalized_address if s.geo else "") or s.address_raw or "").strip()
    flat = apartment_of(s.address_raw)
    if flat and not re.search(r"(?:кв|офис|пом)\.?\s*№?\s*\d", base, re.I):
        base = f"{base}, кв {flat}"
    return base

def itinerary_text(day: str, stops: list[Stop], solution: RouteSolution, depot_address: str, depart_min: int, end_mode: str):
    lines = [
        f"Маршрут на {day}",
        "",
        f"Старт: {hhmm(depart_min)}",
        depot_address,
        "",
    ]
    if solution.used_soft_windows:
        lines.append("⚠ Маршрут построен в best-effort режиме по временным окнам.")
        if solution.warnings:
            lines.extend(f"⚠ {warning}" for warning in solution.warnings)
        lines.append("")
    for number, visit in enumerate(solution.visits, start=1):
        s = stops[visit.stop_index]
        op = "ЗАБОР" if s.operation.value == "pickup" else "ОТВОЗ"
        lines += [
            f"{number}. ETA {hhmm(visit.arrival_min)} · {op} · заказ №{s.order_no}",
            f"   {display_address(s)}",
        ]
        norm = (s.geo.normalized_address if s.geo else "") or ""
        raw_wo_flat = strip_apartment(s.address_raw)
        if raw_wo_flat and raw_wo_flat.strip().lower() not in norm.strip().lower():
            lines.append(f"   в таблице: {s.address_raw}")
        lines += [
            f"   {s.phone}",
            f"   Окно: {s.window.raw if s.window else 'нет'}",
        ]
        if visit.late_by_min:
            lines.append(f"   ⚠ Ожидаемое опоздание: {visit.late_by_min} мин")
        if s.payment.raw:
            lines.append(f"   Оплата: {s.payment.raw}")
        if s.access:
            lines.append(f"   Подъезд/этаж: {s.access}")
        if s.comment:
            lines.append(f"   Комментарий: {s.comment}")
        lines += [
            f"   От предыдущей точки: {fmt_km(visit.distance_m_from_prev)} км · {round(visit.travel_sec_from_prev/60)} мин",
        ]
        note = getattr(s, "coord_note", "")
        if getattr(s, "coord_status", "ok") == "review" and note:
            lines.append(f"   ⚠ {note}")
        for w in s.warnings:
            if w != note:
                lines.append(f"   ⚠ {w}")
        lines.append("")

    total = solution.total_travel_sec + solution.total_service_sec + solution.total_wait_sec
    lines += [
        f"Финиш: {'база' if end_mode == 'depot' else 'свободный конец'}",
        "",
        "Всего:",
        f"{fmt_km(solution.total_distance_m)} км",
        f"{round(solution.total_travel_sec/60)} мин движения",
        f"{round(solution.total_service_sec/60)} мин обслуживания",
        f"{round(solution.total_wait_sec/60)} мин ожидания",
    ]
    if solution.total_late_min:
        lines.append(f"{solution.total_late_min} мин суммарного опоздания по временным окнам")
    lines.append(f"{round(total/60)} мин суммарно без учёта незафиксированных задержек")
    return "\n".join(lines) + "\n"

def route_json(stops: list[Stop], solution: RouteSolution, geometry):
    def stop_obj(s):
        return {
            "source_row": s.source_row,
            "operation": s.operation.value,
            "order_no": s.order_no,
            "phone": s.phone,
            "district": s.district,
            "address_raw": s.address_raw,
            "address_normalized": s.geo.normalized_address if s.geo else None,
            "address_display": display_address(s),
            "lat": s.geo.lat if s.geo else None,
            "lon": s.geo.lon if s.geo else None,
            "geocoder": s.geo.provider if s.geo else None,
            "geocode_precision": s.geo.precision if s.geo else None,
            "geocode_confidence": s.geo.confidence if s.geo else None,
            "window": s.window.raw if s.window else None,
            "payment": s.payment.raw,
            "comment": s.comment,
            "access": s.access,
            "service_min": s.service_min,
            "coord_status": getattr(s, "coord_status", "ok"),
            "coord_note": getattr(s, "coord_note", ""),
            "warnings": s.warnings,
        }
    return {
        "feasible": solution.feasible,
        "summary": {
            "total_distance_m": solution.total_distance_m,
            "total_travel_sec": solution.total_travel_sec,
            "total_service_sec": solution.total_service_sec,
            "total_wait_sec": solution.total_wait_sec,
            "used_soft_windows": solution.used_soft_windows,
            "total_late_min": solution.total_late_min,
            "solver_status": solution.solver_status,
        },
        "visits": [
            {
                "sequence": n,
                "arrival_min": v.arrival_min,
                "departure_min": v.departure_min,
                "travel_sec_from_prev": v.travel_sec_from_prev,
                "distance_m_from_prev": v.distance_m_from_prev,
                "late_by_min": v.late_by_min,
                "stop": stop_obj(stops[v.stop_index]),
            }
            for n,v in enumerate(solution.visits, 1)
        ],
        "geometry": [[lat, lon] for lat,lon in geometry],
        "warnings": solution.warnings,
    }
