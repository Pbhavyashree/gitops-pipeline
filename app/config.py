"""Configuration from environment variables, which is how a container is configured."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Injected by Helm from values.yaml, so the same image runs in every
    # environment and only the configuration differs.
    version: str = "0.1.0"
    environment: str = "local"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="APP_")


settings = Settings()
