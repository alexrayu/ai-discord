"""SQLite persistence, one transcript per Discord channel or thread."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import aiosqlite

from transcript import Attachment, TranscriptMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    author_id INTEGER,
    display_name TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp REAL NOT NULL,
    discord_message_id INTEGER,
    attachments TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS messages_channel ON messages (channel_id, id);
CREATE TABLE IF NOT EXISTS settings (
    channel_id INTEGER PRIMARY KEY,
    budget INTEGER
);
"""
COLUMNS = "id, channel_id, speaker, author_id, display_name, text, timestamp, discord_message_id, attachments"


class Store:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db
        # First message id of each channel's transcript window; lost on restart, which costs one cache miss.
        self._window_starts: dict[int, int] = {}

    @classmethod
    async def open(cls, path: Path | str) -> "Store":
        db = await aiosqlite.connect(path)
        await db.executescript(SCHEMA)
        # Databases created before attachment support.
        async with db.execute("SELECT name FROM pragma_table_info('messages')") as cursor:
            if ("attachments",) not in await cursor.fetchall():
                await db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
        await db.commit()
        return cls(db)

    async def close(self) -> None:
        await self._db.close()

    async def append(self, msg: TranscriptMessage) -> TranscriptMessage:
        cursor = await self._db.execute(
            "INSERT INTO messages (channel_id, speaker, author_id, display_name, text, timestamp, discord_message_id, attachments)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg.channel_id, msg.speaker, msg.author_id, msg.display_name, msg.text, msg.timestamp,
             msg.discord_message_id, _dump(msg.attachments)),
        )
        await self._db.commit()
        return replace(msg, id=cursor.lastrowid)

    async def clear(self, channel_id: int) -> None:
        """Delete the transcript; settings stay."""
        await self._db.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        await self._db.commit()

    async def size(self, channel_id: int) -> tuple[int, int]:
        """Stored messages and characters of message text."""
        async with self._db.execute(
            "SELECT count(*), coalesce(sum(length(text)), 0) FROM messages WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def budget(self, channel_id: int) -> int | None:
        async with self._db.execute("SELECT budget FROM settings WHERE channel_id = ?", (channel_id,)) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_budget(self, channel_id: int, budget: int) -> None:
        await self._db.execute(
            "INSERT INTO settings (channel_id, budget) VALUES (?, ?) ON CONFLICT (channel_id) DO UPDATE SET budget = excluded.budget",
            (channel_id, budget),
        )
        await self._db.commit()

    async def update_attachments(self, message_id: int, attachments: tuple[Attachment, ...]) -> None:
        await self._db.execute("UPDATE messages SET attachments = ? WHERE id = ?", (_dump(attachments), message_id))
        await self._db.commit()

    async def recent(self, channel_id: int, char_limit: int) -> list[TranscriptMessage]:
        """Messages from the window start on, plus the first human message as topic anchor.

        The window start only moves when the window outgrows char_limit, and then cuts back to
        char_limit / 2 (at least one message): the transcript prefix stays byte-stable between
        cuts, so provider prompt caches keep hitting.
        """
        start = self._window_starts.get(channel_id, 0)
        messages: list[TranscriptMessage] = []
        sizes: list[int] = []
        overflow = False
        # Streams newest-first and stops early, so long transcripts are never fully loaded.
        async with self._db.execute(
            f"SELECT {COLUMNS} FROM messages WHERE channel_id = ? AND id >= ? ORDER BY id DESC", (channel_id, start)
        ) as cursor:
            async for row in cursor:
                msg = _row(row)
                sizes.append((sizes[-1] if sizes else 0) + len(msg.text) + sum(len(a.text or "") for a in msg.attachments))
                if messages and sizes[-1] > char_limit:
                    overflow = True
                    break
                messages.append(msg)
        if overflow:
            keep = max(1, sum(size <= char_limit // 2 for size in sizes))
            messages = messages[:keep]
            self._window_starts[channel_id] = messages[-1].id
        messages.reverse()

        async with self._db.execute(
            f"SELECT {COLUMNS} FROM messages WHERE channel_id = ? AND speaker = 'human' ORDER BY id LIMIT 1",
            (channel_id,),
        ) as cursor:
            anchor = await cursor.fetchone()
        if anchor and (not messages or messages[0].id > anchor[0]):
            messages.insert(0, _row(anchor))
        return messages


def _row(row) -> TranscriptMessage:
    id_, channel_id, speaker, author_id, display_name, text, timestamp, discord_message_id, attachments = row
    return TranscriptMessage(
        channel_id=channel_id,
        speaker=speaker,
        text=text,
        display_name=display_name,
        author_id=author_id,
        discord_message_id=discord_message_id,
        attachments=tuple(Attachment(**a) for a in json.loads(attachments)),
        timestamp=timestamp,
        id=id_,
    )


def _dump(attachments: tuple[Attachment, ...]) -> str:
    return json.dumps([asdict(a) for a in attachments])
