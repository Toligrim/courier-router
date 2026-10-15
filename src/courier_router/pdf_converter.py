from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from PIL import Image


class PdfConversionError(ValueError):
    """The PDF did not match the supported route-sheet template."""


@dataclass(frozen=True)
class PdfOrderRow:
    operation: str
    order_no: int
    phone: str
    district: str
    address: str
    window: str
    payment: str
    comment: str = ""

    def as_xlsx_row(self) -> tuple[str, int, str, str, str, str, str, str, str]:
        # The ninth column is the optional comment. Column six is access/floor,
        # which is not present in the partner's PDF and therefore stays empty.
        return (
            self.operation,
            self.order_no,
            self.phone,
            self.district,
            self.address,
            "",
            self.window,
            self.payment,
            self.comment,
        )


_ROW_RE = re.compile(
    r"^\s*(?P<sequence>\d{1,3})\s+"
    r"(?P<operation>Забор|Отвоз|Доставка)\s+"
    r"#?\s*(?P<order>\d+)\b",
    re.IGNORECASE,
)
_NUMBERED_LINE_RE = re.compile(r"^\s*(?P<sequence>\d{1,3})\s+\S")
_PHONE_RE = re.compile(
    r"(?P<phone>(?:\+?\s*7|8)\s*\(?\d{3}\)?[\s-]*\d{3}[\s-]*\d{2}[\s-]*\d{2})"
)
_WINDOW_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\s*[-–—]\s*\d{1,2}[:.]\d{2}\b")
_AMOUNT_RE = re.compile(r"(?<!\d)(?P<amount>\d{2,6})\s*[РPР]?\b", re.IGNORECASE)
_PAYMENT_METHOD_RE = re.compile(
    r"(?i)\b(?:нал\w*|безнал\w*|перевод\w*|карт\w*|бесплат\w*)\b"
)
_DISTRICTS = (
    "Адмиралтейский", "Василеостровский", "Выборгский", "Калининский",
    "Кировский", "Колпинский", "Красногвардейский", "Красносельский",
    "Кронштадтский", "Курортный", "Московский", "Невский", "Петроградский",
    "Петродворцовый", "Приморский", "Пушкинский", "Фрунзенский", "Центральный",
)
_DISTRICT_RE = re.compile(
    r"(?P<address>.+?)\s+(?P<district>"
    + "|".join(map(re.escape, _DISTRICTS))
    + r")\s*$",
    re.IGNORECASE,
)
_HEADER_WORDS = ("тип", "заказ", "адрес", "район", "телефон", "время")
_DECLARED_COUNT_RE = re.compile(r"\bвсего\s+(?P<count>\d+)\s+выезд", re.IGNORECASE)
_MAX_PDF_BYTES = 25 * 1024 * 1024
_MAX_PDF_PAGES = 4
_MAX_RENDER_PIXELS = 16_000_000
_OCR_DPI = 300
_MAX_CONVERSION_SECONDS = 120
_OCR_PROCESS_TIMEOUT = 30
_OCR_SLOT = threading.BoundedSemaphore(1)


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _normalize_address(value: str) -> str:
    value = _normalize_space(value)
    # Tesseract occasionally reads the beginning of «г Санкт-Петербург» as
    # «рсанкт- Петербург» or «санкт“ Петербург».
    value = re.sub(
        r"(?i)(?:^|\s)[рr]?санкт[\s\-\"“”']*петербург",
        " г Санкт-Петербург",
        value,
    ).strip()
    value = re.sub(r"(?i)\bг\s+г\s+санкт", "г Санкт", value)
    # Common OCR variants in the house/building part of this form.
    value = re.sub(r"(?i)\bд\s+з\s*к\s*(\d+)", r"д 3 к \1", value)
    value = re.sub(r"(?i)(\d)к(\d)", r"\1 к \2", value)
    value = re.sub(r"(?i)\bкв\s*\.\s*", "кв. ", value)
    return _normalize_space(value)


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits[0] in "78":
        digits = "8" + digits[1:]
    if len(digits) != 11 or digits[0] != "8":
        raise PdfConversionError(f"Некорректный телефон в PDF: {value!r}")
    return f"8 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"


