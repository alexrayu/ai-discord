from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import openai
import pytest

from config import load_config
from providers.base import ProviderError
from providers.claude import ClaudeProvider
from providers.gpt import GPTProvider
from transcript import Attachment, ImageData, TranscriptMessage

ENV = {
    "DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": "1", "ANTHROPIC_API_KEY": "a", "CLAUDE_MODEL": "claude-m",
    "OPENAI_API_KEY": "o", "GPT_MODEL": "gpt-m",
}
CONFIG = load_config(ENV)
TRANSCRIPT = [TranscriptMessage(channel_id=1, speaker="human", text="hi", display_name="Walrus")]


# Endpoints stay pinned even when the shell exports overrides.

def test_base_urls_ignore_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    assert str(GPTProvider(CONFIG)._client.base_url).startswith("https://api.openai.com/v1")
    assert str(ClaudeProvider(CONFIG)._client.base_url).startswith("https://api.anthropic.com")


# Claude

def claude_with(response=None, error=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response, side_effect=error)
    return ClaudeProvider(CONFIG, client), client


@pytest.mark.parametrize("stop_reason, stop", [("end_turn", "end"), ("max_tokens", "truncated"), ("refusal", "refusal")])
async def test_claude_stop_mapping_and_text(stop_reason, stop):
    blocks = [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="Hi "), SimpleNamespace(type="text", text="there")]
    provider, client = claude_with(SimpleNamespace(content=blocks, stop_reason=stop_reason))
    reply = await provider.respond("SYS", TRANSCRIPT, {})
    assert (reply.text, reply.stop) == ("Hi there", stop)
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == "SYS" and kwargs["model"] == "claude-m"
    assert kwargs["output_config"] == {"effort": "low"}
    assert "thinking" not in kwargs


async def test_claude_error_wrapped():
    error = anthropic.APIConnectionError(request=MagicMock())
    provider, _ = claude_with(error=error)
    with pytest.raises(ProviderError):
        await provider.respond("SYS", TRANSCRIPT, {})


# Quill

def gpt_with(config=CONFIG, content="Yo", finish_reason="stop", refusal=None, error=None):
    message = SimpleNamespace(content=content, refusal=refusal)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response, side_effect=error)
    return GPTProvider(config, client), client


@pytest.mark.parametrize("finish_reason, stop", [("stop", "end"), ("length", "truncated"), ("content_filter", "refusal")])
async def test_gpt_stop_mapping(finish_reason, stop):
    provider, client = gpt_with(finish_reason=finish_reason)
    reply = await provider.respond("SYS", TRANSCRIPT, {})
    assert reply.stop == stop
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "SYS"}
    assert kwargs["messages"][1] == {"role": "user", "content": "[Walrus]: hi"}
    assert kwargs["max_completion_tokens"] == 4000
    assert "reasoning_effort" not in kwargs


async def test_gpt_refusal_and_empty_content():
    provider, _ = gpt_with(content=None, refusal="I can't help with that.")
    assert (await provider.respond("SYS", TRANSCRIPT, {})).stop == "refusal"
    provider, _ = gpt_with(content=None)
    assert (await provider.respond("SYS", TRANSCRIPT, {})).text == ""


async def test_gpt_reasoning_effort_passed_when_set():
    provider, client = gpt_with(config=load_config({**ENV, "GPT_REASONING_EFFORT": "low"}))
    await provider.respond("SYS", TRANSCRIPT, {})
    assert client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"


async def test_gpt_error_wrapped():
    provider, _ = gpt_with(error=openai.APIConnectionError(request=MagicMock()))
    with pytest.raises(ProviderError):
        await provider.respond("SYS", TRANSCRIPT, {})


# Image blocks in each provider's native format

IMAGE_TRANSCRIPT = [TranscriptMessage(channel_id=1, speaker="human", text="see", display_name="Walrus", id=3,
                                      attachments=(Attachment("p.png", "image/png", "image", "u"),))]
IMAGES = {(3, 0): ImageData("image/png", "B64")}


async def test_claude_image_block():
    provider, client = claude_with(SimpleNamespace(content=[], stop_reason="end_turn"))
    await provider.respond("SYS", IMAGE_TRANSCRIPT, IMAGES)
    content = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "B64"}}


async def test_gpt_image_block():
    provider, client = gpt_with()
    await provider.respond("SYS", IMAGE_TRANSCRIPT, IMAGES)
    content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,B64"}}
