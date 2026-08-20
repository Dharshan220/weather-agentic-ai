import json
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SEVERITY_LEVELS = ["NONE", "LOW", "MEDIUM", "HIGH", "EXTREME"]


class LocationConfig(BaseSettings):
    name: str
    lat: float
    lon: float
    use_llm: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "groq"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.2
    llm_timeout_seconds: int = 120

    weather_locations: List[LocationConfig] = []
    schedule_interval_minutes: int = 30

    rain_mm_threshold: float = 20.0
    heat_c_threshold: float = 35.0
    wind_kmh_threshold: float = 40.0
    alert_threshold: str = "HIGH"
    alert_cooldown_hours: int = 6

    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_to: str = ""

    daily_summary_enabled: bool = False
    daily_summary_times: str = "07:00"
    timezone: str = "Asia/Kolkata"

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    db_path: str = "weather.db"

    @field_validator("weather_locations", mode="before")
    @classmethod
    def parse_locations(cls, v):
        if isinstance(v, str):
            v = json.loads(v)
        return v or [{"name": "Chennai", "lat": 13.0827, "lon": 80.2707}]

    @property
    def daily_summary_time_list(self) -> List[str]:
        return [t.strip() for t in self.daily_summary_times.split(",") if t.strip()]

    @property
    def alert_threshold_index(self) -> int:
        return SEVERITY_LEVELS.index(self.alert_threshold.upper())

    @property
    def has_llm_key(self) -> bool:
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()