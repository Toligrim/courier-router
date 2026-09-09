from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import html
import json
import os
import secrets
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .cli import cmd_plan

RUNS_ROOT = Path(os.getenv("WEB_RUNS_PATH", "data/web/runs"))
USERS_PATH = Path(os.getenv("WEB_USERS_PATH", "data/web/users.json"))
MAX_UPLOAD_BYTES = int(os.getenv("WEB_MAX_UPLOAD_MB", "10")) * 1024 * 1024

CSS = """
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#18212f;background:#f4f6f8}
*{box-sizing:border-box}body{margin:0}.top{height:60px;background:#111827;color:white;display:flex;align-items:center;justify-content:space-between;padding:0 18px;position:sticky;top:0;z-index:1000}.top a{color:white;text-decoration:none}.brand{font-weight:800}.wrap{max-width:1100px;margin:0 auto;padding:20px}.card{background:white;border-radius:18px;padding:20px;box-shadow:0 8px 30px rgba(15,23,42,.08);margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.metric{background:#f8fafc;border-radius:14px;padding:14px}.metric b{font-size:24px;display:block}.muted{color:#64748b}.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:12px;padding:12px 16px;background:#2563eb;color:white;text-decoration:none;font-weight:700;cursor:pointer}.btn.secondary{background:#e2e8f0;color:#0f172a}.btn.danger{background:#334155}.field{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}.field input,.field select{width:100%;border:1px solid #cbd5e1;border-radius:12px;padding:12px;font-size:16px}.upload{border:2px dashed #94a3b8;border-radius:18px;padding:28px;text-align:center;background:#f8fafc}.error{background:#fee2e2;color:#991b1b;border-radius:12px;padding:12px;margin-bottom:14px}.run{display:flex;justify-content:space-between;gap:14px;align-items:center;border-top:1px solid #e2e8f0;padding:14px 0}.run:first-child{border-top:0}.route-layout{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(340px,.8fr);gap:16px}.map{height:72vh;min-height:520px;border-radius:18px;overflow:hidden}.stops{max-height:72vh;overflow:auto}.stop{border:1px solid #e2e8f0;border-radius:16px;padding:14px;margin-bottom:10px;background:white}.seq{display:inline-flex;width:30px;height:30px;border-radius:50%;align-items:center;justify-content:center;background:#111827;color:white;font-weight:800;margin-right:8px}.stop h3{margin:0 0 8px}.stop p{margin:5px 0}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:12px;font-weight:700}.login{max-width:420px;margin:10vh auto}.leaflet-div-icon{background:transparent!important;border:0!important}.marker-num{width:34px;height:34px;border-radius:50%;background:#111827;color:white;display:flex;align-items:center;justify-content:center;border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,.35);font-weight:800}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}@media(max-width:800px){.wrap{padding:12px}.grid{grid-template-columns:1fr}.route-layout{grid-template-columns:1fr}.map{height:55vh;min-height:360px}.stops{max-height:none}.top{height:54px}.card{border-radius:14px;padding:14px}}
"""


