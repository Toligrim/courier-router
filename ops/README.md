# Local deployment

The `courier-router-web.service` unit runs the authenticated web UI on
`127.0.0.1:8100`. Cloudflare Tunnel terminates HTTPS at
`https://courier.kurva-cloud.ru` and forwards to that loopback port.

The active host already has a shared `cloudflared.service`; its ingress entry
is kept in `/etc/cloudflared/config.yml` and the DNS route belongs to the
existing `ledger-miniapp` tunnel. The app-specific ingress fragment is stored
in `cloudflared-courier-route.yml` as a reviewable record of the route.

The former `/etc/systemd/system/courier-router-web.service.d/parser-release.conf`
override was disabled on 2026-10-20. The active service now imports code from
`courier-router/src`, using this checkout's `.env`, virtual environment and data.
`courier-router-parser-release` is an inactive historical checkout. Before deploying,
inspect `systemctl cat courier-router-web.service` and the running process's
`PYTHONPATH`; a CLI check alone does not verify the code served by the website.

Geocoding is configured as `GEOCODER=yandex`, `DADATA_USE_CLEAN=0`,
`YANDEX_CROSSCHECK=0` in the shared `.env`. Verify a real authenticated `POST /routes`
after restarting the service, in addition to `/health`.

## FNS address dictionary

`courier-router-dictionary.service` and `.timer` check GAR releases daily at
04:00 Europe/Moscow, build and validate a separate SQLite snapshot, and publish
it atomically. Install these units only in the deployment that contains the
dictionary code; a feature worktree needs its own paths. Bootstrap, inspection,
rollback, cache rules and installation: [FNS_DICTIONARY.md](../docs/FNS_DICTIONARY.md).
