"""OpenAI adapter (Quill)."""

import logging
from collections.abc import Mapping

from openai import APIError, AsyncOpenAI

from config import Config
from providers.base import ProviderError, Reply, add_hint, cache_breakpoint, to_messages
from transcript import ImageData, TranscriptMessage

log = logging.getLogger(__name__)

# Pinned so an inherited OPENAI_BASE_URL can never route chats through another endpoint.
BASE_URL = "https://api.openai.com/v1"
STOP = {"length": "truncated", "content_filter": "refusal"}
CACHE = {"prompt_cache_breakpoint": {"mode": "explicit"}}  # gpt-5.6 and later


class GPTProvider:
    name = "gpt"

    def __init__(self, config: Config, client: AsyncOpenAI | None = None):
        self._config = config
        # SDK retries 408/409/429/5xx and connection errors with backoff.
        self._client = client or AsyncOpenAI(
            api_key=config.openai_api_key, base_url=BASE_URL, max_retries=2, timeout=120
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
        extra = {"reasoning_effort": self._config.gpt_reasoning_effort} if self._config.gpt_reasoning_effort else {}
        try:
            response = await self._client.chat.completions.create(
                model=self._config.gpt_model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=self._config.max_reply_tokens,
                # Routes requests of one channel to the same cache.
                prompt_cache_key=f"{self.name}-{transcript[0].channel_id}",
                **extra,
            )
        except APIError as e:
            out_of_credits = getattr(e, "code", None) == "insufficient_quota"
            raise ProviderError(f"{type(e).__name__} {getattr(e, 'status_code', '')}".strip(), out_of_credits) from e
        usage = response.usage
        details = usage.prompt_tokens_details
        log.debug(
            "Quill tokens: input %s, cache read %s, cache write %s, output %s",
            usage.prompt_tokens, details and details.cached_tokens, details and details.cache_write_tokens,
            usage.completion_tokens,
        )
        choice = response.choices[0]
        if choice.message.refusal:
            return Reply(text="", stop="refusal")
        return Reply(text=choice.message.content or "", stop=STOP.get(choice.finish_reason, "end"))


def _image_block(image: ImageData) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{image.data}"}}
