"""A scratch chat.db shaped like the tables Memcal's iMessage reader queries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


SCHEMA = """
CREATE TABLE IF NOT EXISTS message (
    ROWID INTEGER PRIMARY KEY, guid TEXT UNIQUE, date INTEGER, text TEXT,
    attributedBody BLOB, is_from_me INTEGER, handle_id INTEGER);
CREATE TABLE IF NOT EXISTS handle (ROWID INTEGER PRIMARY KEY, id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS chat (
    ROWID INTEGER PRIMARY KEY, chat_identifier TEXT UNIQUE, display_name TEXT);
CREATE TABLE IF NOT EXISTS chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE IF NOT EXISTS chat_handle_join (
    chat_id INTEGER, handle_id INTEGER, UNIQUE(chat_id, handle_id));
"""


def append_messages(path: Path, messages: list[dict],
                    apple_ns: Callable[[str], int]) -> int:
    """Append a day's messages, retaining rowids for the next incremental read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        handles: dict[str, int] = {}
        chats: dict[str, int] = {}
        for message in messages:
            if str(message.get("channel")) != "imessage":
                raise ValueError("chat.db accepts only iMessage messages")
            thread = str(message["thread"] or "").lower()
            if thread not in chats:
                conn.execute("INSERT OR IGNORE INTO chat(chat_identifier) VALUES(?)",
                             (thread,))
                chats[thread] = conn.execute(
                    "SELECT ROWID FROM chat WHERE chat_identifier = ?", (thread,)
                ).fetchone()[0]
            sender = str(message["sender"])
            if message.get("from_owner") or sender.casefold() == "me":
                continue
            name = sender.casefold()
            if name not in handles:
                conn.execute("INSERT OR IGNORE INTO handle(id) VALUES(?)", (name,))
                handles[name] = conn.execute(
                    "SELECT ROWID FROM handle WHERE id = ?", (name,)
                ).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO chat_handle_join VALUES(?,?)",
                         (chats[thread], handles[name]))

        added = 0
        for message in sorted(messages, key=lambda m: (m["written_at"], m["source_id"])):
            thread = str(message["thread"] or "").lower()
            owner = bool(message.get("from_owner") or
                         str(message["sender"]).casefold() == "me")
            handle_id = None if owner else handles[str(message["sender"]).casefold()]
            cursor = conn.execute(
                "INSERT OR IGNORE INTO message(guid, date, text, attributedBody,"
                " is_from_me, handle_id) VALUES(?,?,?,NULL,?,?)",
                (str(message["source_id"]), apple_ns(str(message["written_at"])),
                 str(message["text"]), int(owner), handle_id))
            if cursor.rowcount:
                conn.execute("INSERT INTO chat_message_join VALUES(?,?)",
                             (chats[thread], cursor.lastrowid))
                added += 1
        conn.commit()
        return added
    finally:
        conn.close()
