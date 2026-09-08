from __future__ import annotations
import argparse, json, os, platform, sys
from pathlib import Path
from .config import Config
from .domain import GeoPoint
from .geocode import DaDataGeocoder, PublicNominatimGeocoder
from .llm import clean_with_openai, clean_with_anthropic
from .optimizer import solve_single_vehicle
from .parsing import read_table
from .render import render_map
from .report import itinerary_text, route_json
from .routing import ORSRouter, OSRMRouter
from .storage import Storage


def minutes(s: str) -> int:
    h,m = s.split(":")
    return int(h)*60 + int(m)


def get_geocoder(c: Config):
    if c.geocoder == "dadata":
        return DaDataGeocoder(c.dadata_token, c.dadata_secret)
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


def _resolver_meta(geo: GeoPoint) -> dict:
    if not isinstance(geo.raw, dict):
        return {}
    value = geo.raw.get("_resolver")
    return value if isinstance(value, dict) else {}


def geocode_stops(c, stops, store, allow_low_confidence=False):
    g = get_geocoder(c)
    report = []
    for s in stops:
        cached = store.get_geocode(s.address_raw, s.district)
        # Cache rows created by the old one-shot DaData cleaner have no resolver metadata.
        # Re-resolve them once so historical bad matches do not survive this upgrade.
        legacy_dadata_cache = bool(
            cached and c.geocoder == "dadata" and cached.provider == "dadata" and not _resolver_meta(cached)
        )
        if cached and not legacy_dadata_cache:
            s.geo = cached
            source = "cache"
        else:
            try:
                s.geo = g.geocode(s.address_raw, s.district)
                source = c.geocoder if not legacy_dadata_cache else f"{c.geocoder}:cache-refresh"
            except Exception as first:
                if c.llm_provider == "none":
                    raise RuntimeError(f"Строка {s.source_row}, адрес {s.address_raw!r}: {first}") from first
                cleaned = maybe_llm_clean(c, s.address_raw, s.district)
                s.geo = g.geocode(cleaned, s.district)
                s.warnings.append("Адрес потребовал LLM-нормализацию")
                source = f"{c.geocoder}+{c.llm_provider}"
            store.put_geocode(s.address_raw, s.district, s.geo)

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

        report.append({
            "row": s.source_row, "order_no": s.order_no, "raw": s.address_raw,
            "normalized": s.geo.normalized_address, "lat": s.geo.lat, "lon": s.geo.lon,
            "precision": s.geo.precision, "confidence": s.geo.confidence, "source": source,
            "requires_review": requires_review,
            "outside_expected_area": outside_expected_area,
            "resolver_status": resolver_status,
            "resolver_score": resolver.get("score"),
            "resolver_reasons": resolver.get("reasons", []),
            "resolver_query": resolver.get("query"),
            "resolver_candidates": resolver.get("candidates", []),
        })
    return report


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
