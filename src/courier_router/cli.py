from __future__ import annotations
import argparse, json, os, platform, sys
from pathlib import Path
from .config import Config
from .domain import GeoPoint
from .geocode import DaDataGeocoder, PublicNominatimGeocoder, RESOLVER_VERSION
from .llm import clean_with_openai, clean_with_anthropic
from .optimizer import solve_single_vehicle
from .parsing import read_table
from .render import render_map
from .report import itinerary_text, route_json
from .routing import ORSRouter, OSRMRouter
from .storage import Storage


def minutes(s: str) -> int:
    h,m = s.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Некорректное время старта: {s!r}")
    return h*60 + m


def get_geocoder(c: Config):
    if c.geocoder == "dadata":
        return DaDataGeocoder(c.dadata_token, c.dadata_secret, use_clean=c.dadata_use_clean)
    if c.geocoder == "nominatim":
        return PublicNominatimGeocoder(c.tile_user_agent)
    raise RuntimeError(f"Неизвестный GEOCODER={c.geocoder}")


def get_router(c: Config):
    if c.router == "ors":
        return ORSRouter(c.ors_api_key)
    if c.router == "osrm":
        return OSRMRouter(c.osrm_url)
    raise RuntimeError(f"Неизвестный ROUTER={c.router}")


def maybe_llm_clean(c: Config, address: str, district: str) -> str:
    if c.llm_provider == "openai":
        if not c.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai, но OPENAI_API_KEY пуст")
        return clean_with_openai(c.openai_api_key, c.openai_model, address, district)
    if c.llm_provider == "anthropic":
        if not c.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic, но ANTHROPIC_API_KEY пуст")
        return clean_with_anthropic(c.anthropic_api_key, c.anthropic_model, address, district)
    return address


def depot(c: Config, geocoder=None):
    if c.depot_lat and c.depot_lon:
        return GeoPoint(float(c.depot_lat), float(c.depot_lon), "config", c.depot_address, "verified", 1.0)
    if geocoder is None:
        geocoder = get_geocoder(c)
    return geocoder.geocode(c.depot_address, "")


def cmd_doctor(args):
    c = Config()
    store = Storage(c.db_path)
    print(f"platform={platform.platform()}")
    print(f"machine={platform.machine()}")
    print(f"python={sys.version.split()[0]}")
    try:
        import ortools
        print(f"ortools={ortools.__version__}")
    except Exception as e:
        print(f"ortools=ERROR {e}")
    print(f"sqlite={store.path} OK")
    print(f"geocoder={c.geocoder} {'configured' if (c.dadata_token or c.geocoder!='dadata') else 'MISSING KEY'}")
    print(f"router={c.router} {'configured' if (c.ors_api_key or c.router!='ors') else 'MISSING KEY'}")
    print(f"llm={c.llm_provider}")
    return 0


def cmd_geocode_depot(args):
    c = Config()
    g = get_geocoder(c)
    d = depot(c, g)
    print(json.dumps({
        "address": d.normalized_address, "lat": d.lat, "lon": d.lon,
        "provider": d.provider, "precision": d.precision, "confidence": d.confidence
    }, ensure_ascii=False, indent=2))


def _resolver_meta(geo) -> dict:
    raw = getattr(geo, "raw", None)
    if not isinstance(raw, dict):
        return {}
    value = raw.get("_resolver")
    return value if isinstance(value, dict) else {}


def _cache_matches_geocoder(c, cached) -> bool:
    if not cached:
        return False
    provider = str(getattr(cached, "provider", ""))
    if c.geocoder == "dadata":
        resolver = _resolver_meta(cached)
        return provider in {"dadata_suggest", "dadata_clean"} and resolver.get("version") == RESOLVER_VERSION
    return provider == c.geocoder


