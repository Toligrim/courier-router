from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    dadata_token: str = os.getenv("DADATA_TOKEN", "")
    dadata_secret: str = os.getenv("DADATA_SECRET", "")
    ors_api_key: str = os.getenv("ORS_API_KEY", "")
    osrm_url: str = os.getenv("OSRM_URL", "http://127.0.0.1:5000")
    llm_provider: str = os.getenv("LLM_PROVIDER", "none").lower()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    geocoder: str = os.getenv("GEOCODER", "dadata").lower()
    router: str = os.getenv("ROUTER", "ors").lower()
    db_path: str = os.getenv("DB_PATH", "data/cache/courier-router.db")
    depot_address: str = os.getenv("DEPOT_ADDRESS", "проспект Костюшко, 2, Санкт-Петербург")
    depot_lat: str = os.getenv("DEPOT_LAT", "")
    depot_lon: str = os.getenv("DEPOT_LON", "")
    default_service_min: int = int(os.getenv("DEFAULT_SERVICE_MIN", "10"))
    solver_time_limit_sec: int = int(os.getenv("SOLVER_TIME_LIMIT_SEC", "8"))
    tile_url: str = os.getenv("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    tile_user_agent: str = os.getenv("TILE_USER_AGENT", "courier-router/0.1")
