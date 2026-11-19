from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    keitaro_url: str = "https://tlgk.host"
    keitaro_api_key: str = ""
    keitaro_timeout: float = 30.0
    keitaro_mock: bool = False  # in-memory fake Keitaro for demo/tests

    campaign_name_prefix: str = "test_grimaylo_"
    default_domain: str = "kylovoa.com"
    default_group: str = "FORTESTS"
    default_traffic_source: str = "FORTESTS"
    google_url: str = "https://google.com"

    database_url: str = "sqlite:///./keitaro_wrapper.db"
    report_timezone: str = "Europe/Moscow"

    @property
    def keitaro_api_base(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
