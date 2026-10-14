"""Anthropic adapter."""

import logging

from anthropic import APIError, AsyncAnthropic

from config import Config
from models import ImageData, Part, PdfData
from providers.base import ProviderError, Reply, to_messages

log = logging.getLogger(__name__)

# Pinned so an inherited ANTHROPIC_BASE_URL can never route reviews through another endpoint.
BASE_URL = "https://api.anthropic.com"
STOP = {"max_tokens": "truncated", "refusal": "refusal"}
CACHE = {"cache_control": {"type": "ephemeral"}}


class ClaudeProvider:
    name = "claude"

    def __init__(self, config: Config, client: AsyncAnthropic | None = None):
        self._config = config
        # SDK retries 408/409/429/5xx and connection errors with backoff. Long reviews over many pages need the timeout.
        self._client = client or AsyncAnthropic(
            api_key=config.anthropic_api_key, base_url=BASE_URL, max_retries=2, timeout=600
        )

    async def respond(self, system: str, material: list[Part], turns: list[tuple[str, str]]) -> Reply:
        # Thinking is left at the model default (it can't be disabled on current models); effort bounds it.
        try:
            response = await self._client.messages.create(
                model=self._config.claude_model,
                max_tokens=self._config.max_reply_tokens,
                system=[{"type": "text", "text": system, **CACHE}],
                messages=to_messages(material, turns, _block, CACHE),
                output_config={"effort": self._config.claude_effort},
            )
        except APIError as e:
            raise ProviderError(f"{type(e).__name__} {getattr(e, 'status_code', '')}".strip(), _out_of_credits(e)) from e
        usage = response.usage
        log.debug(
            "Claude tokens: input %s, cache read %s, cache write %s, output %s",
            usage.input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens, usage.output_tokens,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return Reply(text=text, stop=STOP.get(response.stop_reason, "end"))


def _block(item: ImageData | PdfData) -> dict:
    if isinstance(item, PdfData):
        return {"type": "document", "title": item.filename,
                "source": {"type": "base64", "media_type": "application/pdf", "data": item.data}}
    return {"type": "image", "source": {"type": "base64", "media_type": item.media_type, "data": item.data}}


def _out_of_credits(e: APIError) -> bool:
    error = e.body.get("error") if isinstance(e.body, dict) else None
    if not isinstance(error, dict):
        return False
    return error.get("type") == "billing_error" or "credit balance" in str(error.get("message", "")).lower()
