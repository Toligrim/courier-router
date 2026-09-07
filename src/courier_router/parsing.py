from __future__ import annotations
import csv
import re
from pathlib import Path
from openpyxl import load_workbook
from .domain import Operation, Payment, Stop, TimeWindow

WINDOW_RE = re.compile(r"(?P<h1>\d{1,2})[:.](?P<m1>\d{2})\s*[-–—]\s*(?P<h2>\d{1,2})[:.](?P<m2>\d{2})")
MONEY_RE = re.compile(r"(?P<amount>\d[\d\s]*)")


def parse_operation(value: object) -> Operation:
    s = str(value or "").strip().lower()
    if s.startswith("заб"):
        return Operation.PICKUP
    if s.startswith("отв") or s.startswith("дост"):
        return Operation.DELIVERY
    raise ValueError(f"Неизвестный тип операции: {value!r}")


def parse_window(value: object) -> TimeWindow | None:
    s = str(value or "").strip()
    if not s:
        return None
    m = WINDOW_RE.search(s)
    if not m:
        raise ValueError(f"Не удалось разобрать временное окно: {s!r}")
    a = int(m["h1"]) * 60 + int(m["m1"])
    b = int(m["h2"]) * 60 + int(m["m2"])
    if b < a:
        raise ValueError(f"Окно заканчивается раньше начала: {s!r}")
    return TimeWindow(a, b, s)


def parse_payment(value: object) -> Payment:
    s = str(value or "").strip()
    if not s:
        return Payment(raw="")
    low = s.lower()
    if "бесплат" in low:
        return Payment(amount_rub=0, method="free", raw=s)
    m = MONEY_RE.search(low.replace("\xa0", " "))
    amount = int(re.sub(r"\s+", "", m["amount"])) if m else None
    if "нал" in low:
        method = "cash"
    elif "перев" in low or "кар" in low:
        method = "transfer"
    else:
        method = "unknown"
    return Payment(amount_rub=amount, method=method, raw=s)


def normalize_phone(value: object) -> str:
    s = str(value or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _rows_to_stops(rows, default_service_min: int = 10) -> list[Stop]:
    stops: list[Stop] = []
    for row_no, row in enumerate(rows, start=1):
        vals = list(row[:9]) + [None] * max(0, 9 - len(row))
        if not any(v not in (None, "") for v in vals[:8]):
            continue
        try:
            op = parse_operation(vals[0])
            order_no = int(float(vals[1]))
        except Exception as e:
            raise ValueError(f"Строка {row_no}: {e}") from e
        address = str(vals[4] or "").strip()
        if not address:
            raise ValueError(f"Строка {row_no}: пустой адрес")
        stops.append(Stop(
            source_row=row_no,
            operation=op,
            order_no=order_no,
            phone=normalize_phone(vals[2]),
            district=str(vals[3] or "").strip(),
            address_raw=address,
            access=str(vals[5] or "").strip(),
            window=parse_window(vals[6]),
            payment=parse_payment(vals[7]),
            comment=str(vals[8] or "").strip(),
            service_min=default_service_min,
        ))
    if not stops:
        raise ValueError("В таблице не найдено ни одной точки")
    return stops


def read_excel(path: str | Path, default_service_min: int = 10) -> list[Stop]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    return _rows_to_stops(ws.iter_rows(values_only=True), default_service_min)


def read_csv(path: str | Path, default_service_min: int = 10) -> list[Stop]:
    path = Path(path)
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV должен быть в UTF-8 или Windows-1251")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    return _rows_to_stops(csv.reader(text.splitlines(), dialect), default_service_min)


def read_table(path: str | Path, default_service_min: int = 10) -> list[Stop]:
    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        return read_excel(path, default_service_min)
    if suffix == ".csv":
        return read_csv(path, default_service_min)
    raise ValueError("Поддерживаются файлы .xlsx и .csv")
