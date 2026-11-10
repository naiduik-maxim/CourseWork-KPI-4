from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Налаштування backend."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url : str = (
        "postgresql+asyncpg://telemetry:telemetry@localhost:5432/telemetry"
    )

    model_dir : Path = Path("models/power-iforest-v1")

    electrical_model_dir: Path = Path("models/electrical-iforest-v1")

    log_dir: Path = Path("logs")

    instance_id: str = "api"

    db_pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
    )

    db_max_overflow: int = Field(
        default=5,
        ge=0,
        le=50,
    )

    inference_workers: int = Field(
        default=2,
        ge=1,
        le=8,
    )

    max_body_bytes: int = Field(
        default=65536,
        ge=1024,
        le=1048576,
    )