def geocode_stops(c, stops, store, allow_low_confidence=False):
    g = get_geocoder(c)
    report = []
    for s in stops:
        cached = store.get_geocode(s.address_raw, s.district)
        cache_valid = _cache_matches_geocoder(c, cached)
        if cached and cache_valid:
            s.geo = cached
            source = "cache"
        else:
            try:
                s.geo = g.geocode(s.address_raw, s.district)
                source = c.geocoder if not cached else f"{c.geocoder}:cache-refresh"
            except Exception as first:
                if c.llm_provider == "none":
                    raise RuntimeError(f"Строка {s.source_row}, адрес {s.address_raw!r}: {first}") from first
                cleaned = maybe_llm_clean(c, s.address_raw, s.district)
                s.geo = g.geocode(cleaned, s.district)
                s.warnings.append("Адрес потребовал LLM-нормализацию")
                source = f"{c.geocoder}+{c.llm_provider}"

        resolver = _resolver_meta(s.geo)
        resolver_status = resolver.get("status")
        if resolver_status == "strong_mismatch":
            candidate_values = [x.get("value") for x in resolver.get("candidates", []) if x.get("value")]
            hint = f" Лучшие варианты: {'; '.join(candidate_values[:3])}" if candidate_values else ""
            s.warnings.append("Сильное расхождение исходного и найденного адреса")
            if not allow_low_confidence:
                raise RuntimeError(
                    f"Строка {s.source_row}: найденный адрес сильно расходится с исходным. "
                    f"Исходный: {s.address_raw!r}. Найдено: {s.geo.normalized_address!r}.{hint}"
                )

        outside_expected_area = not (58.2 <= s.geo.lat <= 61.7 and 26.5 <= s.geo.lon <= 36.5)
        if outside_expected_area:
            s.warnings.append("Координата находится вне ожидаемой зоны СПб/Ленобласти")
            if not allow_low_confidence:
                raise RuntimeError(
                    f"Строка {s.source_row}: геокодер вернул точку вне СПб/Ленобласти: "
                    f"{s.geo.lat}, {s.geo.lon}"
                )

        requires_review = s.geo.confidence < 0.80 or resolver_status in {"review", "strong_mismatch"}
        if requires_review:
            s.warnings.append(
                f"Геокодирование требует проверки ({s.geo.confidence:.2f}, {s.geo.precision}) — "
                "проверьте точку на карте"
            )

        # Cache only after all blocking validation has passed. This prevents a failed
        # route build from poisoning subsequent runs with the rejected coordinate.
        if not cache_valid:
            store.put_geocode(s.address_raw, s.district, s.geo)

        report.append({
            "row": s.source_row, "order_no": s.order_no, "raw": s.address_raw,
            "normalized": s.geo.normalized_address, "lat": s.geo.lat, "lon": s.geo.lon,
            "precision": s.geo.precision, "confidence": s.geo.confidence, "source": source,
            "requires_review": requires_review,
            "outside_expected_area": outside_expected_area,
            "resolver_status": resolver_status,
            "resolver_version": resolver.get("version"),
            "resolver_score": resolver.get("score"),
            "resolver_reasons": resolver.get("reasons", []),
            "resolver_query": resolver.get("query"),
            "resolver_candidates": resolver.get("candidates", []),
            "dadata_lat": s.geo.lat, "dadata_lon": s.geo.lon,
            "yandex_lat": None, "yandex_lon": None, "delta_m": None,
            "coord_source": "dadata",
        })

    if getattr(c, "yandex_crosscheck", False):
        _yandex_crosscheck(c, stops, report, store)
    return report


_YANDEX_CACHE_SCHEMA = """CREATE TABLE IF NOT EXISTS yandex_maps_cache (
  cache_key TEXT PRIMARY KEY, lat REAL, lon REAL, title TEXT, subtitle TEXT,
  url TEXT, is_house INTEGER, found INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"""


def _yandex_query(raw: str) -> str:
    """Строка для поиска на Яндекс Картах из сырого адреса Excel (без квартиры)."""
    import re
    q = re.sub(r",?\s*кв\.?\s*[0-9А-Яа-я/\-]+\s*$", "", raw or "").strip(" ,")
    q = re.sub(r"^\s*г\s+", "", q)
    return q


def _yandex_lookup_cached(c, store, jobs):
    """jobs: [(key, query, (near_lat, near_lon))] -> {key: YResult|None}. Кэш в SQLite."""
    from . import yandex_maps as ym
    store.con.executescript(_YANDEX_CACHE_SCHEMA)
    result, todo = {}, []
    for key, query, near in jobs:
        ck = " ".join(query.lower().replace("ё", "е").split())
        row = store.con.execute(
            "SELECT lat,lon,title,subtitle,url,is_house,found FROM yandex_maps_cache WHERE cache_key=?",
            (ck,)).fetchone()
        if row is None:
            todo.append((key, query, near, ck))
        elif not row[6]:
            result[key] = None
        else:
            result[key] = ym.YResult(row[0], row[1], row[2] or "", row[3] or "", row[4] or "", bool(row[5]))
    if todo:
        fresh = ym.lookup_batch([(k, q, n) for k, q, n, _ in todo])
        for key, query, near, ck in todo:
            r = fresh.get(key)
            if r is None:
                store.con.execute(
                    "INSERT INTO yandex_maps_cache(cache_key,found) VALUES(?,0) "
                    "ON CONFLICT(cache_key) DO UPDATE SET found=0, updated_at=CURRENT_TIMESTAMP", (ck,))
            else:
                store.con.execute(
                    "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) "
                    "VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(cache_key) DO UPDATE SET lat=excluded.lat,"
                    "lon=excluded.lon,title=excluded.title,subtitle=excluded.subtitle,url=excluded.url,"
                    "is_house=excluded.is_house,found=1,updated_at=CURRENT_TIMESTAMP",
                    (ck, r.lat, r.lon, r.title, r.subtitle, r.url, int(r.is_house)))
            result[key] = r
        store.con.commit()
    return result


