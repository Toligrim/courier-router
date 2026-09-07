from courier_router.parsing import parse_operation, parse_payment, parse_window, read_table


def test_window():
    w = parse_window("17:00-21:00")
    assert w.start_min == 17*60
    assert w.end_min == 21*60


def test_payment():
    p = parse_payment("3300/ наличка")
    assert p.amount_rub == 3300
    assert p.method == "cash"
    assert parse_payment("Бесплатно").amount_rub == 0


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
