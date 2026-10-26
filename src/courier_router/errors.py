"""Explicit failure categories and safe messages for the web interface."""
from __future__ import annotations
import re
import httpx


class GeocodingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TableError(ValueError):
    """A validation error from the table parser, safe to explain to its owner."""


def user_error(exc: Exception) -> str:
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    row = next((m.group(1) for e in chain if (m := re.search(r"Строка\s+(\d+)", str(e)))), None)
    prefix = f"Строка {row}. " if row else ""
    for error in chain:
        if isinstance(error, GeocodingError):
            messages = {
                "timeout": "Не удалось дождаться ответа сервиса карт. Попробуйте построить маршрут ещё раз через минуту.",
                "unavailable": "Сервис поиска адресов временно недоступен. Попробуйте ещё раз через несколько минут.",
                "blocked": "Сервис карт временно ограничил поиск адресов. Попробуйте ещё раз позже.",
                "extraction": "Не удалось прочитать координаты найденного адреса. Попробуйте ещё раз; если ошибка повторяется, сообщите её номер.",
                "not_found": "По этому адресу нет результата поиска. Пожалуйста, проверьте город, улицу, номер дома и корпус в таблице.",
            }
            return prefix + messages.get(error.code, messages["unavailable"])
        if isinstance(error, TableError):
            return "Проверьте маршрутный лист: " + str(error)[:1000]
        if isinstance(error, httpx.TimeoutException):
            return "Расчёт маршрута занял слишком много времени. Попробуйте ещё раз через минуту."
        if isinstance(error, (httpx.TransportError, httpx.HTTPStatusError)):
            return "Сервис расчёта маршрута временно недоступен. Попробуйте ещё раз позже."
    # Preserve actionable validation messages; do not expose arbitrary exception text.
    if re.match(r"Строка \d+: (?:найденный адрес сильно расходится|геокодер вернул точку вне)", str(exc)):
        return prefix + "Найденная точка не соответствует адресу в таблице. Проверьте адрес и корпус дома."
    return "Не удалось построить маршрут из-за внутренней ошибки. Попробуйте ещё раз; если ошибка повторяется, сообщите её номер."
