import csv

import pytest
from openpyxl import Workbook

from courier_router import cli, web
from courier_router.domain import GeoPoint, Operation, RouteSolution, RouteVisit
from courier_router.parsing import parse_operation, read_table
from courier_router.report import itinerary_text, route_json


@pytest.mark.parametrize("raw, expected", [
    ("Забор", Operation.PICKUP),
    ("Забрать", Operation.PICKUP),
    ("Заб.", Operation.PICKUP),
    ("Отвоз", Operation.DELIVERY),
    ("Отвезти", Operation.DELIVERY),
    ("Отв.", Operation.DELIVERY),
    ("Доставка", Operation.DELIVERY),
    ("Доставить", Operation.DELIVERY),
    ("Дост.", Operation.DELIVERY),
    ("Уточнить Забор", Operation.PICKUP),
    ("  УТОЧНИТЬ\xa0\tзАбОр  ", Operation.PICKUP),
    ("Уточнить:\nЗабор", Operation.PICKUP),
    ("Забор (уточнить)", Operation.PICKUP),
    ("«Уточнить — Забор»", Operation.PICKUP),
    ("\ufeffУточнить\u200b Забор", Operation.PICKUP),
    ("Уточнить Отвоз", Operation.DELIVERY),
    ("Доставка / уточнить", Operation.DELIVERY),
])
def test_normalize_operation(raw, expected):
    assert parse_operation(raw) is expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", 0, 123, "???", "Уточнить", "Перенос",
    "Уточнить адрес", "Заблокирован", "Отвергнуто", "Доставщик",
    "Не Забор", "Отмена Доставка", "Забор отменён", "Не уточнить Забор",
])
def test_unknown_operation_is_rejected(raw):
    with pytest.raises(ValueError, match="Неизвестный тип операции"):
        parse_operation(raw)


@pytest.mark.parametrize("raw", [
    "Забор / Отвоз", "Отвоз Забор", "Уточнить Забор или Доставка",
    "Забор / Дост.",
])
def test_conflicting_operations_are_rejected(raw):
    with pytest.raises(ValueError, match="Неоднозначный тип операции"):
        parse_operation(raw)


def _write_table(path, rows, encoding="utf-8"):
    if path.suffix == ".xlsx":
        wb = Workbook()
        for row in rows:
            wb.active.append(row)
        wb.save(path)
        wb.close()
    else:
        with path.open("w", encoding=encoding, newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)


@pytest.mark.parametrize("suffix, encoding", [
    (".xlsx", "utf-8"), (".csv", "utf-8-sig"), (".csv", "cp1251"),
])
@pytest.mark.parametrize("comment", ["", "Позвонить заранее"])
def test_table_with_clarification_keeps_all_five_rows(tmp_path, suffix, encoding, comment):
    # Shape and operations from the failing 2026-10-06 workbook, without personal data.
    operations = ["Отвоз", "Отвоз", "Забор", "Уточнить Забор", "Отвоз"]
    order_numbers = [425, 447, 471, 474, 447]
    rows = [
        [operation, order_no, "79000000000", "Центральный", f"Тестовая ул. д {i}",
         "2/4", "17:00 -20:30", "3300/перевод"]
        for i, (operation, order_no) in enumerate(zip(operations, order_numbers), 1)
    ]
    if comment:
        rows[3].append(comment)
    path = tmp_path / f"orders{suffix}"
    _write_table(path, rows, encoding)

    stops = read_table(path, default_service_min=12)

    assert [stop.source_row for stop in stops] == [1, 2, 3, 4, 5]
    assert [stop.order_no for stop in stops] == order_numbers
    assert [stop.operation for stop in stops] == [
        Operation.DELIVERY, Operation.DELIVERY, Operation.PICKUP,
        Operation.PICKUP, Operation.DELIVERY,
    ]
    expected_comment = "Тип операции в таблице: Уточнить Забор"
    if comment:
        expected_comment += f"; {comment}"
    assert stops[3].comment == expected_comment
    assert all(stop.comment == "" for stop in stops if stop.source_row != 4)
    for stop, row in zip(stops, rows):
        assert stop.address_raw == row[4]
        assert stop.access == row[5]
        assert stop.window.raw == "17:00-20:30"
        assert stop.payment.raw == row[7]
        assert stop.service_min == 12


@pytest.mark.parametrize("suffix", [".xlsx", ".csv"])
@pytest.mark.parametrize("operation, message", [
    ("Уточнить", "Неизвестный тип операции"),
    ("Забор / Отвоз", "Неоднозначный тип операции"),
])
def test_operation_error_identifies_original_row_and_column(tmp_path, suffix, operation, message):
    path = tmp_path / f"bad{suffix}"
    _write_table(path, [
        ["Отвоз", 1, "", "", "Тестовая ул., д 1"],
        [None] * 8,
        [operation, 2, "", "", "Тестовая ул., д 2"],
    ])

    with pytest.raises(ValueError, match=f"Строка 3, колонка A: {message}"):
        read_table(path)


def test_clarification_is_visible_in_reports_web_and_manual_edit(tmp_path):
    path = tmp_path / "orders.xlsx"
    _write_table(path, [[
        "Уточнить: Забор", 474, "", "", "Тестовая ул., д 1", "", "", "",
        "Позвонить заранее",
    ]])
    stops = read_table(path)
    stops[0].geo = GeoPoint(59.94, 30.32, "stub", "Тестовая ул., д 1")
    solution = RouteSolution([RouteVisit(0, 600, 610, 0, 0)], 0, 0, 600, 0)

    route = route_json(stops, solution, [(59.94, 30.32)])
    comment = "Тип операции в таблице: Уточнить: Забор; Позвонить заранее"
    assert route["visits"][0]["stop"]["comment"] == comment
    assert comment in itinerary_text("2026-10-06", stops, solution, "База", 600, "open")
    assert comment in web._route_page("test", "test001", {}, route)
    assert cli._stop_from_json(route["visits"][0]["stop"], 10).comment == comment
