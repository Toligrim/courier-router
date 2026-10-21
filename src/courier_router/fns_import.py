"""Build a compact street dictionary from complete GAR XML files for regions 47 and 78."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

from .address_dictionary import AddressDictionary, SCHEMA_VERSION, street_type, text_key

REGIONS = {"47", "78"}


def xml_rows(archive, member):
    """Stream even the house-heavy hierarchy files without retaining the XML tree."""
    with archive.open(member) as stream:
        iterator = ET.iterparse(stream, events=("start", "end"))
        _, root = next(iterator)
        for event, element in iterator:
            if event == "end" and element is not root:
                yield dict(element.attrib)
                element.clear()
                root.clear()


def build_dictionary(archive, output: Path, version: str) -> dict:
    files = {}
    for member in archive.infolist():
        match = re.fullmatch(r"(47|78)/(AS_ADDR_OBJ|AS_ADM_HIERARCHY)_\d[^/]*\.xml", member.filename, re.I)
        if match:
            key = (match[1], match[2].upper())
            if key in files:
                raise ValueError(f"Повторяющийся файл ГАР: {key}")
            if member.file_size > 4 * 1024**3 or member.compress_size > 512 * 1024**2:
                raise ValueError(f"Неожиданно большой файл ГАР: {member.filename}")
            files[key] = member
    expected = {(region, kind) for region in REGIONS for kind in ("AS_ADDR_OBJ", "AS_ADM_HIERARCHY")}
    if set(files) != expected:
        raise ValueError("Полная выгрузка должна содержать объекты и иерархию регионов 47 и 78")
    objects, parents = {}, {}
    for region in sorted(REGIONS):
        print(f"  регион {region}: адресные объекты", flush=True)
        for row in xml_rows(archive, files[region, "AS_ADDR_OBJ"]):
            if row.get("ISACTIVE") != "1" or row.get("ISACTUAL") != "1":
                continue
            oid = int(row["OBJECTID"])
            if oid in objects:
                raise ValueError(f"Две актуальные записи ГАР для объекта {oid}")
            objects[oid] = {"name": row["NAME"], "type": row["TYPENAME"],
                            "level": int(row["LEVEL"]), "region": region, "guid": row["OBJECTGUID"]}
        print(f"  регион {region}: иерархия", flush=True)
        for row in xml_rows(archive, files[region, "AS_ADM_HIERARCHY"]):
            if row.get("ISACTIVE") != "1":
                continue
            oid = int(row["OBJECTID"])
            if oid not in objects:
                continue
            parent = int(row.get("PARENTOBJID") or "0")
            if oid in parents and parents[oid] != parent:
                raise ValueError(f"Несколько актуальных родителей у объекта {oid}")
            parents[oid] = parent

    streets, rejected = [], Counter()
    for oid, row in objects.items():
        if row["level"] != 8:
            continue
        context, seen, cursor, valid = [], {oid}, parents.get(oid), False
        while cursor and len(seen) < 32:
            ancestor = objects.get(cursor)
            if cursor in seen or ancestor is None or ancestor["region"] != row["region"]:
                break
            seen.add(cursor)
            context.append(text_key(ancestor["name"]))
            if ancestor["level"] == 1:
                valid = True
                break
            cursor = parents.get(cursor)
        if not valid:
            rejected[row["region"]] += 1
            continue
        streets.append((oid, row["guid"], row["name"], street_type(row["type"]), row["region"],
                        json.dumps(sorted(set(context)), ensure_ascii=False)))
    counts = Counter(row[4] for row in streets)
    for region in REGIONS:
        if rejected[region] > max(5, counts[region] * 0.02):
            raise ValueError(f"Слишком много улиц с неполной иерархией: регион {region}, {rejected[region]}")
    with closing(sqlite3.connect(output)) as con:
        con.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE streets(object_id INTEGER PRIMARY KEY, guid TEXT NOT NULL, name TEXT NOT NULL,
                                 kind TEXT NOT NULL, region TEXT NOT NULL, contexts TEXT NOT NULL);
            CREATE INDEX streets_region_kind ON streets(region,kind);
        """)
        con.executemany("INSERT INTO metadata VALUES(?,?)", [("version", str(version)), ("schema_version", str(SCHEMA_VERSION))])
        con.executemany("INSERT INTO streets VALUES(?,?,?,?,?,?)", streets)
        con.commit()
    return {"counts": dict(counts), "excluded_orphans": dict(rejected),
            "members": [{"name": m.filename, "crc32": m.CRC, "bytes": m.file_size,
                         "compressed_bytes": m.compress_size} for m in files.values()]}


def validate_dictionary(database: Path, previous: dict | None = None, *, min_streets=500) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Проверка целостности SQLite не пройдена")
        counts = dict(con.execute("SELECT region,COUNT(*) FROM streets GROUP BY region"))
        if set(counts) != REGIONS or any(n < min_streets for n in counts.values()):
            raise ValueError("В справочнике недостаточно улиц СПб/Ленобласти")
        if con.execute("SELECT COUNT(*) FROM streets WHERE name='' OR contexts='[]'").fetchone()[0]:
            raise ValueError("В справочнике есть улицы без названия или территории")
    if previous:
        for region, count in counts.items():
            old_count = previous.get("counts", {}).get(region, 0)
            if old_count and not 0.8 <= count / old_count <= 1.25:
                raise ValueError(f"Необычное изменение количества улиц региона {region}")
    snapshot = AddressDictionary(database)
    return {"counts": counts, "version": snapshot.version}


def validate_canaries(database: Path) -> None:
    """Production smoke cases, validated on the first real import as well as every update."""
    snapshot = AddressDictionary(database)
    checks = [
        ("Санкт-Петербург, ул Гороховая, д 68", "exact"),
        ("Санкт-Петербург, ул Горохвая, д 68", "corrected"),
        ("Санкт-Петербург, проспект Комендантский, д 53 к 1", "exact"),
    ]
    for query, expected in checks:
        if snapshot.normalize(query).status != expected:
            raise ValueError(f"Контрольный адрес не прошёл проверку: {query}")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
