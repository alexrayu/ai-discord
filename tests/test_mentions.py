import pytest

from mentions import mention_names, mentions
from transcript import TranscriptMessage


def msg(text, speaker="human", name="Walrus"):
    return TranscriptMessage(channel_id=1, speaker=speaker, text=text, display_name=name)


TRANSCRIPT = [msg("hi"), msg("yo", name="Anna"), msg("x", name="Claude")]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("@claude hi", {"claude"}),
        ("hey @Quill.", {"gpt"}),
        ("@GPT?", {"gpt"}),
        ("@anna look", {"Anna"}),
        ("@Claude and @quill", {"claude", "gpt"}),
        ("mail user@gpt.com", set()),
        ("@gptx", set()),
        ("@Annabel", set()),
        ("`@Claude` inline", set()),
        ("```py\n@Quill\ndef f(): pass\n```", set()),
        ("```\n@Quill unclosed", set()),
        ("@Walrus talking to myself", set()),
    ],
)
def test_mentions_from_human(text, expected):
    assert mentions(msg(text), TRANSCRIPT) == expected


def test_self_mention_ignored_for_ai():
    assert mentions(msg("@Claude, @Quill?", speaker="claude", name="Claude"), TRANSCRIPT) == {"gpt"}


def test_human_named_like_ai_is_not_addressable():
    assert mentions(msg("@claude"), TRANSCRIPT) == {"claude"}


def test_mention_names():
    assert mention_names({"gpt", "Anna"}) == "@Anna, @Quill"
