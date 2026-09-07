from __future__ import annotations
import httpx

class ORSRouter:
    name = "ors"
    def __init__(self, api_key: str, timeout: float = 60):
        if not api_key:
            raise RuntimeError("Для ROUTER=ors нужен ORS_API_KEY")
        self.key = api_key
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": api_key})

    def matrix(self, coords: list[tuple[float,float]]):
        # coords input lat,lon; ORS uses lon,lat
        locs = [[lon, lat] for lat, lon in coords]
        r = self.client.post(
            "https://api.openrouteservice.org/v2/matrix/driving-car",
            json={"locations": locs, "metrics": ["duration", "distance"]},
        )
        r.raise_for_status()
        j = r.json()
        return j["durations"], j["distances"]

    def geometry(self, coords: list[tuple[float,float]]):
        locs = [[lon, lat] for lat, lon in coords]
        r = self.client.post(
            "https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            json={"coordinates": locs, "instructions": False},
        )
        r.raise_for_status()
        j = r.json()
        line = j["features"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat in line]

class OSRMRouter:
    name = "osrm"
    def __init__(self, base_url: str = "http://127.0.0.1:5000", timeout: float = 60):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def matrix(self, coords: list[tuple[float,float]]):
        pts = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
        r = self.client.get(f"{self.base}/table/v1/driving/{pts}",
                            params={"annotations": "duration,distance"})
        r.raise_for_status()
        j = r.json()
        return j["durations"], j["distances"]

    def geometry(self, coords: list[tuple[float,float]]):
        pts = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coords)
        r = self.client.get(f"{self.base}/route/v1/driving/{pts}",
                            params={"overview": "full", "geometries": "geojson", "steps": "false"})
        r.raise_for_status()
        line = r.json()["routes"][0]["geometry"]["coordinates"]
        return [(lat, lon) for lon, lat in line]