def _shell(title: str, body: str, user: str | None = None, head: str = "") -> str:
    auth = f'<span>{html.escape(user)} · <a href="/logout">Выйти</a></span>' if user else ""
    top = f'<header class="top"><a class="brand" href="/">Courier Router</a>{auth}</header>' if user else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{html.escape(title)}</title><style>{CSS}</style>{head}</head><body>{top}<main class="wrap">{body}</main></body></html>"""


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _load_users() -> dict[str, str]:
    if not USERS_PATH.exists():
        return {}
    return json.loads(USERS_PATH.read_text(encoding="utf-8"))


def _save_users(users: dict[str, str]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        USERS_PATH.chmod(0o600)
    except OSError:
        pass


def add_user(username: str, password: str | None = None) -> None:
    username = username.strip()
    if not username or any(ch in username for ch in "/\\\0"):
        raise ValueError("Некорректное имя пользователя")
    if password is None:
        password = getpass.getpass("Пароль: ")
        repeated = getpass.getpass("Повторите пароль: ")
        if password != repeated:
            raise ValueError("Пароли не совпадают")
    if len(password) < 8:
        raise ValueError("Пароль должен быть не короче 8 символов")
    users = _load_users()
    users[username] = _hash_password(password)
    _save_users(users)
    print(f"Пользователь {username!r} сохранён в {USERS_PATH}")


def list_users() -> None:
    users = _load_users()
    if not users:
        print("Пользователей пока нет")
        return
    for username in sorted(users, key=str.casefold):
        print(username)


def _require_user(request: Request) -> str | None:
    user = request.session.get("user")
    return str(user) if user else None


def _user_runs_root(user: str) -> Path:
    """Stable filesystem namespace for one account without putting usernames in paths."""
    user_key = hashlib.sha256(user.encode("utf-8")).hexdigest()[:32]
    return RUNS_ROOT / "_users" / user_key


def _owned_meta(folder: Path, user: str) -> dict | None:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return meta if meta.get("uploaded_by") == user else None


def _find_run_folder(user: str, run_id: str) -> Path | None:
    """Find a run owned by user. New namespaced layout first, then legacy v1 layout."""
    if not run_id.isalnum():
        return None
    candidates = [
        _user_runs_root(user) / run_id,
        RUNS_ROOT / run_id,
    ]
    for folder in candidates:
        if folder.is_dir() and _owned_meta(folder, user) is not None:
            return folder
    return None


def _login_page(error: str = "") -> str:
    err = f'<div class="error">{html.escape(error)}</div>' if error else ""
    body = f"""<div class="login card"><h1>Courier Router</h1><p class="muted">Закрытый планировщик маршрутов</p>{err}<form method="post" action="/login"><div class="field"><label>Логин</label><input name="username" autocomplete="username" required autofocus></div><div class="field"><label>Пароль</label><input type="password" name="password" autocomplete="current-password" required></div><button class="btn" type="submit">Войти</button></form></div>"""
    return _shell("Вход", body)


def _list_runs(user: str) -> list[dict]:
    if not RUNS_ROOT.exists():
        return []

    items: list[dict] = []
    seen: set[str] = set()

    user_root = _user_runs_root(user)
    if user_root.exists():
        for folder in user_root.iterdir():
            if not folder.is_dir():
                continue
            meta = _owned_meta(folder, user)
            route_path = folder / "route.json"
            if meta is None or not route_path.exists():
                continue
            try:
                route = json.loads(route_path.read_text(encoding="utf-8"))
                summary = route.get("summary", {})
                items.append({
                    "id": folder.name,
                    "meta": meta,
                    "summary": summary,
                    "count": len(route.get("visits", [])),
                    "mtime": folder.stat().st_mtime,
                })
                seen.add(folder.name)
            except (OSError, ValueError, TypeError):
                continue

    for folder in RUNS_ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith("_") or folder.name in seen:
            continue
        meta = _owned_meta(folder, user)
        route_path = folder / "route.json"
        if meta is None or not route_path.exists():
            continue
        try:
            route = json.loads(route_path.read_text(encoding="utf-8"))
            summary = route.get("summary", {})
            items.append({
                "id": folder.name,
                "meta": meta,
                "summary": summary,
                "count": len(route.get("visits", [])),
                "mtime": folder.stat().st_mtime,
            })
        except (OSError, ValueError, TypeError):
            continue

    return sorted(items, key=lambda x: x["mtime"], reverse=True)[:30]


def _home_page(user: str, error: str = "") -> str:
    err = f'<div class="error">{html.escape(error)}</div>' if error else ""
    runs_html = ""
    for run in _list_runs(user):
        km = run["summary"].get("total_distance_m", 0) / 1000
        runs_html += f"""<div class="run"><div><b>{html.escape(run['meta'].get('date',''))}</b><div class="muted">{run['count']} точек · {km:.1f} км · старт {html.escape(run['meta'].get('depart',''))}</div></div><a class="btn secondary" href="/routes/{run['id']}">Открыть</a></div>"""
    if not runs_html:
        runs_html = '<p class="muted">Пока нет рассчитанных маршрутов.</p>'
    today = date.today().isoformat()
    body = f"""{err}<section class="card"><h1>Новый маршрут</h1><p class="muted">Загрузите таблицу в том же формате, что используется CLI. Поддерживаются XLSX и CSV.</p><form method="post" action="/routes" enctype="multipart/form-data"><div class="upload field"><label><b>Файл с заказами</b></label><input type="file" name="table" accept=".xlsx,.csv" required></div><div class="grid"><div class="field"><label>Дата</label><input type="date" name="day" value="{today}" required></div><div class="field"><label>Старт</label><input type="time" name="depart" value="10:00" required></div><div class="field"><label>Финиш</label><select name="end"><option value="open" selected>Последняя точка</option><option value="depot">Вернуться на базу</option></select></div></div><label><input type="checkbox" name="allow_low_confidence" value="1"> Разрешить точки с низкой точностью геокодирования</label><div class="toolbar"><button class="btn" type="submit">Построить маршрут</button></div></form></section><section class="card"><h2>Мои последние маршруты</h2>{runs_html}</section>"""
    return _shell("Маршруты", body, user)


def _hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def _route_page(user: str, run_id: str, meta: dict, route: dict) -> str:
    if not route.get("feasible"):
        warnings = "<br>".join(html.escape(x) for x in route.get("warnings", []))
        return _shell("Маршрут не построен", f'<div class="card"><h1>Маршрут не построен</h1><div class="error">{warnings}</div><a class="btn secondary" href="/">Назад</a></div>', user)
    summary = route.get("summary", {})
    km = summary.get("total_distance_m", 0) / 1000
    travel = round(summary.get("total_travel_sec", 0) / 60)
    waiting = round(summary.get("total_wait_sec", 0) / 60)
    stops_html = ""
    for visit in route.get("visits", []):
        stop = visit["stop"]
        lat, lon = stop["lat"], stop["lon"]
        address = stop.get("address_normalized") or stop.get("address_raw") or ""
        nav = f"https://yandex.ru/maps/?rtext=~{lat}%2C{lon}&rtt=auto"
        operation = "Забор" if stop.get("operation") == "pickup" else "Доставка"
        phone = html.escape(stop.get("phone") or "")
        phone_html = f'<p><a href="tel:{quote(stop.get("phone") or "")}">{phone}</a></p>' if phone else ""
        window = html.escape(stop.get("window") or "нет")
        payment = html.escape(stop.get("payment") or "")
        stops_html += f"""<article class="stop" id="stop-{visit['sequence']}"><h3><span class="seq">{visit['sequence']}</span>{html.escape(address)}</h3><p><span class="pill">ETA {_hhmm(visit['arrival_min'])}</span> <span class="pill">{operation}</span></p><p>Заказ №{html.escape(str(stop.get('order_no','')))} · окно {window}</p>{phone_html}{f'<p>Оплата: {payment}</p>' if payment else ''}<p class="muted">От предыдущей: {visit['distance_m_from_prev']/1000:.1f} км · {round(visit['travel_sec_from_prev']/60)} мин</p><a class="btn" href="{nav}" target="_blank" rel="noopener">Открыть в Яндекс Картах</a></article>"""
    data = json.dumps(route, ensure_ascii=False).replace("</", "<\\/")

    # Одна ссылка на весь маршрут для приложения «Яндекс Карты / Навигатор» на iOS.
    navi_url = ""
    visits = route.get("visits", [])
    geom = route.get("geometry") or []
    if visits and geom:
        from .navlinks import RoutePoint, build_yandex_url
        d_lat, d_lon = geom[0][0], geom[0][1]
        pts = [RoutePoint("start", "База", d_lat, d_lon)]
        for v in visits:
            st = v["stop"]
            pts.append(RoutePoint("via", st.get("address_normalized") or st.get("address_raw") or "",
                                  st["lat"], st["lon"], str(st.get("order_no", ""))))
        if meta.get("end", "depot") == "depot":
            pts.append(RoutePoint("finish", "База", d_lat, d_lon))
        if len(pts) >= 2:
            navi_url = build_yandex_url(pts)
    navi_html = (
        f'<a class="btn" href="{navi_url}">🧭 Весь маршрут в Яндекс Навигаторе</a>'
        f'<p class="muted" style="margin:4px 0 0">Откроется приложение на телефоне. '
        f'Яндекс считает переходы по ссылке: ~5 за 24 ч, дальше откроет веб-версию.</p>'
        if navi_url else ""
    )

    head = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    body = f"""<section class="card"><h1>Маршрут на {html.escape(meta.get('date',''))}</h1><div class="grid"><div class="metric"><span class="muted">Точек</span><b>{len(route.get('visits',[]))}</b></div><div class="metric"><span class="muted">Пробег</span><b>{km:.1f} км</b></div><div class="metric"><span class="muted">Движение / ожидание</span><b>{travel} / {waiting} мин</b></div></div>{navi_html}<div class="toolbar"><a class="btn secondary" href="/">← К загрузке</a><a class="btn secondary" href="/routes/{run_id}/itinerary">Маршрут текстом</a></div></section><div class="route-layout"><div id="map" class="map card"></div><div class="stops">{stops_html}</div></div><script id="route-data" type="application/json">{data}</script>""" + """<script>
