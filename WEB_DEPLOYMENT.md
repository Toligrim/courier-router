# Courier Router Web

Небольшой закрытый веб-интерфейс поверх существующего планировщика.

## Что умеет первая версия

- вход по логину и паролю;
- несколько локальных пользователей;
- пароли хранятся как salted `scrypt` hashes;
- загрузка `.xlsx` и `.csv`;
- запуск того же deterministic pipeline, что используется CLI;
- история последних рассчитанных маршрутов;
- интерактивная Leaflet/OpenStreetMap карта;
- пронумерованные точки и линия маршрута;
- ETA, временные окна, телефон, оплата и расстояние от предыдущей точки;
- кнопка открытия каждой точки в Яндекс Картах;
- адаптивный интерфейс для телефона;
- health endpoint `/health`.

## Установка

После получения ветки/релиза обновите окружение проекта:

```bash
cd /path/to/courier-router
source .venv/bin/activate
pip install -e '.[dev]'
```

Добавьте в `.env`:

```dotenv
WEB_SESSION_SECRET=<random-64-hex-string>
WEB_HOST=127.0.0.1
WEB_PORT=8080
WEB_HTTPS_ONLY=1
WEB_USERS_PATH=data/web/users.json
WEB_RUNS_PATH=data/web/runs
```

Секрет можно создать так:

```bash
openssl rand -hex 32
```

## Создание пользователей

```bash
courier-web user-add tolya
courier-web user-add partner
```

Команда дважды запросит пароль. Файл пользователей создаётся с правами `0600`, где это поддерживает ОС.

## Локальный запуск

Для локальной проверки без HTTPS временно укажите:

```dotenv
WEB_HTTPS_ONLY=0
```

Затем:

```bash
courier-web serve
```

Откройте `http://127.0.0.1:8080`.

Для рабочего доступа через интернет верните `WEB_HTTPS_ONLY=1` и публикуйте приложение только через HTTPS reverse proxy.

## systemd на Raspberry Pi

Пример `/etc/systemd/system/courier-router-web.service`:

```ini
[Unit]
Description=Courier Router Web
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=toligrim
WorkingDirectory=/path/to/courier-router
EnvironmentFile=/path/to/courier-router/.env
ExecStart=/path/to/courier-router/.venv/bin/courier-web serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

После создания:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now courier-router-web
sudo systemctl status courier-router-web
```

## Публикация через VPS

Рекомендуемая схема для домашнего Raspberry Pi:

```text
телефон/ноутбук
      |
    HTTPS
      |
Caddy на VPS
      |
Tailscale / WireGuard / FRP
      |
Raspberry Pi 127.0.0.1:8080
```

Не открывайте порт FastAPI напрямую в интернет.

Если VPS видит Raspberry Pi по Tailscale/WireGuard, Caddyfile на VPS может выглядеть так:

```caddyfile
route.example.ru {
    reverse_proxy <RASPBERRY_TUNNEL_IP>:8080
}
```

Если используется FRP, `reverse_proxy` должен указывать на локальный порт VPS, на который FRP публикует `127.0.0.1:8080` Raspberry Pi.

## Формат таблицы

Сохраняется существующий порядок колонок:

1. тип операции;
2. номер заказа;
3. телефон;
4. район;
5. адрес;
6. подъезд/этаж;
7. временное окно;
8. оплата;
9. комментарий (опционально).

CSV может быть UTF-8/UTF-8 BOM или Windows-1251. Разделитель определяется автоматически среди `;`, `,` и tab.

## Данные

По умолчанию веб-запуски лежат в `data/web/runs/<run-id>/`:

- исходный загруженный файл;
- `meta.json`;
- `route.json`;
- `itinerary.txt`;
- `route.png`;
- `geocoding-report.json`.

Пароли и секреты в Git не добавляются.

## Ограничения v1

- расчёт выполняется синхронно; для двух пользователей это нормально, но очереди задач пока нет;
- ручное drag-and-drop изменение порядка точек пока не реализовано;
- нет отдельной страницы предварительного подтверждения распознанных адресов;
- карта использует публичные OpenStreetMap tiles, что подходит для небольшого частного использования, но не для массового сервиса;
- навигацию между точками приложение не заменяет: для этого используется Яндекс Карты/Навигатор.
