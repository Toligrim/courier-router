# Диагностика Courier Router

Приложение пишет JSON Lines через стандартный Python logging.
На production: `/var/log/courier-router/application.jsonl`.
Локально: `data/logs/application.jsonl`; переменная `COURIER_LOG_FILE`
переопределяет путь. Настройка VPS — `ops/courier-router-diagnostics.conf`.
Логи принадлежат `courier-router`, каталог 0750, файлы 0600. Не публикуйте их:
поисковые адреса являются служебными данными.

## Найти ошибку

Пользователь передаёт **номер ошибки**, показанный на странице. Это
`request_id`, который есть и в заголовке HTTP `X-Request-ID`.

```bash
sudo rg -F 'НОМЕР_ОШИБКИ' /var/log/courier-router/application.jsonl*
sudo tail -n 100 /var/log/courier-router/application.jsonl
sudo journalctl -u courier-router-web.service --since '2026-10-26 13:00:00' --no-pager
```

Если `rg` не установлен на сервере, используйте `sudo grep -F`.
Поле `release` содержит установленную ревизию кода (задаётся при развёртывании
через `COURIER_RELEASE`). Время JSON — UTC (`Z`); Москва = UTC+3. `journalctl` использует часовой пояс
сервера, его можно явно задать: `sudo env TZ=Europe/Moscow journalctl ...`.

`run_id` связывает события попытки с каталогом маршрута. События содержат
начала/завершения этапов, длительности в мс, число заказов, использование
кэша, нормализованный поисковый адрес без квартиры, результаты и повторы
поиска, ответы маршрутизатора, проверки координат и исключения с traceback.
Не записываются тела запросов, загруженные таблицы, телефоны, оплаты,
пароли, токены и cookies. Известные секреты окружения и поля авторизации
маскируются. Traceback не включает значения локальных переменных.

Основные события: `route.build.started`, `stage.started/completed/failed`,
`geocoder.lookup.started/completed/failed`, `geocoder.stop.completed`,
`routing.response`, `route.build.failed/completed`, `request.completed`.
Поле `reason` различает `timeout`, `blocked`, `unavailable`, `extraction`.
`not_found` означает пустой поиск, а не техническую ошибку чтения.

## Хранение и ограничения

При достижении 5 MiB файл ротируется в `.1` … `.5`: активный файл и пять
архивов занимают примерно 30 MiB. Срок хранения зависит от интенсивности;
гарантированного хранения за фиксированное число дней нет. Внешний
logrotate для этих файлов не нужен. Журнал не теряется при перезапуске
web или VPS, но старые события вытесняются при ротации. Файловая запись
сбрасывается после каждого события. Это диагностика, а не резервная копия:
при потере VPS локальные логи тоже потеряются.

Поддерживается текущий однопроцессный запуск uvicorn. Перед переходом к
нескольким процессам нужен единый writer (journald/collector/queue), поскольку
RotatingFileHandler не координирует ротацию между процессами. Команды CLI
по умолчанию пишут в локальный журнал; не направляйте несколько процессов
одновременно в production JSON-файл.

Если файл недоступен/диск заполнен, запись остаётся в stderr/journal, а
построение маршрута продолжается. Ищите `logging.file_unavailable` и
`application log write failed`. Ошибки процесса и запуска смотрите через
`journalctl`; приложение не меняет настройки остальных служб journald.

## Установка и откат

Установите drop-in из `ops/courier-router-diagnostics.conf`, выполните
`sudo systemctl daemon-reload`, затем перезапустите **только**
`courier-router-web.service`. systemd создаёт каталог с разрешённой записью
даже при `ProtectSystem=strict`. Проверьте `logging.ready`, права файла и
`curl --fail http://127.0.0.1:8100/health`.

Код выпускается отдельным каталогом релиза и отдельным drop-in `PYTHONPATH`.
Для отката удаляется только drop-in этого релиза, затем daemon-reload и
перезапуск web. Старое `.venv` сохраняется. Логи лежат отдельно от релизов.
