# courier-router — отчёт о развёртывании на Raspberry Pi 5

Дата: 2026-09-07
Исполнитель: coding-агент на целевой машине
Основание: `AGENT_DEPLOYMENT_PROMPT.md` из архива `courier-router-v0.1.0.zip`

## Краткий итог

- Проект распакован, установлен в отдельный `.venv` (Python 3.13.7),
  `pip install -e '.[dev]'`.
- OR-Tools 9.15.6755 импортируется **нативно на aarch64**, без x86-эмуляции.
- `pytest -q` — **7 passed** (5 штатных + 2 новых офлайн-теста pipeline).
- `courier-route doctor` — ОК, `geocoder=dadata configured`, `router=ors configured`.
- **DaData проверен вживую**: `geocode-depot` + геокодирование всех 4 адресов
  `sample.xlsx` реальными вызовами (3× precision `exact` / conf 0.99, 1× `nearest_house` / 0.90).
- **ORS проверен вживую**: matrix + directions на `api.openrouteservice.org`,
  ответ 200, 917 точек геометрии.
- **Полный боевой прогон `courier-route plan` на `sample.xlsx` удался**: маршрут
  `feasible`, временные окна соблюдены, созданы все 4 артефакта (в т.ч.
  `route.png` с дорожной геометрией и стрелками направления).
- **OSRM и LLM не запускались** — по инструкции это этапы после рабочего cloud MVP
  (OSRM) и после рабочего маршрута без LLM (LLM); плюс для LLM нет ключа.
- **Багов в коде проекта не найдено. Исходники пакета `src/` не изменялись.**
  Добавлен новый тест `tests/test_pipeline_offline.py`; в нём же по ходу
  исправлена собственная ошибка изоляции (тест писал в реальный кэш) — см. §9–10.

---

## 1. Среда

| | |
|---|---|
| Железо | Raspberry Pi 5 Model B Rev 1.1 |
| CPU | 4× ARM Cortex-A76, `aarch64` |
| RAM | 16 GB, swap отсутствует |
| ОС | Ubuntu 25.10, ядро `6.17.0-1021-raspi` |
| `platform.platform()` | `Linux-6.17.0-1021-raspi-aarch64-with-glibc2.42` |
| Диск | SSD, свободно ~255 GB |
| Docker | 29.4.3 + Compose v5.1.3 установлены; демон `inactive` (нужен для OSRM-этапа) |
| Git | каталог проекта — не git-репозиторий; ничего не коммитилось |
| Интернет | есть |

---

## 2. Выполненные команды (ключевые, в порядке выполнения)

```bash
# в системе нет unzip — распаковка модулем Python
python3 -m zipfile -e courier-router-v0.1.0.zip .
cd courier-router

# venv + установка
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e '.[dev]'

# архитектура и OR-Tools
uname -m                                                             # aarch64
.venv/bin/python -c "from ortools.constraint_solver import pywrapcp; print('OR-Tools OK')"
file .venv/lib/python3.13/site-packages/ortools/constraint_solver/_pywrapcp.so
#   -> ELF 64-bit LSB shared object, ARM aarch64 (нативно)

# статические проверки, тесты, doctor, sample
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m pytest -q
cp .env.example .env
#   -> вписаны DADATA_TOKEN, DADATA_SECRET, ORS_API_KEY (ключи оператора)
.venv/bin/courier-route doctor
.venv/bin/python scripts/make_sample_xlsx.py

# ЖИВЫЕ вызовы
.venv/bin/courier-route geocode-depot                                # DaData
.venv/bin/courier-route plan data/input/sample.xlsx \
  --date 2026-09-08 --depart 10:00 --end depot \
  --output data/output/sample-live                                   # DaData + ORS + OR-Tools
```

Дополнительно: офлайн-прогоны `cli.cmd_plan` со стаб-геокодером/роутером для
режимов `--end depot` и `--end open`; прямой тест ключа ORS на
`api.openrouteservice.org` и `api.heigit.org`.

---

## 3. Установленные версии

Python **3.13.7** (`/usr/bin/python3.13`); pip 26.2.1; setuptools 84.0.0; wheel 0.48.0.

| пакет | версия | пакет | версия |
|---|---|---|---|
| courier-router | 0.1.0 (editable) | pydantic | 2.13.5 |
| ortools | **9.15.6755** | pydantic-core | 2.46.5 |
| httpx | 0.28.1 | protobuf | 6.33.6 |
| openpyxl | 3.1.5 | numpy | 2.5.3 |
| Pillow | 12.3.0 | pandas | 3.0.5 |
| python-dotenv | 1.2.3 | pytest | 9.1.1 |

