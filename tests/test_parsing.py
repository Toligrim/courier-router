from courier_router.parsing import parse_operation, parse_payment, parse_window

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
