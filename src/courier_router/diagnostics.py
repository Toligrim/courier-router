"""Bounded JSON diagnostics. Never attach request bodies or traceback locals."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
import traceback
from urllib.parse import urlsplit, urlunsplit


_context: ContextVar[dict] = ContextVar("diagnostic_context", default={})
_logger = logging.getLogger("courier_router")
_SECRET_KEY = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization|cookie", re.I)
_SECRET_HEADER = re.compile(
    r"(?i)(\b(?:authorization|(?:set-)?cookie)[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
_SECRET_PAIR = re.compile(
    r"(?i)(\b[\w-]*(?:password|passwd|secret|token|api[_-]?key)[\w-]*[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|(?:Bearer\s+)?[^\s&\"'<>;,}]+)"
)


def _redact(value: str) -> str:
    for key, secret in os.environ.items():
        if _SECRET_KEY.search(key) and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?<=://)[^/\s@]+@", "[REDACTED]@", value)
    value = _SECRET_HEADER.sub(r"\1[REDACTED]", value)
    return _SECRET_PAIR.sub(r"\1[REDACTED]", value)


def _safe(value, key=""):
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value[:30]]
    if isinstance(value, str):
        return _redact(value)[:16000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _redact(str(value))[:2000]


def safe_url(url: str) -> str:
    """Log a destination without query strings, fragments or credentials."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))[:1000]


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "release": os.getenv("COURIER_RELEASE", "development"),
            "event": record.getMessage(),
            **getattr(record, "event_fields", {}),
        }
        if record.exc_info:
            entry["error_type"] = record.exc_info[0].__name__
            entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(_safe(entry), ensure_ascii=False, allow_nan=False)


class _FileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def handleError(self, record):
        # The console handler still receives this event. Do not let logging stop a route.
        try:
            sys.stderr.write("Courier Router: application log write failed; use the system journal.\n")
        except Exception:
            pass


def configure_logging(path=None, *, max_bytes=5 * 1024 * 1024, backup_count=5, console=None):
    """Configure only our package; leave uvicorn and third-party HTTP logging alone."""
    for handler in _logger.handlers[:]:
        if getattr(handler, "_courier_owned", False):
            _logger.removeHandler(handler)
            handler.close()
    _logger.setLevel(logging.INFO)
    formatter = JsonFormatter()
    stream = logging.StreamHandler(console if console is not None else sys.stderr)
    stream.setFormatter(formatter)
    stream._courier_owned = True
    _logger.addHandler(stream)
    path = Path(path or os.getenv("COURIER_LOG_FILE", "data/logs/application.jsonl"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        handler = _FileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        handler.setFormatter(formatter)
        handler._courier_owned = True
        _logger.addHandler(handler)
    except OSError:
        log_event("logging.file_unavailable", level=logging.ERROR, exc_info=True)
    log_event("logging.ready", log_file=str(path), max_bytes=max_bytes, backup_count=backup_count)


def log_event(event: str, *, level=logging.INFO, exc_info=False, **fields):
    _logger.log(level, event, extra={"event_fields": {**_context.get(), **fields}}, exc_info=exc_info)


@contextmanager
def log_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def stage(name: str, **fields):
    started = time.monotonic()
    with log_context(stage=name, **fields):
        log_event("stage.started")
        try:
            yield
        except Exception:
            log_event("stage.failed", level=logging.ERROR, exc_info=True,
                      elapsed_ms=round((time.monotonic() - started) * 1000, 1))
            raise
        else:
            log_event("stage.completed", elapsed_ms=round((time.monotonic() - started) * 1000, 1))
