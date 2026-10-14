import pytest

from models import ImageData
from providers.base import Reply, finalize, to_messages

MARK = {"cache": 1}


def block(item):
    return {"type": "img", "data": item.data}


def test_material_first_cache_mark_on_last_block_turns_merged():
    material = ["Page 1:", ImageData("image/png", "B64")]
    turns = [("user", "analyze"), ("assistant", "notes"), ("user", "critique"), ("user", "final")]
    assert to_messages(material, turns, block, MARK) == [
        {"role": "user", "content": [
            {"type": "text", "text": "Page 1:"}, {"type": "img", "data": "B64", "cache": 1}, {"type": "text", "text": "analyze"},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "notes"}]},
        {"role": "user", "content": [{"type": "text", "text": "critique"}, {"type": "text", "text": "final"}]},
    ]


def test_no_cache_mark():
    assert to_messages(["x"], [], block) == [{"role": "user", "content": [{"type": "text", "text": "x"}]}]


@pytest.mark.parametrize(
    "reply, expected",
    [
        (Reply("A — B", "end"), "A - B"),
        (Reply("  text \n", "truncated"), "text […]"),
        (Reply("text", "refusal"), None),
        (Reply(" \n", "end"), None),
    ],
)
def test_finalize(reply, expected):
    assert finalize(reply) == expected
