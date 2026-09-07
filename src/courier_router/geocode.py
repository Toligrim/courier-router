from __future__ import annotations
import time
import httpx
from .domain import GeoPoint

QC_CONF = {
    0: ("exact", 0.99),
    1: ("nearest_house", 0.90),
    2: ("street", 0.72),
    3: ("settlement", 0.55),
    4: ("city", 0.45),
    5: ("unknown", 0.30),
}

class DaDataGeocoder:
    def __init__(self, token: str, secret: str, timeout: float = 20):
        if not token or not secret:
            raise RuntimeError("Для DaData нужны DADATA_TOKEN и DADATA_SECRET")
        self.token = token
        self.secret = secret
        self.client = httpx.Client(timeout=timeout)

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        query = f"{district}, {address}" if district and district.lower() not in address.lower() else address
        r = self.client.post(
            "https://cleaner.dadata.ru/api/v1/clean/address",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Secret": self.secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=[query],
        )
        r.raise_for_status()
        data = r.json()[0]
        if data.get("geo_lat") is None or data.get("geo_lon") is None:
            raise ValueError(f"DaData не вернула координаты: {address}")
        qc = int(data.get("qc_geo") if data.get("qc_geo") is not None else 5)
        precision, conf = QC_CONF.get(qc, ("unknown", 0.3))
        return GeoPoint(
            lat=float(data["geo_lat"]),
            lon=float(data["geo_lon"]),
            provider="dadata",
            normalized_address=data.get("result") or address,
            precision=precision,
            confidence=conf,
            provider_ref=data.get("fias_id"),
            raw=data,
        )

class PublicNominatimGeocoder:
    """Small-volume fallback only. Respect OSMF Nominatim public usage policy."""
    def __init__(self, user_agent: str, timeout: float = 20):
        self.user_agent = user_agent
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})
        self._last = 0.0

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        wait = 1.05 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        q = f"{address}, {district}, Россия" if district else f"{address}, Россия"
        r = self.client.get("https://nominatim.openstreetmap.org/search", params={
            "q": q, "format": "jsonv2", "limit": 3, "countrycodes": "ru", "addressdetails": 1,
        })
        self._last = time.monotonic()
        r.raise_for_status()
        items = r.json()
        if not items:
            raise ValueError(f"Nominatim не нашёл адрес: {address}")
        item = items[0]
        typ = item.get("type", "")
        conf = 0.85 if typ in {"house", "building"} else 0.65
        return GeoPoint(
            lat=float(item["lat"]), lon=float(item["lon"]), provider="nominatim",
            normalized_address=item.get("display_name", address),
            precision=typ or "unknown", confidence=conf,
            provider_ref=str(item.get("place_id", "")), raw=item,
        )