const route=JSON.parse(document.getElementById('route-data').textContent);
const visits=route.visits||[]; const geometry=route.geometry||[];
const map=L.map('map',{zoomControl:true});
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
const bounds=[];
if(geometry.length){const line=geometry.map(p=>[p[0],p[1]]);L.polyline(line,{weight:5,opacity:.75}).addTo(map);line.forEach(p=>bounds.push(p));}
visits.forEach(v=>{const s=v.stop;const icon=L.divIcon({className:'leaflet-div-icon',html:`<div class="marker-num">${v.sequence}</div>`,iconSize:[34,34],iconAnchor:[17,17]});const m=L.marker([s.lat,s.lon],{icon}).addTo(map);m.bindPopup(`<b>${v.sequence}. ${s.address_normalized||s.address_raw}</b><br>ETA ${String(Math.floor(v.arrival_min/60)%24).padStart(2,'0')}:${String(v.arrival_min%60).padStart(2,'0')}<br>Заказ №${s.order_no}`);m.on('click',()=>document.getElementById(`stop-${v.sequence}`)?.scrollIntoView({behavior:'smooth',block:'center'}));bounds.push([s.lat,s.lon]);});
if(bounds.length) map.fitBounds(bounds,{padding:[30,30]}); else map.setView([59.94,30.31],10);
</script>"""
    return _shell(f"Маршрут {meta.get('date','')}", body, user, head)


def create_app(session_secret: str | None = None) -> FastAPI:
    secret = session_secret or os.getenv("WEB_SESSION_SECRET")
    if not secret:
        raise RuntimeError("Укажите WEB_SESSION_SECRET (например: openssl rand -hex 32)")
    app = FastAPI(title="Courier Router Web", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=os.getenv("WEB_HTTPS_ONLY", "1") != "0",
        max_age=60 * 60 * 24 * 30,
    )

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if _require_user(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_login_page())

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
        encoded = _load_users().get(username)
        if not encoded or not _verify_password(password, encoded):
            return HTMLResponse(_login_page("Неверный логин или пароль"), status_code=401)
        request.session.clear()
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(_home_page(user))

    @app.post("/routes", response_class=HTMLResponse)
    async def build_route(
        request: Request,
        table: UploadFile = File(...),
        day: str = Form(...),
        depart: str = Form(...),
        end: str = Form("open"),
        allow_low_confidence: str | None = Form(None),
    ):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        suffix = Path(table.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".csv"}:
            return HTMLResponse(_home_page(user, "Поддерживаются только XLSX и CSV"), status_code=400)
        payload = await table.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            return HTMLResponse(_home_page(user, f"Файл больше лимита {MAX_UPLOAD_BYTES // 1024 // 1024} МБ"), status_code=413)
        run_id = uuid4().hex[:12]
        folder = _user_runs_root(user) / run_id
        folder.mkdir(parents=True, exist_ok=False)
        input_path = folder / f"input{suffix}"
        input_path.write_bytes(payload)
        meta = {"date": day, "depart": depart, "end": end, "uploaded_by": user, "filename": table.filename}
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        args = SimpleNamespace(
            xlsx=str(input_path), date=day, depart=depart, end=end,
            output=str(folder), allow_low_confidence=bool(allow_low_confidence),
        )
        try:
            await run_in_threadpool(cmd_plan, args)
        except Exception as exc:
            return HTMLResponse(_home_page(user, f"Не удалось построить маршрут: {exc}"), status_code=400)
        return RedirectResponse(f"/routes/{run_id}", status_code=303)

    @app.get("/routes/{run_id}", response_class=HTMLResponse)
    async def route_view(request: Request, run_id: str):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not run_id.isalnum():
            return HTMLResponse("Некорректный маршрут", status_code=400)
        folder = _find_run_folder(user, run_id)
        if folder is None:
            return HTMLResponse("Маршрут не найден", status_code=404)
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            route = json.loads((folder / "route.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return HTMLResponse("Маршрут не найден", status_code=404)
        return HTMLResponse(_route_page(user, run_id, meta, route))

    @app.get("/routes/{run_id}/itinerary", response_class=HTMLResponse)
    async def itinerary(request: Request, run_id: str):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not run_id.isalnum():
            return HTMLResponse("Некорректный маршрут", status_code=400)
        folder = _find_run_folder(user, run_id)
        if folder is None:
            return HTMLResponse("Маршрут не найден", status_code=404)
        path = folder / "itinerary.txt"
        if not path.exists():
            return HTMLResponse("Маршрут не найден", status_code=404)
        text = html.escape(path.read_text(encoding="utf-8"))
        return HTMLResponse(_shell("Маршрут текстом", f'<div class="card"><pre style="white-space:pre-wrap;font:inherit">{text}</pre></div>', user))

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="courier-web")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Запустить веб-интерфейс")
    serve.add_argument("--host", default=os.getenv("WEB_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")))
    user = sub.add_parser("user-add", help="Создать или сменить пароль пользователя")
    user.add_argument("username")
    sub.add_parser("user-list", help="Показать созданные аккаунты")
    args = parser.parse_args()
    if args.command == "user-add":
        add_user(args.username)
        return
    if args.command == "user-list":
        list_users()
        return
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
