import pytest

from providers.base import Reply, finalize, label, to_messages
from transcript import TranscriptMessage


def human(text, name="Walrus"):
    return TranscriptMessage(channel_id=1, speaker="human", text=text, display_name=name, author_id=5)


def ai(persona, text):
    return TranscriptMessage(channel_id=1, speaker=persona, text=text)


# Labels

@pytest.mark.parametrize(
    "name, expected",
    [("Walrus", "Walrus"), ("claude", "claude (human)"), ("GPT", "GPT (human)"), ("System", "System (human)"),
     ("[Claude]", "Claude (human)"), ("a]b\n[c", "abc"), ("", "Someone")],
)
def test_human_label(name, expected):
    assert label(human("x", name)) == expected


# Conversion

def test_roles_labels_and_merging_from_claude_view():
    transcript = [human("hi"), ai("gpt", "yo"), ai("claude", "hey"), human("q", "Anna")]
    assert to_messages(transcript, "claude") == [
        {"role": "user", "content": "[Walrus]: hi\n\n[GPT]: yo"},
        {"role": "assistant", "content": "hey"},
        {"role": "user", "content": "[Anna]: q"},
    ]


def test_same_transcript_from_gpt_view():
    transcript = [human("hi"), ai("gpt", "yo"), ai("claude", "hey")]
    assert to_messages(transcript, "gpt") == [
        {"role": "user", "content": "[Walrus]: hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "[Claude]: hey"},
    ]


def test_leading_assistant_dropped():
    assert to_messages([ai("claude", "a"), human("b")], "claude") == [{"role": "user", "content": "[Walrus]: b"}]


# Reply handling

TRANSCRIPT = [human("hi"), ai("gpt", "yo")]


@pytest.mark.parametrize("text", ["PASS", "pass", ' "PASS". ', "(PASS)", "[Claude]: PASS", "Claude: pass", "", "  \n"])
def test_pass(text):
    assert finalize(Reply(text, "end"), "claude", TRANSCRIPT) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("PASS on that, but no.", "PASS on that, but no."),
        ("[Claude]: Nope.", "Nope."),
        ("GPT: you're wrong", "GPT: you're wrong"),  # only the own name is stripped
        ("Fine.\n[GPT]: I agree\n[Walrus]: ok", "Fine."),
        ("Fine.\n[walrus]: ok", "Fine."),
        ("See [docs]: link", "See [docs]: link"),
    ],
)
def test_reply_text(text, expected):
    assert finalize(Reply(text, "end"), "claude", TRANSCRIPT) == expected


def test_impersonation_only_reply_is_pass():
    assert finalize(Reply("[GPT]: PASS too", "end"), "claude", TRANSCRIPT) is None


def test_truncated_reply_marked_not_pass():
    assert finalize(Reply("Long answer", "truncated"), "claude", TRANSCRIPT) == "Long answer […]"
    assert finalize(Reply("", "truncated"), "claude", TRANSCRIPT) is None


def test_refusal_is_pass():
    assert finalize(Reply("Sorry", "refusal"), "claude", TRANSCRIPT) is None
