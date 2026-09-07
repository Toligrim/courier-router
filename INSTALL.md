# Установка на Raspberry Pi 5 / Ubuntu ARM64

Целевая машина:
- Raspberry Pi 5, ARM64, 16 GB RAM
- Ubuntu 25.10
- Python 3.13
- Docker уже установлен

OR-Tools 9.15.6755 имеет готовый wheel CPython 3.13 для Linux aarch64, поэтому
эмуляция x86 не нужна.

## 1. Распаковать проект

```bash
unzip courier-router.zip
cd courier-router
```

## 2. Создать виртуальное окружение

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e '.[dev]'
```

Проверка архитектуры и OR-Tools:

```bash
uname -m
python -c "import platform; print(platform.machine())"
python -c "from ortools.constraint_solver import pywrapcp; print('OR-Tools OK')"
```

Ожидается `aarch64` и `OR-Tools OK`.

## 3. Настроить секреты

```bash
cp .env.example .env
nano .env
```

Для самого простого рабочего режима нужны:
- `DADATA_TOKEN`
- `DADATA_SECRET`
- `ORS_API_KEY`

Оставить:
- `GEOCODER=dadata`
- `ROUTER=ors`
- `LLM_PROVIDER=none`

LLM не обязателен для первого запуска: DaData сам стандартизует российский адрес.
LLM можно включить после базовой проверки.

## 4. Инициализировать БД

```bash
courier-route doctor
```

Команда сама создаст SQLite-файл и таблицы.

## 5. Проверить фиксированный depot

```bash
courier-route geocode-depot
```

Команда покажет найденный адрес и координаты для:
`проспект Костюшко, 2, Санкт-Петербург`.

Проверьте координаты вручную. После проверки лучше записать их в `.env`:
`DEPOT_LAT=...`
`DEPOT_LON=...`

Тогда depot больше не зависит от внешнего геокодера.

## 6. Создать тестовый Excel

```bash
python scripts/make_sample_xlsx.py
```

Получится:
`data/input/sample.xlsx`

## 7. Построить маршрут

```bash
courier-route plan data/input/sample.xlsx \
  --date 2026-09-08 \
  --depart 10:00 \
  --end depot \
  --output data/output/sample
```

Проверить:
- `data/output/sample/itinerary.txt`
- `data/output/sample/route.json`
- `data/output/sample/route.png`
- `data/output/sample/geocoding-report.json`

## 8. Боевой Excel

Просто положить файл в `data/input/`:

```bash
courier-route plan data/input/route.xlsx \
  --date 2026-09-08 \
  --depart 10:00 \
  --end depot \
  --output data/output/2026-09-08
```

Если окно пустое, оно считается равным всей смене.
По умолчанию время обслуживания одной точки — 10 минут.

## 9. Локальный OSRM — второй этап

Сначала убедитесь, что cloud MVP полностью работает.

Затем:

```bash
bash docker/osrm/download-nwfd.sh
bash docker/osrm/preprocess.sh
docker compose -f docker/compose.osrm.yml up -d
```

Проверка:

```bash
curl http://127.0.0.1:5000/nearest/v1/driving/30.3,59.9
```

После этого изменить `.env`:

```text
ROUTER=osrm
OSRM_URL=http://127.0.0.1:5000
```

И повторить `courier-route plan`.

`download-nwfd.sh` загружает экстракт Северо-Западного ФО. Для постоянной
эксплуатации рекомендуется позже вырезать только Санкт-Петербург и Ленобласть через
osmium, чтобы уменьшить preprocessing и объём данных.

## 10. LLM-режим

Он нужен не всегда. Включать только после базового запуска.

OpenAI:

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
```

Anthropic:

```text
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-5
```

LLM вызывается только если основной геокодер не дал достаточно точного результата.
В LLM передаются район и адрес; телефон, номер оплаты и сумма не передаются.

## 11. Тесты

```bash
pytest -q
```

## 12. systemd не нужен для CLI

Это пакетный инструмент: водитель/оператор запускает его по необходимости.
Если позднее появится HTTP API или Telegram-бот, их лучше добавить отдельным
процессом, не смешивая с ядром маршрутизации.

## Важные ограничения

1. ORS/OSRM ETA не содержит актуальные дорожные пробки.
2. Низкоточная геокодировка не должна молча приниматься как правильный дом.
3. Стандартные OSM tiles используются только как небольшой online basemap.
   Не делать массовый offline download с tile.openstreetmap.org.
4. DaData/ORS/LLM требуют интернет; с локальным OSRM уже известные координаты можно
   оптимизировать без routing API.
5. Текущая версия считает `Забор` и `Отвоз` независимыми заданиями. Shipment-пары
   предусмотрены в модели, но автоматическое связывание по номеру заказа отключено,
   пока бизнес-семантика не подтверждена.