def _payment_from_tail(value: str) -> str:
    value = value.replace("—", " ").replace("–", " ").replace("-", " ")
    amount = _AMOUNT_RE.search(value)
    amount_text = amount.group("amount") if amount else ""
    rest = value
    if amount:
        rest = rest[:amount.start()] + " " + rest[amount.end():]
    methods = _PAYMENT_METHOD_RE.findall(rest)
    method = _normalize_space(" ".join(methods)).strip("—–- ,")
    leftovers = _PAYMENT_METHOD_RE.sub(" ", rest)
    leftovers = re.sub(r"[\s,.;:/]+", "", leftovers)
    if leftovers:
        raise PdfConversionError(f"Неизвестное значение оплаты в PDF: {value!r}")
    if amount_text and method:
        return f"{amount_text} / {method}"
    return amount_text or method


def _looks_like_address_continuation(line: str) -> bool:
    if re.fullmatch(r"\d{1,5}", line):
        return True
    return bool(re.search(r"\d", line) and re.search(
        r"(?i)\b(?:д|дом|кв|корп|к|ул|пр(?:-кт)?|пл|наб|ш|пер|проезд|б-р|аллея)\b\.?",
        line,
    ))


def _split_row_lines(lines: list[str], row_index: int) -> tuple[str, list[str]]:
    start = _normalize_space(lines[0])
    extras = []
    for line in lines[1:]:
        line = _normalize_space(line)
        if not line:
            continue
        # Continuation lines in this template are address cells printed below
        # the row (for example an apartment number or a wrapped street name).
        if not _looks_like_address_continuation(line):
            raise PdfConversionError(
                f"Строка {row_index}: неизвестное продолжение после строки заказа: {line!r}"
            )
        extras.append(line)
    return start, extras


def _parse_row(lines: list[str], row_index: int) -> PdfOrderRow:
    start, extras = _split_row_lines(lines, row_index)
    match = _ROW_RE.match(start)
    if not match:
        raise PdfConversionError(f"Строка {row_index}: не удалось разобрать начало строки: {start!r}")

    payload = start[match.end():].strip()
    phone_match = _PHONE_RE.search(payload)
    if not phone_match:
        raise PdfConversionError(f"Строка {row_index}: не найден телефон: {start!r}")

    prefix = _normalize_space(payload[:phone_match.start()])
    district_match = _DISTRICT_RE.match(prefix)
    if not district_match:
        raise PdfConversionError(
            f"Строка {row_index}: не найден район перед телефоном: {prefix!r}"
        )
    address = district_match.group("address")
    district = district_match.group("district")
    if extras:
        address = f"{address} {' '.join(extras)}"
    address = _normalize_address(address)

    tail = payload[phone_match.end():]
    window_match = _WINDOW_RE.search(tail)
    if not window_match:
        raise PdfConversionError(f"Строка {row_index}: не найдено временное окно: {start!r}")
    window = re.sub(r"\s+", "", window_match.group(0)).replace(".", ":")
    payment = _payment_from_tail(tail[:window_match.start()])
    comment = _normalize_space(tail[window_match.end():]).strip("—–- ")

    return PdfOrderRow(
        operation=match.group("operation").capitalize(),
        order_no=int(match.group("order")),
        phone=_normalize_phone(phone_match.group("phone")),
        district=district.capitalize(),
        address=address,
        window=window,
        payment=payment,
        comment=comment,
    )


