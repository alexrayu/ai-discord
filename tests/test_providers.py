from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import openai
import pytest

from config import load_config
from providers.base import ProviderError
from providers.claude import ClaudeProvider
from providers.gpt import GPTProvider
from models import ImageData, PdfData

ENV = {
    "DISCORD_TOKEN": "t", "ALLOWED_CHANNEL_IDS": "1", "ANTHROPIC_API_KEY": "a", "CLAUDE_MODEL": "claude-m",
    "OPENAI_API_KEY": "o", "GPT_MODEL": "gpt-m",
}
CONFIG = load_config(ENV)
MATERIAL = ["Page 1:"]
TURNS = [("user", "TASK")]
CLAUDE_USAGE = SimpleNamespace(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1)
GPT_USAGE = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0))


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
    provider, client = claude_with(SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=CLAUDE_USAGE))
    reply = await provider.respond("SYS", MATERIAL, TURNS)
    assert (reply.text, reply.stop) == ("Hi there", stop)
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kwargs["model"] == "claude-m"
    assert kwargs["output_config"] == {"effort": "low"}
    assert "thinking" not in kwargs


async def test_claude_error_wrapped():
    error = anthropic.APIConnectionError(request=MagicMock())
    provider, _ = claude_with(error=error)
    with pytest.raises(ProviderError):
        await provider.respond("SYS", MATERIAL, TURNS)


# Quill

def gpt_with(config=CONFIG, content="Yo", finish_reason="stop", refusal=None, error=None):
    message = SimpleNamespace(content=content, refusal=refusal)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], usage=GPT_USAGE)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response, side_effect=error)
    return GPTProvider(config, client), client


@pytest.mark.parametrize("finish_reason, stop", [("stop", "end"), ("length", "truncated"), ("content_filter", "refusal")])
async def test_gpt_stop_mapping(finish_reason, stop):
    provider, client = gpt_with(finish_reason=finish_reason)
    reply = await provider.respond("SYS", MATERIAL, TURNS)
    assert reply.stop == stop
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": "SYS"}
    assert kwargs["messages"][1] == {"role": "user", "content": [
        {"type": "text", "text": "Page 1:"}, {"type": "text", "text": "TASK"},
    ]}
    assert kwargs["max_completion_tokens"] == 16000
    assert "reasoning_effort" not in kwargs


async def test_gpt_refusal_and_empty_content():
    provider, _ = gpt_with(content=None, refusal="I can't help with that.")
    assert (await provider.respond("SYS", MATERIAL, TURNS)).stop == "refusal"
    provider, _ = gpt_with(content=None)
    assert (await provider.respond("SYS", MATERIAL, TURNS)).text == ""


async def test_gpt_reasoning_effort_passed_when_set():
    provider, client = gpt_with(config=load_config({**ENV, "GPT_REASONING_EFFORT": "low"}))
    await provider.respond("SYS", MATERIAL, TURNS)
    assert client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"


async def test_gpt_error_wrapped():
    provider, _ = gpt_with(error=openai.APIConnectionError(request=MagicMock()))
    with pytest.raises(ProviderError):
        await provider.respond("SYS", MATERIAL, TURNS)


# Image and PDF blocks in each provider's native format

BLOBS = [ImageData("image/png", "B64"), PdfData("ch.pdf", "PDF64")]


async def test_claude_blocks_and_cache_breakpoint():
    provider, client = claude_with(SimpleNamespace(content=[], stop_reason="end_turn", usage=CLAUDE_USAGE))
    await provider.respond("SYS", BLOBS, TURNS)
    assert client.messages.create.call_args.kwargs["messages"] == [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "B64"}},
        {"type": "document", "title": "ch.pdf", "source": {"type": "base64", "media_type": "application/pdf", "data": "PDF64"},
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "TASK"},
    ]}]


async def test_gpt_blocks():
    provider, client = gpt_with()
    await provider.respond("SYS", BLOBS, TURNS)
    assert client.chat.completions.create.call_args.kwargs["messages"][1]["content"][:2] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,B64"}},
        {"type": "file", "file": {"filename": "ch.pdf", "file_data": "data:application/pdf;base64,PDF64"}},
    ]


# Out of credits

def _response(status):
    return MagicMock(status_code=status, headers={})


@pytest.mark.parametrize(
    "error, expected",
    [
        ({"type": "billing_error", "message": "x"}, True),
        ({"type": "invalid_request_error", "message": "Your credit balance is too low to access the API."}, True),
        ({"type": "invalid_request_error", "message": "max_tokens too large"}, False),
    ],
)
async def test_claude_out_of_credits(error, expected):
    provider, _ = claude_with(error=anthropic.BadRequestError("err", response=_response(400), body={"type": "error", "error": error}))
    with pytest.raises(ProviderError) as e:
        await provider.respond("SYS", MATERIAL, TURNS)
    assert e.value.out_of_credits is expected


@pytest.mark.parametrize("code, expected", [("insufficient_quota", True), ("rate_limit_exceeded", False)])
async def test_gpt_out_of_credits(code, expected):
    provider, _ = gpt_with(error=openai.RateLimitError("err", response=_response(429), body={"code": code}))
    with pytest.raises(ProviderError) as e:
        await provider.respond("SYS", MATERIAL, TURNS)
    assert e.value.out_of_credits is expected