`ortools` wheel: `ortools-9.15.6755-cp313-cp313-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl`.
`_pywrapcp.so` → `ELF 64-bit LSB shared object, ARM aarch64` (нативно, без эмуляции).

Отдельно (инструментарий, не зависимость проекта, добавлен для удалённого браузера
и потом остановлен — см. §13): `playwright 1.62.0` в `.venv`, Playwright-Chromium
151.0.7922.34 в `~/.cache/ms-playwright` (~984 MB), apt-пакет `novnc 1.6.0`.

---

## 4. Результат `pytest -q`

```
7 passed in ~34s
```

- `tests/test_optimizer.py::test_small_route` — passed (OR-Tools реально
  установлен; в среде сборки архива тест не исполнялся).
- `tests/test_parsing.py` — 3 passed.
- `tests/test_render.py::test_webmercator_finite` — passed.
- `tests/test_pipeline_offline.py` — 2 passed (**добавлено агентом**, см. §10).

`python -m compileall src scripts tests` — без ошибок.
Время в основном уходит на загрузку OSM-тайлов в рендер-тестах.

---

## 5. Результат `courier-route doctor`

```
platform=Linux-6.17.0-1021-raspi-aarch64-with-glibc2.42
machine=aarch64
python=3.13.7
ortools=9.15.6755
sqlite=data/cache/courier-router.db OK
geocoder=dadata configured
router=ors configured
llm=none
```

---

## 6. Статус внешних компонентов

### DaData — РАБОТАЕТ

`courier-route geocode-depot`:

```json
{
  "address": "г Санкт-Петербург, ул Костюшко, д 2",
  "lat": 59.8491293, "lon": 30.2955126,
  "provider": "dadata", "precision": "exact", "confidence": 0.99
}
```

Геокодирование адресов `sample.xlsx` (все `source=dadata`, живые вызовы):

| заказ | precision | conf | координаты | нормализованный адрес |
|---|---|---|---|---|
| 18452 | exact | 0.99 | 59.86156, 30.18205 | г Санкт-Петербург, пр-кт Ленинский, д 72 к 1, кв 378 |
| 18477 | nearest_house | 0.90 | 59.83593, 30.25701 | г Санкт-Петербург, ул Танкиста Хрустицкого, д 102 литера а, кв 20 |
| 18491 | exact | 0.99 | 59.77549, 30.07737 | Ленинградская обл, Ломоносовский р-н, д Пески, ул Центральная, д 210 |
| 18500 | exact | 0.99 | 59.73592, 30.07897 | г Санкт-Петербург, г Красное Село, ул Новая, д 22, кв 25 |

Все ≥ 0.80 — порог `confidence` не сработал, `--allow-low-confidence` не
потребовался. Даже «деревня Пески» в Ленобласти определилась как `exact`.

### ORS — РАБОТАЕТ

Прямой тест ключа: `POST https://api.openrouteservice.org/v2/matrix/driving-car`
→ **200**, корректные `durations`/`distances`. В боевом `plan`: matrix +
`directions/driving-car/geojson` → 917 точек геометрии.

Квоты бесплатного ключа (из дашборда HeiGIT): Directions V2 2000/сут,
Matrix V2 500/сут, Isochrones 500/сут, Snap 2000/сут, Export 100/сут.
Один прогон `plan` расходует 1 matrix + 1 directions.

Замечание: ORS помечает `api.openrouteservice.org` как deprecated в пользу
`api.heigit.org`. Сейчас старый хост отвечает 200 и используется проектом
(`src/courier_router/routing.py`). `api.heigit.org/v2/matrix/...` в лоб отдал
не-JSON — миграция потребует уточнения пути/авторизации, это отдельная задача,
на MVP не влияет.

### Локальный OSRM — НЕ ЗАПУСКАЛСЯ

По `AGENT_DEPLOYMENT_PROMPT` п.13 и `INSTALL.md` §9 — только после полностью
рабочего cloud MVP. Требуется: `systemctl start docker`, загрузка экстракта СЗФО
(~0.7–1 ГБ), `osrm-extract/partition/customize` (CPU/RAM-затратно). Образ
`ghcr.io/project-osrm/osrm-backend:26.8.0-debian` по `docs/SOURCES.md` публикуется
под linux/arm64 — manifest и отсутствие amd64-эмуляции проверю на этапе
развёртывания. Планируемая проверка после запуска: тот же `plan` с `ROUTER=osrm`,
сравнение порядка и суммарной дистанции с прогоном на ORS.

