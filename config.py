"""Loads and validates settings from .env / the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv


EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    owner_user_id: int | None  # None: the owner of the server the message comes from
    allowed_channel_ids: frozenset[int]
    anthropic_api_key: str
    claude_model: str
    claude_effort: str
    openai_api_key: str
    gpt_model: str
    gpt_reasoning_effort: str | None
    max_reply_tokens: int
    transcript_char_limit: int
    image_max_edge: int
    user_rounds_per_hour: int
    debug: bool


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        # .env wins over the shell, which may export unrelated keys for the same SDKs.
        load_dotenv(override=True)
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

    def positive_int(key: str, default: int) -> int:
        raw = env.get(key, "").strip()
        if not raw:
            return default
        if not raw.isdigit() or int(raw) < 1:
            errors.append(f"{key} must be a positive integer, got {raw!r}")
            return default
        return int(raw)

    effort = env.get("CLAUDE_EFFORT", "").strip().lower() or "low"
    if effort not in EFFORTS:
        errors.append(f"CLAUDE_EFFORT must be one of {', '.join(EFFORTS)}, got {effort!r}")

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
        anthropic_api_key=required("ANTHROPIC_API_KEY"),
        claude_model=required("CLAUDE_MODEL"),
        claude_effort=effort,
        openai_api_key=required("OPENAI_API_KEY"),
        gpt_model=required("GPT_MODEL"),
        gpt_reasoning_effort=env.get("GPT_REASONING_EFFORT", "").strip() or None,
        max_reply_tokens=positive_int("MAX_REPLY_TOKENS", 4000),
        transcript_char_limit=positive_int("TRANSCRIPT_CHAR_LIMIT", 60000),
        image_max_edge=positive_int("IMAGE_MAX_EDGE", 1568),
        user_rounds_per_hour=positive_int("USER_ROUNDS_PER_HOUR", 20),
        debug=env.get("DEBUG", "false").strip().lower() in ("1", "true", "yes"),
    )

    if errors:
        raise ConfigError("; ".join(errors))
    return config
