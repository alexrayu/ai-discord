import pytest

from store import Store
from transcript import TranscriptMessage


@pytest.fixture
async def store(tmp_path):
    store = await Store.open(tmp_path / "t.sqlite")
    yield store
    await store.close()


def msg(text, speaker="human", channel=1):
    return TranscriptMessage(channel_id=channel, speaker=speaker, text=text, display_name="W", author_id=5)


async def test_round_trip_per_channel(store):
    await store.append(msg("a"))
    await store.append(msg("b", speaker="claude"))
    await store.append(msg("other", channel=2))
    got = await store.recent(1, 1000)
    assert [(m.speaker, m.text) for m in got] == [("human", "a"), ("claude", "b")]
    assert got[0].author_id == 5 and got[0].id is not None


async def test_trim_keeps_newest_and_first_human_anchor(store):
    await store.append(msg("topic"))
    for i in range(10):
        await store.append(msg(f"m{i}" + "x" * 8, speaker="claude"))
    got = await store.recent(1, 25)  # room for the two newest 10-char messages
    assert [m.text[:2] for m in got] == ["to", "m8", "m9"]


async def test_newest_always_included_even_if_over_limit(store):
    await store.append(msg("x" * 100))
    assert len(await store.recent(1, 10)) == 1


async def test_anchor_not_duplicated(store):
    await store.append(msg("topic"))
    await store.append(msg("next"))
    assert [m.text for m in await store.recent(1, 1000)] == ["topic", "next"]


async def test_empty_channel(store):
    assert await store.recent(1, 1000) == []


async def test_attachments_round_trip_and_update(store):
    from transcript import Attachment
    att = Attachment("a.png", "image/png", "image", "https://cdn/a")
    stored = await store.append(TranscriptMessage(channel_id=1, speaker="human", text="", attachments=(att,)))
    await store.update_attachments(stored.id, (Attachment("a.png", "image/png", "image", "https://cdn/b"),))
    assert (await store.recent(1, 1000))[0].attachments[0].url == "https://cdn/b"


async def test_migrates_pre_attachment_database(tmp_path):
    import aiosqlite
    path = tmp_path / "old.sqlite"
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL,"
                         " speaker TEXT NOT NULL, author_id INTEGER, display_name TEXT NOT NULL, text TEXT NOT NULL,"
                         " timestamp REAL NOT NULL, discord_message_id INTEGER)")
        await db.execute("INSERT INTO messages (channel_id, speaker, display_name, text, timestamp) VALUES (1, 'human', 'W', 'old', 0)")
        await db.commit()
    store = await Store.open(path)
    assert [(m.text, m.attachments) for m in await store.recent(1, 1000)] == [("old", ())]
    await store.close()


async def test_inlined_file_text_counts_toward_limit(store):
    from transcript import Attachment
    await store.append(msg("topic"))
    await store.append(msg("old"))
    file = Attachment("a.txt", "text/plain", "text", "u", "x" * 50)
    await store.append(TranscriptMessage(channel_id=1, speaker="human", text="f", display_name="W", attachments=(file,)))
    assert [m.text for m in await store.recent(1, 52)] == ["topic", "f"]
