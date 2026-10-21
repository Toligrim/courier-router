"""Read official GAR metadata and selected members of its large ZIP over HTTP Range."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import io
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from zipfile import ZipFile

import httpx

LATEST_URL = "https://fias.nalog.ru/WebServices/Public/GetLastDownloadFileInfo"
ALLOWED_HOSTS = {"fias.nalog.ru", "fias-file.nalog.ru"}


def official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError("Ожидалась HTTPS-ссылка на официальный сервер ФНС")
    return url


def request(client: httpx.Client, url: str, headers=None) -> httpx.Response:
    """Bounded requests; validate every redirect, and never download a full ZIP by accident."""
    for redirect in range(6):
        official_url(url)
        for attempt in range(3):
            try:
                response = client.send(client.build_request("GET", url, headers=headers), stream=True, follow_redirects=False)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                response.close()
                time.sleep(attempt + 1)
                continue
            break
        if response.is_redirect:
            location = response.headers.get("location")
            next_url = str(response.url.join(location)) if location else ""
            response.close()
            url = next_url
            continue
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response
    raise ValueError("Слишком много перенаправлений ФНС")


def download(client, url, *, max_bytes, headers=None, validate=None, on_chunk=None):
    """Retry interrupted bodies from the beginning; never return a partial range."""
    for attempt in range(3):
        response = None
        try:
            response = request(client, url, headers)
            expected = validate(response) if validate else None
            data = bytearray()
            for chunk in response.iter_bytes():
                if on_chunk:
                    on_chunk(len(chunk))
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("ФНС вернула слишком большой ответ или лишние байты диапазона")
                data.extend(chunk)
            if expected is not None and len(data) != expected:
                raise httpx.RemoteProtocolError("Обрыв загрузки диапазона ФНС")
            return response.headers, bytes(data)
        except httpx.TransportError:
            if attempt == 2:
                raise
        finally:
            if response is not None:
                response.close()
        time.sleep(attempt + 1)


def latest_release(client: httpx.Client) -> dict:
    import json
    _, data = download(client, LATEST_URL, max_bytes=1024 * 1024)
    info = json.loads(data)
    version = str(info.get("VersionId", ""))
    if not re.fullmatch(r"\d{1,20}", version):
        raise ValueError("ФНС не вернула корректную версию")
    return {"version": version, "date": str(info.get("Date", "")),
            "url": official_url(info["GarXMLFullURL"])}


class RangeReader(io.RawIOBase):
    """Seekable, bounded reader for zipfile (including ZIP64). Cache four 1 MiB blocks."""
    block_size = 1024 * 1024

    def __init__(self, client: httpx.Client, url: str, max_bytes: int = 1024**3):
        self.client, self.url = client, official_url(url)
        self.position, self.transferred, self.max_bytes = 0, 0, max_bytes
        self.blocks = OrderedDict()
        self.etag = None
        def validate(response):
            match = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("content-range", ""))
            if response.status_code != 206 or not match:
                raise ValueError("Сервер ФНС не поддержал HTTP Range; используйте локальный ZIP")
            self.size = int(match.group(1))
            self.etag = response.headers.get("etag")
            return 1
        download(client, url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
                 validate=validate, max_bytes=1, on_chunk=self._count_bytes)

    def _count_bytes(self, size):
        self.transferred += size
        if self.transferred > self.max_bytes:
            raise ValueError("Превышен лимит выборочной загрузки ГАР")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        position = offset if whence == io.SEEK_SET else (
            self.position + offset if whence == io.SEEK_CUR else self.size + offset)
        if whence not in {io.SEEK_SET, io.SEEK_CUR, io.SEEK_END} or position < 0:
            raise ValueError("Invalid seek")
        self.position = position
        return position

    def _block(self, number):
        if number in self.blocks:
            self.blocks.move_to_end(number)
            return self.blocks[number]
        start = number * self.block_size
        end = min(self.size, start + self.block_size) - 1
        expected = end - start + 1
        if self.transferred + expected > self.max_bytes:
            raise ValueError("Превышен лимит выборочной загрузки ГАР")
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        if self.etag:
            headers["If-Match"] = self.etag
        def validate(response):
            if (response.status_code != 206 or response.headers.get("content-range")
                    != f"bytes {start}-{end}/{self.size}"):
                raise ValueError("Неполный или изменившийся архив ФНС")
            if self.etag and response.headers.get("etag") not in {None, self.etag}:
                raise ValueError("Архив ФНС изменился во время загрузки")
            return expected
        _, data = download(self.client, self.url, headers=headers, validate=validate,
                           max_bytes=expected, on_chunk=self._count_bytes)
        self.blocks[number] = data
        if len(self.blocks) > 4:
            self.blocks.popitem(last=False)
        return self.blocks[number]

    def read(self, size=-1):
        size = min(self.size - self.position, size if size >= 0 else self.size - self.position)
        if size > 32 * 1024**2:
            raise ValueError("Слишком большое чтение ZIP за один раз")
        chunks = []
        while size > 0:
            number, offset = divmod(self.position, self.block_size)
            data = self._block(number)[offset:offset + size]
            chunks.append(data)
            self.position += len(data)
            size -= len(data)
        return b"".join(chunks)


@contextmanager
def gar_archive(source: str | Path, client: httpx.Client):
    if str(source).startswith("https://"):
        with RangeReader(client, str(source)) as reader, ZipFile(reader) as archive:
            yield archive
    else:
        with ZipFile(Path(source)) as archive:
            yield archive
