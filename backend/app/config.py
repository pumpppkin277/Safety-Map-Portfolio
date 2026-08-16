import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "sqlite:///./data/keli.db").strip()
    if value.startswith("mysql://"):
        return value.replace("mysql://", "mysql+pymysql://", 1)
    return value


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "壳里安全地图 API")
    environment: str = os.getenv("APP_ENV", "development")
    database_url: str = _database_url()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    tencent_map_key: str = os.getenv("TENCENT_MAP_KEY", "")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    allowed_origins: List[str] = None
    map_radius_meters: int = int(os.getenv("MAP_RADIUS_METERS", "3000"))
    poi_limit_per_category: int = int(os.getenv("POI_LIMIT_PER_CATEGORY", "12"))

    def __post_init__(self) -> None:
        origins = _csv(os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"))
        object.__setattr__(self, "allowed_origins", origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()
