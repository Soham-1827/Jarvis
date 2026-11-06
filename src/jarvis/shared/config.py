"""Typed runtime settings, loaded from `JARVIS_*` environment variables and validated at startup."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Tailscale hands out addresses from the CGNAT range (IPv4) and fd7a:115c:a1e0::/48 (IPv6).
_TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


def _is_allowed_bind_host(host: str) -> bool:
    """Only loopback and Tailscale addresses: the brain must never listen on a public interface."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or any(addr in net for net in _TAILSCALE_NETS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", frozen=True, extra="ignore")

    data_dir: Path = Field(default=Path("/var/lib/jarvis"))
    bind_hosts: tuple[str, ...] = ("127.0.0.1",)
    port: int = Field(default=8765, ge=1024, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("bind_hosts")
    @classmethod
    def _validate_bind_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        if not hosts:
            raise ValueError("at least one bind host is required")
        bad = [h for h in hosts if not _is_allowed_bind_host(h)]
        if bad:
            raise ValueError(f"bind hosts must be loopback or Tailscale addresses, got {bad}")
        return hosts

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"


def load_settings() -> Settings:
    """Read settings from the environment. Raises pydantic.ValidationError on bad config."""
    return Settings()
