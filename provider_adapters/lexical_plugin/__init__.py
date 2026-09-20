"""Lexical archive-search baseline as a Hermes memory provider.

Deliberately dumb: stored public observations plus Hermes-turn transcripts,
ranked by raw term overlap. No embeddings, no rewriting, no scenario logic —
storage + lexical match only. Installed into the isolated Hermes home by
PACBench's harness; stdlib-only (plus the in-process ``agent`` import every
provider plugin uses).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

DEFAULT_LEXICAL_HOME = Path.home() / ".lexical"

#: Per-text cap on tool results (corpus messages are far shorter; the flag
#: makes any truncation explicit so reviewers know the cap).
TEXT_CAP = 2000

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _WORD.findall((text or "").lower())


def _overlap(query_toks: List[str], doc_toks: List[str]) -> int:
    if not query_toks or not doc_toks:
        return 0
    doc_set = set(doc_toks)
    return sum(1 for tok in set(query_toks) if tok in doc_set)


SEARCH = {
    "name": "lexical_search",
    "description": (
        "Keyword search over stored messages. Use distinctive words from the "
        "question (names, places, times); returns the best matching messages "
        "with sender, channel, date, and source id. Ranking is plain term "
        "overlap — it finds messages that share words, nothing smarter. "
        "When the top hits share no words with the question, the store has "
        "nothing relevant: say so instead of guessing."),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "words to find in stored messages"},
            "limit": {"type": "integer",
                      "description": "max messages to return; default 5, max 20"},
        },
        "required": ["query"],
    },
}

OPEN = {
    "name": "lexical_open",
    "description": (
        "Read one stored message in full by its source id (from "
        "lexical_search). Returns the complete text with sender, channel, "
        "and timestamps."),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {"type": "string",
                    "description": "source id of the message to open"},
        },
        "required": ["ref"],
    },
}


def _db_path(home: Path) -> Path:
    return home / "lexical.db"


class LexicalMemoryProvider(MemoryProvider):
    """Term-overlap search over a scratch sqlite archive."""

    def __init__(self) -> None:
        self._home = Path(os.environ.get("LEXICAL_HOME") or DEFAULT_LEXICAL_HOME)
        self._session_id = ""
        self._agent_context = "primary"
        self._lock = threading.Lock()
        self._turn_seen = 0

    @property
    def name(self) -> str:
        return "lexical"

    # ------------------------------------------------------------- lifecycle --
    def is_available(self) -> bool:
        """Ready when the adapter has created the scratch store. No network."""
        return _db_path(self._home).exists()

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._agent_context = kwargs.get("agent_context", "primary")
        with self._lock:
            self._turn_seen = 0

    def shutdown(self) -> None:
        return

    # ----------------------------------------------------------------- inject --
    def system_prompt_block(self) -> str:
        """Static tool-use instructions (no changing content: prompt-cache safe)."""
        return (
            "# Lexical archive\n\n"
            "A keyword archive of past messages is available. Every turn also "
            "carries a LEXICAL ARCHIVE count line. To answer questions about "
            "past messages, search it with lexical_search using distinctive "
            "words, then read promising hits in full with lexical_open. Quote "
            "or paraphrase what the messages actually say; never invent "
            "details the retrieved messages do not contain."
        )

    def _count(self) -> Optional[int]:
        try:
            conn = sqlite3.connect(_db_path(self._home))
            try:
                return int(conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("lexical count failed: %s", exc)
            return None

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Stored-message count line (generic metadata, no scenario content)."""
        if self._agent_context != "primary":
            return ""
        count = self._count()
        if count is None:
            return ""
        return (f"LEXICAL ARCHIVE: {count} stored message(s). Use lexical_search "
                f"with distinctive words to find relevant ones.")

    # -------------------------------------------------------------- sync_all --
    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: Optional[List[Dict[str, Any]]] = None) -> None:
        """Persist the completed turn so later questions can retrieve it.

        The §31 path for user turns: they run through real Hermes and reach
        the store only here, never via adapter writes. One row per turn.
        """
        if self._agent_context != "primary":
            return
        user_text = (user_content or "").strip()
        assistant_text = (assistant_content or "").strip()
        if not user_text and not assistant_text:
            return
        sid = session_id or self._session_id
        with self._lock:
            self._turn_seen += 1
            n = self._turn_seen
        text = f"user: {user_text}\nassistant: {assistant_text}".strip()
        try:
            conn = sqlite3.connect(_db_path(self._home))
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO docs(source_id, channel, sender,"
                    " thread, written_at, collected_at, text)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (f"turn:{sid}:{n}", "hermes", "user", f"hermes:{sid}",
                     "", "", text))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("lexical sync_turn failed: %s", exc)

    # ------------------------------------------------------------------ tools --
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH, OPEN]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            conn = sqlite3.connect(_db_path(self._home))
            try:
                if tool_name == "lexical_search":
                    return self._search(conn, args)
                if tool_name == "lexical_open":
                    return self._open(conn, args)
                return json.dumps({"error": f"unknown tool {tool_name!r}"})
            finally:
                conn.close()
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    def _search(self, conn: sqlite3.Connection, args: Dict[str, Any]) -> str:
        query = str(args.get("query", "") or "")
        try:
            limit = max(1, min(20, int(args.get("limit", 5))))
        except (TypeError, ValueError):
            limit = 5
        qtoks = _tokens(query)
        rows = conn.execute(
            "SELECT source_id, channel, sender, thread, written_at,"
            " collected_at, text FROM docs").fetchall()
        scored = []
        for source_id, channel, sender, thread, written_at, collected_at, text in rows:
            score = _overlap(qtoks, _tokens(text))
            scored.append((score, collected_at or "", source_id, channel,
                           sender, thread, written_at, text))
        # Raw overlap first; recency breaks ties, then source id (neutral
        # rules, no tuning). Stable sorts compose: least significant first.
        scored.sort(key=lambda r: r[2])
        scored.sort(key=lambda r: (r[1] or ""), reverse=True)
        scored.sort(key=lambda r: -r[0])
        # Zero-overlap rows are not results: the store has nothing relevant,
        # and the driver is instructed to say so instead of guessing.
        scored = [r for r in scored if r[0] > 0]
        hits = []
        for score, collected_at, source_id, channel, sender, thread, written_at, text in scored[:limit]:
            snippet = text if len(text) <= TEXT_CAP else text[:TEXT_CAP] + "[truncated]"
            hits.append({
                "source_id": source_id,
                "overlap": score,
                "channel": channel,
                "sender": sender,
                "thread": thread,
                "written_at": written_at,
                "collected_at": collected_at,
                "text": snippet,
                "truncated": len(text) > TEXT_CAP,
            })
        return json.dumps({"query": query, "hits": hits})

    def _open(self, conn: sqlite3.Connection, args: Dict[str, Any]) -> str:
        ref = str(args.get("ref", "") or "")
        row = conn.execute(
            "SELECT source_id, channel, sender, thread, written_at,"
            " collected_at, text FROM docs WHERE source_id = ?",
            (ref,)).fetchone()
        if row is None:
            return json.dumps({"error": f"no stored message {ref!r}"})
        source_id, channel, sender, thread, written_at, collected_at, text = row
        return json.dumps({
            "source_id": source_id,
            "channel": channel,
            "sender": sender,
            "thread": thread,
            "written_at": written_at,
            "collected_at": collected_at,
            "text": text,
        })


def register(ctx) -> None:
    ctx.register_memory_provider(LexicalMemoryProvider())