### LLM — НЕ ПРОВЕРЯЛСЯ

По инструкции — только после рабочего маршрута без LLM; плюс нет ключа OpenAI/
Anthropic. `LLM_PROVIDER=none`.

**Не хватает секретов:** только для LLM-этапа — `OPENAI_API_KEY` или
`ANTHROPIC_API_KEY`. Для cloud MVP всё есть.

---

## 7. Боевой прогон `courier-route plan` (sample.xlsx, живые API)

`--date 2026-09-08 --depart 10:00 --end depot`, старт `г Санкт-Петербург, ул Костюшко, д 2`.

| # | ETA | тип | заказ | окно | от пред. точки |
|---|---|---|---|---|---|
| 1 | 10:13 | ОТВОЗ | 18452 | 10:00-12:00 | 7,1 км · 13 мин |
| 2 | 11:00 | ЗАБОР | 18477 | 11:00-15:00 | 6,0 км · 11 мин |
| 3 | 12:00 | ОТВОЗ | 18491 | 12:00-18:00 | 15,7 км · 20 мин |
| 4 | 15:00 | ОТВОЗ | 18500 | 15:00-21:00 | 8,3 км · 15 мин |

Итого: 58,7 км · 90 мин движения · 40 мин обслуживания · 211 мин ожидания ·
341 мин суммарно. `route.json.feasible = true`. Все ETA попадают в свои окна
(ожидание перед точками 2–4 — из-за поздних окон при близком расположении, это
свойство данных, а не ошибка).

Дополнительно офлайн (стаб-роутер) проверена ветка `--end open`: 4 артефакта,
маршрут без обратного плеча.

---

## 8. Артефакты

`data/input/sample.xlsx` — из `scripts/make_sample_xlsx.py` (4 строки).

`data/output/sample-live/` (боевой прогон, живые DaData+ORS):

| файл | размер | содержимое |
|---|---|---|
| `itinerary.txt` | ~1.5 KB | порядок объезда, ETA, адрес (нормализованный DaData), телефон, окно, оплата, км/мин от предыдущей, блок «Всего» |
| `route.json` | ~46 KB | `feasible`, `summary`, `visits[]` (lat/lon, precision, confidence), `geometry` (917 точек) |
| `route.png` | ~1.6 MB | карта: базовый слой OSM + синяя линия по дорогам + стрелки направления + нумерованные метки (зелёная = ЗАБОР, оранжевые = ОТВОЗ) + чёрный квадрат `S` + атрибуция OSM |
| `geocoding-report.json` | ~1.6 KB | по каждой точке: raw/normalized/lat/lon/precision/confidence/source=dadata |

`data/cache/courier-router.db` — SQLite: 4 записи `geocode_cache` (provider=dadata),
таблицы `address_aliases`, `route_runs`.

---

## 9. Найденные ошибки

**В коде проекта (`src/courier_router/*`) — нет.** `compileall` чистый; штатные и
новые тесты зелёные; боевой прогон и все офлайн-ветки CLI/солвера отрабатывают
корректно. Правки исходников пакета не потребовались.

Ошибка в **добавленном агентом тесте** (найдена и исправлена в этой же сессии):
`tests/test_pipeline_offline.py` первой версии использовал реальный `Config().db_path`,
поэтому стаб-геокодер писал фейковые координаты в общий
`data/cache/courier-router.db`. Из-за этого первый «боевой» `plan` брал адреса из
отравленного кэша (`source=cache`, raw-адреса), а не из DaData. Исправление:
тест подменяет `cli.Storage` на экземпляр с БД в `tmp_path`. Кэш очищен, `plan`
перезапущен — теперь `source=dadata`. Вывод для проекта: у `plan`/`geocode_stops`
нет флага «игнорировать кэш» — при отладке кэш чистится вручную
(`rm data/cache/courier-router.db`).

Замечания (не баги, не исправлялись — вне MVP):

1. `.env.example`: `OPENAI_MODEL=gpt-5.6-luna` — идентификатор выглядит
   несуществующим; при `LLM_PROVIDER=openai` реальный API, вероятно, вернёт
   ошибку. Anthropic-дефолт `claude-sonnet-5` — валиден.
