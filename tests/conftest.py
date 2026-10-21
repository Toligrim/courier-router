"""Small GAR-format fixtures; the test suite never downloads address data."""
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest


@pytest.fixture
def gar_factory(tmp_path):
    def make(version="20261020", *, rename=None, omit=None, broken_parent=False):
        rows = [
            (1, "47", "Ленинградская", "обл", 1, 0),
            (2, "47", "Всеволожск", "г", 5, 1),
            (3, "47", "Гатчина", "г", 5, 1),
            (10, "78", "Санкт-Петербург", "г", 1, 0),
            (11, "78", "Пушкин", "г", 5, 10),
            (100, "78", "Гороховая", "ул", 8, 10),
            (101, "78", "Комендантский", "пр-кт", 8, 10),
            (102, "78", "Ленина", "ул", 8, 10),
            (103, "78", "Ленина", "пр-кт", 8, 10),
            (104, "78", "15-я Васильевского острова", "линия", 8, 10),
            (105, "78", "Большая Морская", "ул", 8, 10),
            (106, "78", "Малая Морская", "ул", 8, 10),
            (107, "78", "Ленина", "ул", 8, 11),
            (200, "47", "Советская", "ул", 8, 2),
            (201, "47", "Советская", "ул", 8, 3),
            (202, "47", "Гороховая", "ул", 8, 2),
        ]
        path = tmp_path / f"gar-{version}.zip"
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for region in ("47", "78"):
                objects = ET.Element("ADDRESSOBJECTS")
                hierarchy = ET.Element("ITEMS")
                for oid, r, name, kind, level, parent in rows:
                    if r != region:
                        continue
                    name = (rename or {}).get(oid, name)
                    ET.SubElement(objects, "OBJECT", ID=str(oid), OBJECTID=str(oid), OBJECTGUID=f"guid-{oid}",
                                  NAME=name, TYPENAME=kind, LEVEL=str(level), ISACTIVE="1", ISACTUAL="1")
                    ET.SubElement(hierarchy, "ITEM", ID=str(oid), OBJECTID=str(oid),
                                  PARENTOBJID=str(999999 if broken_parent and oid == 100 else parent), ISACTIVE="1")
                # Old rows must not compete with the active record regardless of file order.
                ET.SubElement(objects, "OBJECT", ID="9999", OBJECTID="100", OBJECTGUID="guid-100",
                              NAME="Горохвая", TYPENAME="ул", LEVEL="8", ISACTIVE="0", ISACTUAL="0")
                for kind, tree in (("AS_ADDR_OBJ", objects), ("AS_ADM_HIERARCHY", hierarchy)):
                    if omit == (region, kind):
                        continue
                    archive.writestr(f"{region}/{kind}_{version}_test.XML", ET.tostring(tree, encoding="utf-8"))
        return path
    return make


@pytest.fixture
def dictionary_root(tmp_path, gar_factory):
    from courier_router.fns_update import update
    root = tmp_path / "dictionary"
    update(root, source=gar_factory(), version="20261020", min_streets=1)
    return root
