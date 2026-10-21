from __future__ import annotations
import time
import httpx

ORS_BASE_URL = "https://api.heigit.org/openrouteservice/v2"
RETRYABLE_ROUTING_STATUS = {500, 502, 503, 504}


class ORSRouter:
    name = "ors"

    def __init__(self, api_key: str, timeout: float = 60):
        if not api_key:
            raise RuntimeError("Для ROUTER=ors нужен ORS_API_KEY")
        self.key = api_key
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            headers={"Authorization": api_key},
        )

    def _post(self, url: str, payload: dict):
        for attempt in range(3):
            try:
                response = self.client.post(url, json=payload)
            except httpx.TransportError:
                if attempt == 2:
                    raise
            else:
                if response.status_code not in RETRYABLE_ROUTING_STATUS or attempt == 2:
                    response.raise_for_status()
                    return response
            time.sleep(0.5 * (2 ** attempt))

    def matrix(self, coords: list[tuple[float, float]]):
        # coords input lat,lon; ORS uses lon,lat
        locs = [[lon, lat] for lat, lon in coords]
        r = self._post(
            f"{ORS_BASE_URL}/matrix/driving-car",
            {"locations": locs, "metrics": ["duration", "distance"]},
        )
        j = r.json()
        return j["durations"], j["distances"]

    def geometry(self, coords: list[tuple[float, float]]):
        locs = [[lon, lat] for lat, lon in coords]
        r = self._post(
            f"{ORS_BASE_URL}/directions/driving-car/geojson",
            {"coordinates": locs, "instructions": False},
        )
        j = r.json()
        line = j["features"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat in line]


class OSRMRouter:
    name = "osrm"

    def __init__(self, base_url: str = "http://127.0.0.1:5000", timeout: float = 60):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def matrix(self, coords: list[tuple[float, float]]):
        pts = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
        r = self.client.get(
            f"{self.base}/table/v1/driving/{pts}",
            params={"annotations": "duration,distance"},
        )
        r.raise_for_status()
        j = r.json()
        return j["durations"], j["distances"]

    def geometry(self, coords: list[tuple[float, float]]):
        pts = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
        r = self.client.get(
            f"{self.base}/route/v1/driving/{pts}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
        )
        r.raise_for_status()
        line = r.json()["routes"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat in line]