2. ORS deprecatiт `api.openrouteservice.org` → `api.heigit.org` (см. §6). Сейчас
   старый хост работает; в будущем `routing.py` потребует смены base URL.
3. `render.py`: стрелки направления рисуются с шагом `max(20, len//20)` по точкам
   геометрии — на очень короткой геометрии не появляются; с реальной геометрией
   ORS (917 точек) появляются штатно.
4. `cli.cmd_plan` создаёт геокодер дважды (в `geocode_stops` и отдельно для депо)
   — лишний `httpx.Client`; микро-неэффективность.

---

## 10. Внесённые изменения

Добавлен **один новый файл** — `tests/test_pipeline_offline.py` (2 теста):

- `test_full_plan_offline` — прогон `cmd_plan` из `sample.xlsx` со стаб-геокодером
  и стаб-роутером (monkeypatch), с изолированной БД в `tmp_path`; проверка
  наличия и непустоты всех 4 артефактов, корректности `route.json`, соблюдения
  временных окон, валидности `route.png` (размер, не одноцветный).
- `test_report_has_no_low_confidence_warnings` — `geocoding-report.json` для
  sample не содержит записей с `confidence < 0.80`.

**Исходники пакета `src/courier_router/*` не изменялись.**

---

## 11. Точные команды для боевого Excel

`.env` уже заполнен (`DADATA_TOKEN`, `DADATA_SECRET`, `ORS_API_KEY`;
`GEOCODER=dadata`, `ROUTER=ors`, `LLM_PROVIDER=none`).

Опционально закрепить депо (после ручной сверки координат
`59.8491293, 30.2955126` — «ул Костюшко, д 2»):

```
# в .env
DEPOT_LAT=59.8491293
DEPOT_LON=30.2955126
```

Боевой запуск:

```bash
cd courier-router
.venv/bin/courier-route plan data/input/route.xlsx \
  --date 2026-09-08 \
  --depart 10:00 \
  --end depot \
  --output data/output/2026-09-08
```

Результат — в `data/output/2026-09-08/`: `itinerary.txt`, `route.json`,
`route.png`, `geocoding-report.json`. Если по точке `confidence < 0.80` — запуск
останавливается; после ручной проверки адреса повторить с `--allow-low-confidence`.
При отладке, чтобы не брать старые координаты из кэша:
`rm data/cache/courier-router.db`.

Следующие этапы (по инструкции — после подтверждения, что cloud MVP устраивает):

```bash
# OSRM
sudo systemctl start docker
bash docker/osrm/download-nwfd.sh
bash docker/osrm/preprocess.sh
docker compose -f docker/compose.osrm.yml up -d
curl http://127.0.0.1:5000/nearest/v1/driving/30.3,59.9
#   в .env:  ROUTER=osrm   OSRM_URL=http://127.0.0.1:5000
.venv/bin/courier-route plan data/input/route.xlsx --date 2026-09-08 \
  --depart 10:00 --end depot --output data/output/2026-09-08-osrm
#   сравнить порядок/дистанцию с прогоном на ORS

# LLM: один искусственно грязный адрес, после рабочего маршрута без LLM
#   в .env:  LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=...  ANTHROPIC_MODEL=claude-sonnet-5
```

---

## 12. Открытые вопросы к автору проекта

1. Подтвердить/поправить `OPENAI_MODEL` в `.env.example` (§9.1).
2. Планировать ли переезд `routing.py` на `api.heigit.org` (§6, §9.2).
3. `Забор`/`Отвоз` в v0.1 — независимые задания (как сейчас) или shipment-пары
   по номеру заказа? В `docs/ARCHITECTURE.md` автосвязывание отключено до
   подтверждения бизнес-семантики.
4. Нужен ли фолбэк-геокодер (`GEOCODER=nominatim`) в проде.

---

## 13. Инфраструктура удалённого браузера (поднята и остановлена)

По ходу сессии для получения ключей поднимался удалённый браузер (Xvfb :99 +
x11vnc + noVNC/websockify на 127.0.0.1:6080 + Playwright-Chromium с CDP на
127.0.0.1:9222), всё только на loopback, под SSH-туннель. Оператор в итоге создал
оба токена сам, стек **остановлен** (процессы убиты, порты 5900/6080/9222
закрыты). Осталось установленным (можно удалить): `playwright` в `.venv`,
`~/.cache/ms-playwright` (~984 MB), apt-пакет `novnc`.