def parse_ocr_text(text: str) -> list[PdfOrderRow]:
    """Parse OCR text from the partner's printed route-sheet template."""
    # Keep page boundaries: each printed page repeats its title and headings,
    # which must not become a continuation of the previous page's last address.
    lines = []
    for page in text.replace("\r", "").split("\f"):
        if not page.strip():
            continue
        page_lines = [line.strip() for line in page.splitlines()]
        header_index = next((
            i for i, line in enumerate(page_lines)
            if "заказ" in line.lower() and "адрес" in line.lower()
        ), None)
        if header_index is None:
            raise PdfConversionError("В PDF не найдена строка заголовков таблицы.")
        lines.extend(page_lines[header_index + 1:])
    if not lines:
        raise PdfConversionError("В PDF не найдена строка заголовков таблицы.")
    for line in lines:
        if _NUMBERED_LINE_RE.match(line) and not _ROW_RE.match(line) and not line.isdigit():
            raise PdfConversionError(f"Не удалось распознать строку заказа: {line!r}")

    starts = [i for i, line in enumerate(lines) if _ROW_RE.match(line)]
    if not starts:
        raise PdfConversionError("В PDF не найдены строки заказов. Проверьте шаблон и качество скана.")
    for line in lines[:starts[0]]:
        if line:
            raise PdfConversionError(f"Неизвестный текст перед первой строкой заказа: {line!r}")

    declared = [int(match.group("count")) for match in _DECLARED_COUNT_RE.finditer(text)]
    if not declared:
        raise PdfConversionError("В PDF не найдено заявленное количество выездов.")
    if any(count != len(starts) for count in declared):
        raise PdfConversionError(
            f"Количество заказов не совпало: в заголовке {declared[0]}, распознано {len(starts)}."
        )

    rows: list[PdfOrderRow] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        sequence = int(_ROW_RE.match(lines[start]).group("sequence"))
        if sequence != n + 1:
            raise PdfConversionError(
                f"Нарушена последовательность строк: ожидалась {n + 1}, найдена {sequence}."
            )
        rows.append(_parse_row(lines[start:end], n + 1))
    order_numbers = [row.order_no for row in rows]
    if len(set(order_numbers)) != len(order_numbers):
        raise PdfConversionError("В PDF обнаружены повторяющиеся номера заказов.")
    return rows


def _ocr_score(text: str) -> int:
    low = text.lower()
    return sum(2 if word in low else 0 for word in _HEADER_WORDS) + (5 if "маршрутный лист" in low else 0)


