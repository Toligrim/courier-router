import pytest

from courier_router.parsing import (
    normalize_phone,
    parse_operation,
    parse_payment,
    parse_window,
    phone_dial_digits,
    read_table,
)


@pytest.mark.parametrize("raw, expected", [
    ("7 (921) 189-16-57", "8 (921) 189-16-57"),
    ("79213245712", "8 (921) 324-57-12"),
    ("+7 (981) 685-08-81", "8 (981) 685-08-81"),
    ("7 921 946 15 67", "8 (921) 946-15-67"),
    ("8 (921) 189-16-57", "8 (921) 189-16-57"),
    ("9211234567", "8 (921) 123-45-67"),
    ("79213245712.0", "8 (921) 324-57-12"),
    ("7 (911) 901-99-03 / 7 (911) 836-53-73", "8 (911) 901-99-03 / 8 (911) 836-53-73"),
    ("", ""),
    ("нет", "нет"),
])
def test_normalize_phone_to_leading_eight(raw, expected):
    assert normalize_phone(raw) == expected


def test_phone_dial_digits_is_plain_eight_number():
    assert phone_dial_digits(normalize_phone("+7 (921) 189-16-57")) == "89211891657"
    assert phone_dial_digits(normalize_phone("7 (911) 901-99-03 / 7 (911) 836-53-73")) == "89119019903"
    assert phone_dial_digits("") == ""


def test_window():
    w = parse_window("17:00-21:00")
    assert w.start_min == 17*60
    assert w.end_min == 21*60


def test_window_rejects_invalid_clock_values():
    with pytest.raises(ValueError):
        parse_window("12:60-14:00")
    with pytest.raises(ValueError):
        parse_window("25:00-26:00")
    with pytest.raises(ValueError):
        parse_window("23:00-24:30")
    assert parse_window("23:00-24:00").end_min == 24 * 60


def test_payment():
    p = parse_payment("3300/ наличка")
    assert p.amount_rub == 3300
    assert p.method == "cash"
    assert parse_payment("Бесплатно").amount_rub == 0
    assert parse_payment("3300 безнал").method == "transfer"


def test_operations():
    assert parse_operation("Забор").value == "pickup"
    assert parse_operation("Отвоз").value == "delivery"


def test_csv_table(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(
        "Отвоз;18452;+79990000000;Приморский;Савушкина 15;2/4;10:00-12:00;3300 наличка;Позвонить\n"
        "Забор;18477;+79991111111;Центральный;Невский 80;;11:00-15:00;Бесплатно;\n",
        encoding="utf-8",
    )
    stops = read_table(path)
    assert len(stops) == 2
    assert stops[0].order_no == 18452
    assert stops[0].address_raw == "Савушкина 15"
    assert stops[0].window.start_min == 10 * 60
    assert stops[1].operation.value == "pickup"
