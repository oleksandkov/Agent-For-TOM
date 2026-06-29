"""TOM runtime configuration.

`Settings` is a Pydantic-settings model loaded from environment variables
and an optional `.env` file. Section 3 will add data-dir and keyring paths;
Section 5 will add provider keys; Section 7 will add MCP server config.

Hard invariant: no value here may cause the backend to bind anywhere other
than the loopback interface. See AGENTS.md.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the TOM backend."""

    model_config = SettingsConfigDict(
        env_prefix="TOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 7878


def get_settings() -> Settings:
    """Return a freshly-loaded Settings instance.

    A function (not a module-level singleton) so tests can patch the env
    without leaking state between cases.
    """
    return Settings()


__all__: list[str] = ["Settings", "get_settings"]