def _yandex_crosscheck(c, stops, report, store):
    """Второй источник координат — Яндекс Карты через headless-браузер.

    Для каждой точки: расхождение с DaData считается по haversine. Если оно больше
    порога и Яндекс дал дом — координаты берутся с Яндекса (он в этой задаче точнее),
    точка помечается на сверку, но маршрут всё равно строится.
    """
    from . import yandex_maps as ym
    from .navlinks import haversine_m

    jobs = [(str(s.source_row), _yandex_query(s.address_raw), (s.geo.lat, s.geo.lon)) for s in stops]
    try:
        found = _yandex_lookup_cached(c, store, jobs)
    except ym.Unavailable as e:
        for s in stops:
            s.warnings.append("Сверка с Яндекс Картами недоступна (нет playwright/chromium)")
        for r in report:
            r["coord_source"] = "dadata"
        print(f"  !! Яндекс-сверка недоступна: {e}", file=sys.stderr)
        return

    warn_m = c.yandex_xcheck_warn_m
    for s, r in zip(stops, report):
        y = found.get(str(s.source_row))
        if y is None:
            s.warnings.append("Второй источник (Яндекс Карты) не нашёл этот адрес — координата от DaData")
            continue
        in_area = 58.2 <= y.lat <= 61.7 and 26.5 <= y.lon <= 36.5
        delta = haversine_m((s.geo.lat, s.geo.lon), (y.lat, y.lon))
        r["yandex_lat"], r["yandex_lon"], r["delta_m"] = round(y.lat, 6), round(y.lon, 6), round(delta, 1)
        if not in_area:
            s.warnings.append(f"Яндекс Карты вернули точку вне СПб/Ленобласти — оставлена координата DaData (Δ {delta:.0f} м)")
            continue
        if delta <= warn_m:
            continue
        if not y.is_house:
            s.warnings.append(
                f"DaData↔Яндекс расходятся на {delta:.0f} м, но Яндекс дал не дом ({y.title or 'топоним'}) — "
                "координата от DaData, сверьте адрес вручную")
            r["requires_review"] = True
            continue
        # Яндекс точнее: берём его координаты, помечаем на сверку, маршрут строим.
        s.geo.lat, s.geo.lon = y.lat, y.lon
        r["lat"], r["lon"], r["coord_source"] = y.lat, y.lon, "yandex_crosscheck"
        r["requires_review"] = True
        if y.subtitle:
            r["normalized"] = y.subtitle
        s.warnings.append(
            f"DaData↔Яндекс расходятся на {delta:.0f} м — взяты координаты Яндекс Карт "
            f"({y.title or 'дом'}), сверьте адрес")
        print(f"  ↳ строка {s.source_row}: Δ {delta:.0f} м → координаты Яндекса {y.lat:.6f},{y.lon:.6f}")


def cmd_plan(args):
    c = Config()
    store = Storage(c.db_path)
    stops = read_table(args.xlsx, c.default_service_min)
    report = geocode_stops(c, stops, store, allow_low_confidence=args.allow_low_confidence)
    g = get_geocoder(c)
    d = depot(c, g)

    coords = [(d.lat,d.lon)] + [(s.geo.lat,s.geo.lon) for s in stops]
    router = get_router(c)
    durations, distances = router.matrix(coords)
    sol = solve_single_vehicle(
        stops, durations, distances, minutes(args.depart),
        end_mode=args.end, time_limit_sec=c.solver_time_limit_sec
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out/"geocoding-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not sol.feasible:
        (out/"route.json").write_text(json.dumps({"feasible":False,"warnings":sol.warnings}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError("Solver не нашёл допустимый маршрут. См. route.json")

    ordered_stops = [stops[v.stop_index] for v in sol.visits]
    route_coords = [(d.lat,d.lon)] + [(s.geo.lat,s.geo.lon) for s in ordered_stops]
    if args.end == "depot":
        route_coords.append((d.lat,d.lon))
    geometry = router.geometry(route_coords)

    text = itinerary_text(args.date, stops, sol, d.normalized_address, minutes(args.depart), args.end)
    (out/"itinerary.txt").write_text(text, encoding="utf-8")
    (out/"route.json").write_text(json.dumps(route_json(stops,sol,geometry), ensure_ascii=False, indent=2), encoding="utf-8")

    markers = [(s.geo.lat,s.geo.lon,s.operation.value) for s in ordered_stops]
    render_map(out/"route.png", (d.lat,d.lon), markers, geometry, c.tile_url, c.tile_user_agent)

    print(text)
    print(f"\nГотово: {out.resolve()}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="courier-route")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="Проверить окружение")
    d.set_defaults(func=cmd_doctor)
    gd = sub.add_parser("geocode-depot", help="Геокодировать фиксированную базу")
    gd.set_defaults(func=cmd_geocode_depot)
    plan = sub.add_parser("plan", help="Построить маршрут из XLSX/CSV")
    plan.add_argument("xlsx", help="Таблица .xlsx или .csv")
    plan.add_argument("--date", required=True)
    plan.add_argument("--depart", default=os.getenv("DEPART_TIME","10:00"))
    plan.add_argument("--end", choices=["depot","open"], default=os.getenv("END_MODE","depot"))
    plan.add_argument("--output", required=True)
    plan.add_argument(
        "--allow-low-confidence", action="store_true",
        help="Явно разрешить сильные расхождения адресов и координаты вне ожидаемой зоны",
    )
    plan.set_defaults(func=cmd_plan)
    return p


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
