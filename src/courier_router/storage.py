from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .domain import GeoPoint

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS geocode_cache (
  cache_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  normalized_address TEXT NOT NULL,
  precision TEXT NOT NULL,
  confidence REAL NOT NULL,
  provider_ref TEXT,
  raw_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS address_aliases (
  alias TEXT PRIMARY KEY,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  normalized_address TEXT NOT NULL,
  note TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS route_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  input_path TEXT NOT NULL,
  router TEXT NOT NULL,
  geocoder TEXT NOT NULL,
  output_dir TEXT NOT NULL
);
"""

class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.executescript(SCHEMA)

    @staticmethod
    def key(address: str, district: str = "") -> str:
        return " ".join(f"{district} {address}".lower().replace("ё", "е").split())

    def get_geocode(self, address: str, district: str = "") -> GeoPoint | None:
        key = self.key(address, district)
        row = self.con.execute(
            "SELECT provider,lat,lon,normalized_address,precision,confidence,provider_ref,raw_json "
            "FROM geocode_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if not row:
            return None
        return GeoPoint(
            provider=row[0], lat=row[1], lon=row[2], normalized_address=row[3],
            precision=row[4], confidence=row[5], provider_ref=row[6],
            raw=json.loads(row[7])
        )

    def get_alias(self, address: str, district: str = "") -> GeoPoint | None:
        row = self.con.execute(
            "SELECT lat,lon,normalized_address,note FROM address_aliases WHERE alias=?",
            (self.key(address, district),),
        ).fetchone()
        if not row:
            return None
        return GeoPoint(
            lat=row[0], lon=row[1], provider="alias",
            normalized_address=row[2] or address, precision="verified", confidence=1.0,
            provider_ref=None, raw={"note": row[3] or "", "_resolver": {"status": "alias"}},
        )

    def put_alias(self, address: str, district: str, lat: float, lon: float,
                  normalized_address: str = "", note: str = "") -> None:
        self.con.execute(
            """INSERT INTO address_aliases(alias,lat,lon,normalized_address,note)
               VALUES(?,?,?,?,?)
               ON CONFLICT(alias) DO UPDATE SET lat=excluded.lat, lon=excluded.lon,
               normalized_address=excluded.normalized_address, note=excluded.note,
               updated_at=CURRENT_TIMESTAMP""",
            (self.key(address, district), lat, lon, normalized_address or address, note),
        )
        self.con.commit()

    def delete_alias(self, address: str, district: str = "") -> int:
        cur = self.con.execute("DELETE FROM address_aliases WHERE alias=?",
                               (self.key(address, district),))
        self.con.commit()
        return cur.rowcount

    def list_aliases(self) -> list[tuple]:
        return self.con.execute(
            "SELECT alias,lat,lon,normalized_address,note,updated_at "
            "FROM address_aliases ORDER BY updated_at DESC").fetchall()

    def put_geocode(self, address: str, district: str, geo: GeoPoint):
        key = self.key(address, district)
        self.con.execute(
            """INSERT INTO geocode_cache(cache_key,provider,lat,lon,normalized_address,precision,
               confidence,provider_ref,raw_json) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET provider=excluded.provider, lat=excluded.lat,
               lon=excluded.lon, normalized_address=excluded.normalized_address,
               precision=excluded.precision, confidence=excluded.confidence,
               provider_ref=excluded.provider_ref, raw_json=excluded.raw_json,
               updated_at=CURRENT_TIMESTAMP""",
            (key, geo.provider, geo.lat, geo.lon, geo.normalized_address, geo.precision,
             geo.confidence, geo.provider_ref, json.dumps(geo.raw, ensure_ascii=False))
        )
        self.con.commit()
