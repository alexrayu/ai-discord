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
    [("Walrus", "Walrus"), ("claude", "claude (human)"), ("GPT", "GPT (human)"), ("quill", "quill (human)"), ("System", "System (human)"),
     ("[Claude]", "Claude (human)"), ("a]b\n[c", "abc"), ("", "Someone")],
)
def test_human_label(name, expected):
    assert label(human("x", name)) == expected


# Conversion

def test_roles_labels_and_merging_from_claude_view():
    transcript = [human("hi"), ai("gpt", "yo"), ai("claude", "hey"), human("q", "Anna")]
    assert to_messages(transcript, "claude") == [
        {"role": "user", "content": "[Walrus]: hi\n\n[Quill]: yo"},
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
        ("Quill: you're wrong", "Quill: you're wrong"),  # only the own name is stripped
        ("Fine.\n[Quill]: I agree\n[Walrus]: ok", "Fine."),
        ("Fine.\n[walrus]: ok", "Fine."),
        ("See [docs]: link", "See [docs]: link"),
        ("Fine—build it. Or — not.", "Fine - build it. Or - not."),
    ],
)
def test_reply_text(text, expected):
    assert finalize(Reply(text, "end"), "claude", TRANSCRIPT) == expected


def test_impersonation_only_reply_is_pass():
    assert finalize(Reply("[Quill]: PASS too", "end"), "claude", TRANSCRIPT) is None


def test_truncated_reply_marked_not_pass():
    assert finalize(Reply("Long answer", "truncated"), "claude", TRANSCRIPT) == "Long answer […]"
    assert finalize(Reply("", "truncated"), "claude", TRANSCRIPT) is None


def test_refusal_is_pass():
    assert finalize(Reply("Sorry", "refusal"), "claude", TRANSCRIPT) is None


def test_prompts_use_ai_names():
    from prompts import load_prompts
    prompts = load_prompts()
    assert "{claude}" not in prompts["claude"] and "{gpt}" not in prompts["gpt"]
    assert "@Quill" in prompts["claude"] and "your name is Quill" in prompts["gpt"]


# Attachments

def test_images_placeholders_and_merging():
    from transcript import Attachment, ImageData
    atts = (Attachment("p1.png", "image/png", "image", "u"), Attachment("old.png", "image/png", "image", "u"),
            Attachment("x.heic", "image/heic", "image", "u"), Attachment("n[o]te.txt", "text/plain", "other", "u"))
    msg = TranscriptMessage(channel_id=1, speaker="human", text="look", display_name="Walrus", attachments=atts, id=7)
    images = {(7, 0): ImageData("image/png", "B64"), (7, 2): "unsupported format"}
    content = to_messages([human("hi"), msg], "claude", images, lambda i: {"img": i.data})[0]["content"]
    assert content == [
        {"type": "text", "text": "[Walrus]: hi\n\n[Walrus]: look"},
        {"img": "B64"},
        {"type": "text", "text": "[image: old.png]\n[image: x.heic - unsupported format]\n[file: note.txt - not supported yet]"},
    ]


def test_hint_after_own_message_is_new_user_turn():
    from providers.base import add_hint
    messages = to_messages([human("hi"), ai("claude", "yo")], "claude")
    add_hint(messages, "Your turn.")
    assert messages[-1] == {"role": "user", "content": "[System]: Your turn."}
