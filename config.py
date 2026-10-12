"""Loads and validates settings from .env / the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    owner_user_id: int | None  # None: the owner of the server the message comes from
    allowed_channel_ids: frozenset[int]
    debug: bool


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        load_dotenv()
        env = os.environ

    errors: list[str] = []

    def required(key: str) -> str:
        value = env.get(key, "").strip()
        if not value:
            errors.append(f"{key} is required")
        return value

    def snowflake(key: str, raw: str) -> int:
        if raw and not raw.isdigit():
            errors.append(f"{key} must be a numeric Discord ID, got {raw!r}")
            return 0
        return int(raw) if raw else 0

    token = required("DISCORD_TOKEN")
    owner = snowflake("OWNER_USER_ID", env.get("OWNER_USER_ID", "").strip()) or None
    channels = frozenset(
        snowflake("ALLOWED_CHANNEL_IDS", part.strip())
        for part in required("ALLOWED_CHANNEL_IDS").split(",")
        if part.strip()
    )
    config = Config(
        discord_token=token,
        owner_user_id=owner,
        allowed_channel_ids=channels,
        debug=env.get("DEBUG", "false").strip().lower() in ("1", "true", "yes"),
    )

    if errors:
        raise ConfigError("; ".join(errors))
    return config
