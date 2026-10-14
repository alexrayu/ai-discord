"""OpenAI adapter (Quill)."""

import logging

from openai import APIError, AsyncOpenAI

from config import Config
from models import ImageData, Part, PdfData
from providers.base import ProviderError, Reply, to_messages

log = logging.getLogger(__name__)

# Pinned so an inherited OPENAI_BASE_URL can never route reviews through another endpoint.
BASE_URL = "https://api.openai.com/v1"
STOP = {"length": "truncated", "content_filter": "refusal"}


class GPTProvider:
    name = "gpt"

    def __init__(self, config: Config, client: AsyncOpenAI | None = None):
        self._config = config
        # SDK retries 408/409/429/5xx and connection errors with backoff. Long reviews over many pages need the timeout.
        self._client = client or AsyncOpenAI(
            api_key=config.openai_api_key, base_url=BASE_URL, max_retries=2, timeout=600
        )

    async def respond(self, system: str, material: list[Part], turns: list[tuple[str, str]]) -> Reply:
        # Quill runs once per review, so no explicit cache breakpoint; the static system prompt is cached automatically.
        extra = {"reasoning_effort": self._config.gpt_reasoning_effort} if self._config.gpt_reasoning_effort else {}
        try:
            response = await self._client.chat.completions.create(
                model=self._config.gpt_model,
                messages=[{"role": "system", "content": system}, *to_messages(material, turns, _block)],
                max_completion_tokens=self._config.max_reply_tokens,
                **extra,
            )
        except APIError as e:
            out_of_credits = getattr(e, "code", None) == "insufficient_quota"
            raise ProviderError(f"{type(e).__name__} {getattr(e, 'status_code', '')}".strip(), out_of_credits) from e
        usage = response.usage
        details = usage.prompt_tokens_details
        log.debug(
            "Quill tokens: input %s, cache read %s, output %s",
            usage.prompt_tokens, details and details.cached_tokens, usage.completion_tokens,
        )
        choice = response.choices[0]
        if choice.message.refusal:
            return Reply(text="", stop="refusal")
        return Reply(text=choice.message.content or "", stop=STOP.get(choice.finish_reason, "end"))


def _block(item: ImageData | PdfData) -> dict:
    if isinstance(item, PdfData):
        return {"type": "file", "file": {"filename": item.filename, "file_data": f"data:application/pdf;base64,{item.data}"}}
    return {"type": "image_url", "image_url": {"url": f"data:{item.media_type};base64,{item.data}"}}
