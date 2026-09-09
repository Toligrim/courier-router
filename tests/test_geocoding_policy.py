from types import SimpleNamespace

import courier_router.cli as cli


class StubStore:
    def get_geocode(self, address, district):
        return None

    def put_geocode(self, address, district, geo):
        pass


class StubGeocoder:
    def __init__(self, geo):
        self.geo = geo

    def geocode(self, address, district):
        return self.geo


def stop():
    return SimpleNamespace(
        address_raw="тестовый адрес",
        district="",
        source_row=2,
        order_no=270,
        warnings=[],
        geo=None,
    )


def config():
    return SimpleNamespace(llm_provider="none", geocoder="stub")


def test_low_confidence_is_warning_not_failure(monkeypatch):
    geo = SimpleNamespace(
        lat=59.9,
        lon=30.3,
        normalized_address="Санкт-Петербург, тестовый адрес",
        precision="street",
        confidence=0.72,
    )
    monkeypatch.setattr(cli, "get_geocoder", lambda c: StubGeocoder(geo))
    s = stop()

    report = cli.geocode_stops(config(), [s], StubStore(), allow_low_confidence=False)

    assert report[0]["requires_review"] is True
    assert report[0]["outside_expected_area"] is False
    assert s.coord_status == "review" and "улиц" in s.coord_note.lower()
    assert s.warnings == []


def test_outside_expected_area_still_blocks(monkeypatch):
    geo = SimpleNamespace(
        lat=55.75,
        lon=37.62,
        normalized_address="Москва",
        precision="city",
        confidence=0.99,
    )
    monkeypatch.setattr(cli, "get_geocoder", lambda c: StubGeocoder(geo))

    try:
        cli.geocode_stops(config(), [stop()], StubStore(), allow_low_confidence=False)
    except RuntimeError as exc:
        assert "вне СПб/Ленобласти" in str(exc)
    else:
        raise AssertionError("outside-area geocode must remain blocking")


def test_outside_expected_area_can_be_explicitly_allowed(monkeypatch):
    geo = SimpleNamespace(
        lat=55.75,
        lon=37.62,
        normalized_address="Москва",
        precision="city",
        confidence=0.72,
    )
    monkeypatch.setattr(cli, "get_geocoder", lambda c: StubGeocoder(geo))
    s = stop()

    report = cli.geocode_stops(config(), [s], StubStore(), allow_low_confidence=True)

    assert report[0]["requires_review"] is True
    assert report[0]["outside_expected_area"] is True
