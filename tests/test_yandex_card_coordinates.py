from courier_router import yandex_maps as ym
from courier_router.errors import GeocodingError
import pytest


def test_html_coordinates_are_not_used_when_card_has_no_coordinates():
    class Page:
        def wait_for_selector(self, *args, **kwargs):
            raise TimeoutError("No card coordinates")
        def content(self):
            raise AssertionError("Must not scrape unrelated page coordinates")
    with pytest.raises(GeocodingError) as caught:
        ym._coords_from_card(Page())
    assert caught.value.code == "timeout"


def test_visible_card_coordinates_use_latitude_longitude_order():
    class Element:
        def inner_text(self):
            return "59.923456, 30.312345"
    class Page:
        def wait_for_selector(self, *args, **kwargs):
            return Element()
    assert ym._coords_from_card(Page()) == (59.923456, 30.312345)
