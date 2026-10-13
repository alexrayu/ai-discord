"""OpenAI adapter (Quill)."""

from collections.abc import Mapping

from openai import APIError, AsyncOpenAI

from config import Config
from providers.base import ProviderError, Reply, to_messages
from transcript import ImageData, TranscriptMessage

# Pinned so an inherited OPENAI_BASE_URL can never route chats through another endpoint.
BASE_URL = "https://api.openai.com/v1"
STOP = {"length": "truncated", "content_filter": "refusal"}


class GPTProvider:
    name = "gpt"

    def __init__(self, config: Config, client: AsyncOpenAI | None = None):
        self._config = config
        # SDK retries 408/409/429/5xx and connection errors with backoff.
        self._client = client or AsyncOpenAI(
            api_key=config.openai_api_key, base_url=BASE_URL, max_retries=2, timeout=120
        )

    async def respond(
        self, system: str, transcript: list[TranscriptMessage], images: Mapping[tuple[int, int], ImageData | str]
    ) -> Reply:
        extra = {"reasoning_effort": self._config.gpt_reasoning_effort} if self._config.gpt_reasoning_effort else {}
        try:
            response = await self._client.chat.completions.create(
                model=self._config.gpt_model,
                messages=[{"role": "system", "content": system}, *to_messages(transcript, self.name, images, _image_block)],
                max_completion_tokens=self._config.max_reply_tokens,
                **extra,
            )
        except APIError as e:
            raise ProviderError(f"{type(e).__name__} {getattr(e, 'status_code', '')}".strip()) from e
        choice = response.choices[0]
        if choice.message.refusal:
            return Reply(text="", stop="refusal")
        return Reply(text=choice.message.content or "", stop=STOP.get(choice.finish_reason, "end"))


def _image_block(image: ImageData) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{image.data}"}}
