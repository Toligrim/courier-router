# Diagnostics Implementation Plan

> **For agentic workers:** Execute inline with basic-loop and TDD; obtain one independent read-only review before deployment.

**Goal:** Store useful bounded diagnostics and show truthful, understandable errors.
**Architecture:** Python logging, JSON rotating files, ContextVars, typed geocoder failures, systemd LogsDirectory.
**Tech Stack:** Existing Python/FastAPI/Playwright/pytest; no new runtime dependencies.
**Spec:** `docs/superpowers/specs/2026-10-26-diagnostics-design.md`

## Global Constraints

- About 30 MiB maximum application logs, file 0600, directory 0750.
- Preserve current address validation, cached successes, aliases and legacy crosscheck.
- Never log request bodies, credentials, contact/payment columns or traceback locals.
- Existing production venv and unrelated VPS services must be preserved.

## Review Focus

- Concurrent requests and worker threads must retain the correct request/run IDs.
- An exception in a batch must not destroy other addresses' successful results.
- Timeout, empty extraction and explicit not-found must not share one UI category.
- Error handling must not leak credentials through exception strings or URLs.
- Unwritable logs and rotation must not break requests or grow storage without bounds.

### Task 1: Structured diagnostics

Files: create `src/courier_router/diagnostics.py`, `tests/test_diagnostics.py`.
Interfaces: `configure_logging()`, `log_event(event, **fields)`,
`log_context(**fields)`, `stage(name, **fields)`.

- [ ] Write and run failing tests for JSON, tracebacks, redaction, rotation,
  thread context, independent concurrent contexts and unwritable destinations.
- [ ] Implement standard-library logging with bounded files and stderr fallback.
- [ ] Run diagnostics tests; confirm failures became passing tests.

### Task 2: Typed geocoding failures and user messages

Files: create `errors.py`, tests; modify `yandex_maps.py`, `geocode.py`,
`cli.py`, `web.py`, existing relevant tests.
Interfaces: `GeocodingError(code, message)`, `user_error(exc)`; batch results
may include a typed failure for one address. Context from Task 1 is inherited.

- [ ] Write and run failing tests for timeout versus extraction, retries,
  partial batch success, no error caching, and UI without exception leakage.
- [ ] Preserve failure reasons, extend coordinate wait, retry timeouts once.
- [ ] Map exceptions and chained causes to helpful user messages plus request ID.
- [ ] Instrument requests, pipeline stages, geocoder/cache and external routing.
- [ ] Run targeted tests and the full suite; investigate every regression.

### Task 3: Operability and release

Files: add `ops/courier-router-diagnostics.conf`, `docs/DIAGNOSTICS.md`;
update `.env.example` and README.

- [ ] Document path, rotation, searching by ID/time, permissions and rollback.
- [ ] Independent read-only review with full diff and test evidence; fix findings.
- [ ] Commit the verified change; stage a new VPS release and systemd drop-in.
- [ ] Validate installed config and tests before restart; restart only web service.
- [ ] Verify health, real-file build and a controlled timeout via the real web
  stack using isolated test data; verify matching persisted JSON events.
- [ ] Report deployed revision, verification, log location and remaining limits.
