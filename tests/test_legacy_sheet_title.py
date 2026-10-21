import csv

import pytest
from openpyxl import Workbook

from courier_router.parsing import read_table


TITLE = ["АСРОР понедельник 14.09.2026"]
ORDER = ["Забор", 123, "", "Невский", "Санкт-Петербург, ул Тестовая, д 1", "", "10:00-12:00", ""]


def write_table(path, rows):
    if path.suffix == ".xlsx":
        workbook = Workbook()
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)


@pytest.mark.parametrize("suffix", [".xlsx", ".csv"])
def test_dated_legacy_title_preserves_orders_and_original_rows(tmp_path, suffix):
    path = tmp_path / f"orders{suffix}"
    second = ORDER.copy()
    second[0:2] = ["Отвоз", 124]
    write_table(path, [TITLE, ORDER, [], second])

    stops = read_table(path)

    assert [stop.source_row for stop in stops] == [2, 4]
    assert [stop.order_no for stop in stops] == [123, 124]
    assert [stop.operation.value for stop in stops] == ["pickup", "delivery"]
    assert all(stop.address_raw == ORDER[4] for stop in stops)


def test_bad_operation_after_title_keeps_original_error_location(tmp_path):
    path = tmp_path / "bad.xlsx"
    bad = ORDER.copy()
    bad[0] = "Неизвестно"
    write_table(path, [TITLE, ORDER, [], bad])

    with pytest.raises(ValueError, match="Строка 4, колонка A: Неизвестный тип операции"):
        read_table(path)


@pytest.mark.parametrize("first", [
    ["АСРОР понедельник 31.02.2026"],
    ["Неизвестный заголовок"],
    [TITLE[0], 125, "", "", ORDER[4]],
    [TITLE[0], None, None, None, None, None, None, None, None, "Данные за пределами A-I"],
])
def test_title_detection_never_hides_unknown_or_populated_rows(tmp_path, first):
    path = tmp_path / "bad.xlsx"
    write_table(path, [first, ORDER])

    with pytest.raises(ValueError, match="Строка 1, колонка A: Неизвестный тип операции"):
        read_table(path)


@pytest.mark.parametrize("rows", [[ORDER, TITLE], [TITLE, TITLE, ORDER]])
def test_title_is_allowed_only_once_before_orders(tmp_path, rows):
    path = tmp_path / "bad.xlsx"
    write_table(path, rows)

    with pytest.raises(ValueError, match="Строка 2, колонка A: Неизвестный тип операции"):
        read_table(path)


def test_title_without_orders_is_not_a_route(tmp_path):
    path = tmp_path / "empty.xlsx"
    write_table(path, [TITLE])

    with pytest.raises(ValueError, match="не найдено ни одной точки"):
        read_table(path)
