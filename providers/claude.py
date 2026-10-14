"""Anthropic adapter."""

import logging
from collections.abc import Mapping

from anthropic import APIError, AsyncAnthropic

from config import Config
from providers.base import ProviderError, Reply, add_hint, cache_breakpoint, to_messages
from transcript import ImageData, TranscriptMessage

log = logging.getLogger(__name__)

# Pinned so an inherited ANTHROPIC_BASE_URL can never route chats through another endpoint.
BASE_URL = "https://api.anthropic.com"
STOP = {"max_tokens": "truncated", "refusal": "refusal"}
CACHE = {"cache_control": {"type": "ephemeral"}}


class ClaudeProvider:
    name = "claude"

    def __init__(self, config: Config, client: AsyncAnthropic | None = None):
        self._config = config
        # SDK retries 408/409/429/5xx and connection errors with backoff.
        self._client = client or AsyncAnthropic(
            api_key=config.anthropic_api_key, base_url=BASE_URL, max_retries=2, timeout=120
        )

    async def respond(
        self,
        system: str,
        transcript: list[TranscriptMessage],
        images: Mapping[tuple[int, int], ImageData | str],
        hint: str,
    ) -> Reply:
        messages = to_messages(transcript, self.name, images, _image_block)
        cache_breakpoint(messages, CACHE)
        add_hint(messages, hint)
        # Thinking is left at the model default (it can't be disabled on current models); effort bounds it.
        try:
            response = await self._client.messages.create(
                model=self._config.claude_model,
                max_tokens=self._config.max_reply_tokens,
                # Own breakpoint: the system prompt stays cached when the transcript window moves.
                system=[{"type": "text", "text": system, **CACHE}],
                messages=messages,
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


def _image_block(image: ImageData) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": image.media_type, "data": image.data}}


def _out_of_credits(e: APIError) -> bool:
    error = e.body.get("error") if isinstance(e.body, dict) else None
    if not isinstance(error, dict):
        return False
    return error.get("type") == "billing_error" or "credit balance" in str(error.get("message", "")).lower()
