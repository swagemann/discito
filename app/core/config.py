from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    env: Literal["dev", "prod"] = Field(default="dev", alias="APP_ENV")
    log_level: str = "info"
    secret_key: str = "change-me-32-bytes-min"

    # Household timezone: streaks and "today" use local day boundaries.
    timezone: str = "America/Los_Angeles"

    # The single household login for the parent area.
    parent_password: str = "change-me"
    # Optional: when set, a device must enter this once (long-lived cookie) before
    # the child picker is shown, so kids' names aren't public to anyone with the URL.
    household_password: str = ""

    # SQLite file on the persistent /data volume in production.
    database_url: str = "sqlite:///./data/discito.db"

    # faster-whisper sidecar (internal docker network only).
    whisper_url: str = "http://whisper:9000"
    whisper_timeout_s: float = 60.0

    # Dev aid: show a "type what you said" box next to the mic so the recite flow
    # can be exercised without a microphone or the whisper container.
    stt_typed_fallback: bool = False

    # Open question in the PRD: should a peek invalidate the step's clean run?
    # Default: no — peeks are logged and visible to the parent.
    peek_invalidates: bool = False

    @property
    def cookie_secure(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