def _run_ocr(image_path: Path, command: str, language: str, timeout: float) -> str:
    try:
        result = subprocess.run(
            [command, str(image_path), "stdout", "-l", language, "--psm", "6"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except FileNotFoundError as exc:
        raise PdfConversionError(
            f"Не найден {command!r}. Установите poppler-utils, tesseract-ocr и tesseract-ocr-rus."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise PdfConversionError(f"OCR не удалось выполнить: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PdfConversionError("OCR превысил общий лимит времени обработки PDF.") from exc
    return result.stdout


def _ocr_pages(pdf_path: Path, language: str, tesseract_command: str) -> str:
    deadline = time.monotonic() + _MAX_CONVERSION_SECONDS

    def remaining() -> float:
        value = min(_OCR_PROCESS_TIMEOUT, deadline - time.monotonic())
        if value <= 0:
            raise PdfConversionError("Конвертация PDF превысила лимит времени.")
        return value

    if pdf_path.stat().st_size > _MAX_PDF_BYTES:
        raise PdfConversionError(
            f"PDF больше лимита {_MAX_PDF_BYTES // 1024 // 1024} МБ."
        )
    pdfinfo = shutil.which("pdfinfo")
    pdftoppm = shutil.which("pdftoppm")
    if not pdfinfo or not pdftoppm:
        raise PdfConversionError("Не найден poppler-utils (нужны команды pdfinfo и pdftoppm).")
    if not shutil.which(tesseract_command):
        raise PdfConversionError(
            f"Не найден {tesseract_command!r}. Установите tesseract-ocr и tesseract-ocr-rus."
        )

    try:
        info = subprocess.run(
            [pdfinfo, str(pdf_path)], check=True, capture_output=True, text=True, timeout=min(15, remaining())
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PdfConversionError("Не удалось прочитать метаданные PDF.") from exc
    page_match = re.search(r"(?m)^Pages:\s*(\d+)\s*$", info.stdout)
    if not page_match:
        raise PdfConversionError("Не удалось определить количество страниц PDF.")
    page_count = int(page_match.group(1))
    if page_count > _MAX_PDF_PAGES:
        raise PdfConversionError(f"В PDF больше {_MAX_PDF_PAGES} страниц.")

    for page_number in range(1, page_count + 1):
        try:
            page_info = info if page_number == 1 else subprocess.run(
                [pdfinfo, "-f", str(page_number), "-l", str(page_number), str(pdf_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=min(15, remaining()),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise PdfConversionError("Не удалось прочитать размер страницы PDF.") from exc
        size_match = re.search(
            r"(?m)^Page(?:\s+\d+)?\s+size:\s*([\d.]+)\s+x\s+([\d.]+)\s+pts",
            page_info.stdout,
        )
        if not size_match:
            raise PdfConversionError("Не удалось определить размер страницы PDF.")
        width_px = float(size_match.group(1)) / 72 * _OCR_DPI
        height_px = float(size_match.group(2)) / 72 * _OCR_DPI
        if width_px * height_px > _MAX_RENDER_PIXELS:
            raise PdfConversionError(f"Страница {page_number} слишком большая для безопасного OCR.")

    with tempfile.TemporaryDirectory(prefix="courier-pdf-") as temp:
        prefix = Path(temp) / "page"
        try:
            subprocess.run(
                [pdftoppm, "-png", "-r", str(_OCR_DPI), "-f", "1", "-l", str(page_count), str(pdf_path), str(prefix)],
                check=True,
                capture_output=True,
                text=True,
                timeout=remaining(),
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfConversionError("Рендеринг PDF превысил лимит времени.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise PdfConversionError(f"PDF не удалось отрендерить: {detail}") from exc

        pages = sorted(Path(temp).glob("page-*.png"))
        if not pages:
            raise PdfConversionError("PDF не содержит страниц для распознавания.")

        ocr_text: list[str] = []
        for page in pages:
            with Image.open(page) as image:
                # Detect rotation on a small preview. Running OCR four times at
                # 300 DPI wastes the time budget on sideways/upside-down text.
                preview = image.copy()
                preview.thumbnail((2000, 2000))
                candidates: list[tuple[int, int]] = []
                for angle in (0, 90, 180, 270):
                    rotated_path = Path(temp) / f"{page.stem}-preview.png"
                    preview.rotate(angle, expand=True).save(rotated_path)
                    text = _run_ocr(rotated_path, tesseract_command, language, remaining())
                    score = _ocr_score(text)
                    candidates.append((score, angle))
                    if score >= 8:
                        break
                score, angle = max(candidates, key=lambda item: item[0])
                if score < 8:
                    raise PdfConversionError(
                        f"Не удалось уверенно определить таблицу на странице {page.name}."
                    )
                rotated_path = Path(temp) / f"{page.stem}-oriented.png"
                image.rotate(angle, expand=True).save(rotated_path)
                ocr_text.append(_run_ocr(rotated_path, tesseract_command, language, remaining()))
        return "\f".join(ocr_text)


def write_xlsx(rows: list[PdfOrderRow], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказы"
    for row in rows:
        sheet.append(row.as_xlsx_row())
    workbook.save(output)
    return output


def read_pdf_rows(
    pdf_path: str | Path,
    *,
    language: str = "rus+eng",
    tesseract_command: str = "tesseract",
) -> list[PdfOrderRow]:
    if not _OCR_SLOT.acquire(blocking=False):
        raise PdfConversionError("Сейчас уже выполняется другая PDF-конвертация. Повторите позже.")
    try:
        pdf = Path(pdf_path)
        if not pdf.is_file():
            raise PdfConversionError(f"PDF не найден: {pdf}")
        text = _ocr_pages(pdf, language, tesseract_command)
        return parse_ocr_text(text)
    finally:
        _OCR_SLOT.release()


def convert_pdf_to_xlsx(
    pdf_path: str | Path,
    output_path: str | Path,
    *,
    language: str = "rus+eng",
    tesseract_command: str = "tesseract",
) -> tuple[Path, list[PdfOrderRow]]:
    rows = read_pdf_rows(pdf_path, language=language, tesseract_command=tesseract_command)
    return write_xlsx(rows, output_path), rows
