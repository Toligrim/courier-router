#!/usr/bin/env python3
"""
Собирает интерактивную карту маршрута (Leaflet) из route.json, который делает
`courier-route plan`. Это вспомогательный просмотрщик, ядро проекта он не трогает.

  python tools/route_map_html.py data/output/<run>/route.json [-o route_map.html]

Открывать в браузере (нужен интернет для тайлов OSM). Карта тянется/зумится,
по клику на точке — заказ, ETA, адрес, окно, телефон, оплата.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
<title>Маршрут {date}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{{margin:0;height:100%;font:14px/1.45 system-ui,-apple-system,sans-serif}}
  #map{{position:absolute;inset:0}}
  .pin{{display:flex;align-items:center;justify-content:center;width:28px;height:28px;
        border-radius:50%;color:#fff;font-weight:700;font-size:13px;
        border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45)}}
  .pin.pickup{{background:#16a34a}}
  .pin.delivery{{background:#ea6a1e}}
  .pin.depot{{background:#111;border-radius:6px}}
  .panel{{position:absolute;z-index:1000;top:10px;right:10px;width:330px;max-width:82vw;
          max-height:calc(100% - 20px);overflow:auto;background:#fff;border-radius:10px;
          box-shadow:0 4px 18px rgba(0,0,0,.28);padding:10px 12px}}
  .panel h3{{margin:.2em 0 .1em;font-size:14px}}
  .panel .sub{{color:#666;margin:0 0 .6em;font-size:12px}}
  .row{{display:flex;gap:8px;padding:7px 0;border-top:1px solid #eee}}
  .row .n{{flex:none;width:22px;height:22px;border-radius:50%;color:#fff;font-weight:700;
           font-size:12px;display:flex;align-items:center;justify-content:center}}
  .row .n.pickup{{background:#16a34a}} .row .n.delivery{{background:#ea6a1e}}
  .row .eta{{font-variant-numeric:tabular-nums;font-weight:600}}
  .row .a{{color:#333}} .row .m{{color:#666;font-size:12px}}
  .toggle{{position:absolute;z-index:1001;top:10px;right:10px;display:none;
           background:#fff;border:1px solid #ccc;border-radius:8px;padding:6px 10px;font-weight:600}}
  @media(max-width:640px){{
    .panel{{transform:translateX(calc(100% + 20px));transition:transform .2s}}
    .panel.open{{transform:none}}
    .toggle{{display:block}}
  }}
</style>
</head>
<body>
<div id="map"></div>
<button class="toggle" onclick="document.querySelector('.panel').classList.toggle('open')">Список</button>
<div class="panel">
  <h3>Маршрут на {date}</h3>
  <p class="sub">Старт {depart} · {dist_km} км · финиш {finish}<br>
     🟢 забор · 🟠 отвоз · ⬛ база. Номер = порядок объезда.</p>
  {rows}
</div>
<script>
const DEPOT = {depot_json};
const STOPS = {stops_json};
const LINE  = {line_json};

const map = L.map('map', {{zoomControl: true}});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}}).addTo(map);

if (LINE.length > 1) L.polyline(LINE, {{color:'#1e55be', weight:5, opacity:.75}}).addTo(map);

function icon(cls, txt) {{
  return L.divIcon({{className:'', html:`<div class="pin ${{cls}}">${{txt}}</div>`,
    iconSize:[28,28], iconAnchor:[14,14]}});
}}

const bounds = [];
L.marker([DEPOT.lat, DEPOT.lon], {{icon: icon('depot','Б'), zIndexOffset:1000}})
  .bindPopup('<b>База</b><br>' + DEPOT.address).addTo(map);
bounds.push([DEPOT.lat, DEPOT.lon]);

STOPS.forEach(s => {{
  const m = L.marker([s.lat, s.lon], {{icon: icon(s.op, s.seq)}}).addTo(map);
  m.bindPopup(
    `<b>${{s.seq}}. ${{s.op_ru}} · заказ №${{s.order_no}}</b><br>` +
    `ETA <b>${{s.eta}}</b> · окно ${{s.window || '—'}}<br>` +
    `${{s.address}}<br>` +
    (s.approx ? '<span style="color:#b45309">⚠ дом определён приблизительно</span><br>' : '') +
    `☎ <a href="tel:${{s.phone_raw}}">${{s.phone}}</a>` +
    (s.payment ? `<br>💰 ${{s.payment}}` : '') +
    `<br><span style="color:#666">от пред.: ${{s.leg_km}} км · ${{s.leg_min}} мин</span>`
  );
  bounds.push([s.lat, s.lon]);
}});

map.fitBounds(bounds, {{padding:[45,45]}});
</script>
</body>
</html>
"""

OP_RU = {"pickup": "ЗАБОР", "delivery": "ОТВОЗ"}


def hhmm(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def build(route_json_path: Path, out_path: Path) -> None:
    d = json.loads(route_json_path.read_text("utf-8"))
    visits = d["visits"]
    geometry = d.get("geometry", [])

    # база — первая точка геометрии, если есть; иначе из первой ноги нет данных
    depot = {"lat": geometry[0][0], "lon": geometry[0][1],
             "address": "проспект Костюшко, 2, Санкт-Петербург"} if geometry else {}

    stops = []
    for v in visits:
        s = v["stop"]
        stops.append({
            "seq": v["sequence"],
            "op": s["operation"],
            "op_ru": OP_RU.get(s["operation"], s["operation"]),
            "order_no": s["order_no"],
            "eta": hhmm(v["arrival_min"]),
            "window": s.get("window") or "",
            "address": s.get("address_normalized") or s.get("address_raw") or "",
            "phone": s.get("phone") or "",
            "phone_raw": (s.get("phone") or "").replace(" ", "").replace("(", "").replace(")", "").replace("-", ""),
            "payment": s.get("payment") or "",
            "approx": (s.get("geocode_confidence") or 1) < 0.9,
            "lat": s["lat"], "lon": s["lon"],
            "leg_km": f'{v["distance_m_from_prev"]/1000:.1f}'.replace(".", ","),
            "leg_min": round(v["travel_sec_from_prev"] / 60),
        })

    rows = []
    for s in stops:
        cls = s["op"]
        rows.append(
            f'<div class="row"><div class="n {cls}">{s["seq"]}</div><div>'
            f'<div><span class="eta">{s["eta"]}</span> · {s["op_ru"]} №{s["order_no"]}'
            f'{" ⚠" if s["approx"] else ""}</div>'
            f'<div class="a">{html.escape(s["address"])}</div>'
            f'<div class="m">окно {html.escape(s["window"] or "—")} · ☎ {html.escape(s["phone"])}'
            f'{" · " + html.escape(s["payment"]) if s["payment"] else ""}</div>'
            f'</div></div>'
        )

    total_km = f'{d["summary"]["total_distance_m"]/1000:.1f}'.replace(".", ",")
    page = TEMPLATE.format(
        date=route_json_path.parent.name.replace("route-", ""),
        depart=hhmm(visits[0]["arrival_min"] - round(visits[0]["travel_sec_from_prev"] / 60)) if visits else "—",
        finish="база",
        dist_km=total_km,
        rows="".join(rows),
        depot_json=json.dumps(depot, ensure_ascii=False),
        stops_json=json.dumps(stops, ensure_ascii=False),
        line_json=json.dumps(geometry),
    )
    out_path.write_text(page, "utf-8")
    print("saved", out_path, f"({len(page)//1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("route_json", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    a = ap.parse_args()
    build(a.route_json, a.out or a.route_json.with_name("route_map.html"))
