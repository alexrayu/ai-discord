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
    owner_user_id: int
    allowed_channel_ids: frozenset[int]
    claude_avatar_url: str | None
    gpt_avatar_url: str | None
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

    def avatar(key: str) -> str | None:
        value = env.get(key, "").strip()
        if value and not value.startswith("https://"):
            errors.append(f"{key} must be an https:// URL")
        return value or None

    token = required("DISCORD_TOKEN")
    owner = snowflake("OWNER_USER_ID", required("OWNER_USER_ID"))
    channels = frozenset(
        snowflake("ALLOWED_CHANNEL_IDS", part.strip())
        for part in required("ALLOWED_CHANNEL_IDS").split(",")
        if part.strip()
    )
    config = Config(
        discord_token=token,
        owner_user_id=owner,
        allowed_channel_ids=channels,
        claude_avatar_url=avatar("CLAUDE_AVATAR_URL"),
        gpt_avatar_url=avatar("GPT_AVATAR_URL"),
        debug=env.get("DEBUG", "false").strip().lower() in ("1", "true", "yes"),
    )

    if errors:
        raise ConfigError("; ".join(errors))
    return config
