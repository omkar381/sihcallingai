"""
Call transcripts and language preferences.

Every turn is stored: what the farmer said (in the language spoken, plus an
English gloss), what was understood, what the agent answered (in the call
language, plus English), the data quality behind the answer, and where the time
went. That serves three purposes: the live call view, an audit record of what
advice a farmer was given, and the evidence needed to tune recognition and
replies later.

A farmer's chosen language is remembered per phone number, so the language
menu is only heard on the first call.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from app.services.db import DB_LOCK, get_connection
from app.voice.language import normalise_language

logger = logging.getLogger(__name__)

_initialised_paths: set = set()
_init_lock = threading.Lock()

# In-memory feed for the live view. Sequence numbers let a client ask for
# "everything after N" without missing or repeating a turn.
_FEED: Deque[Dict[str, Any]] = deque(maxlen=300)
_feed_seq = 0
_feed_lock = threading.Lock()


def _columns(conn, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_call_tables() -> None:
    from app.services.db import DB_PATH

    with _init_lock:
        if DB_PATH in _initialised_paths:
            return
        with DB_LOCK:
            conn = get_connection()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS voice_calls (
                        call_sid     TEXT PRIMARY KEY,
                        phone        TEXT,
                        channel      TEXT NOT NULL DEFAULT 'phone',
                        language     TEXT,
                        status       TEXT NOT NULL DEFAULT 'in-progress',
                        turn_count   INTEGER NOT NULL DEFAULT 0,
                        started_at   INTEGER NOT NULL,
                        ended_at     INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS voice_turns (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        call_sid      TEXT NOT NULL,
                        turn_no       INTEGER NOT NULL,
                        language      TEXT NOT NULL DEFAULT 'kn',
                        farmer_text   TEXT,
                        farmer_en     TEXT,
                        intent        TEXT,
                        slots_json    TEXT,
                        reply_text    TEXT,
                        reply_en      TEXT,
                        data_quality  TEXT,
                        nlu_source    TEXT,
                        nlu_ms        INTEGER,
                        logic_ms      INTEGER,
                        tts_ms        INTEGER,
                        total_ms      INTEGER,
                        audio_file    TEXT,
                        created_at    INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS voice_language_preferences (
                        phone       TEXT PRIMARY KEY,
                        language    TEXT NOT NULL,
                        updated_at  INTEGER NOT NULL
                    );
                    """
                )
                # Tables created before Hindi and English were added stored the
                # Kannada text in *_kn columns and had no language column.
                turns = _columns(conn, "voice_turns")
                if "farmer_kn" in turns:
                    conn.execute("ALTER TABLE voice_turns RENAME COLUMN farmer_kn TO farmer_text")
                if "reply_kn" in turns:
                    conn.execute("ALTER TABLE voice_turns RENAME COLUMN reply_kn TO reply_text")
                if "language" not in turns:
                    conn.execute("ALTER TABLE voice_turns ADD COLUMN language TEXT NOT NULL DEFAULT 'kn'")
                if "language" not in _columns(conn, "voice_calls"):
                    conn.execute("ALTER TABLE voice_calls ADD COLUMN language TEXT")
                conn.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_voice_turns_call ON voice_turns(call_sid, turn_no);
                    CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()
        _initialised_paths.add(DB_PATH)


def _publish(event: Dict[str, Any]) -> None:
    global _feed_seq
    with _feed_lock:
        _feed_seq += 1
        event["seq"] = _feed_seq
        _FEED.append(event)


# ---------------------------------------------------------------------------
# Language preferences
# ---------------------------------------------------------------------------

def get_language_preference(phone: str) -> Optional[str]:
    if not phone:
        return None
    init_call_tables()
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT language FROM voice_language_preferences WHERE phone = ?", (phone,)
            ).fetchone()
            return normalise_language(row["language"], default=None) if row else None
        finally:
            conn.close()


def set_language_preference(phone: str, language: str) -> None:
    language = normalise_language(language, default=None)
    if not phone or not language:
        return
    init_call_tables()
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO voice_language_preferences(phone, language, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET language = excluded.language, updated_at = excluded.updated_at",
                (phone, language, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Calls and turns
# ---------------------------------------------------------------------------

def start_call(call_sid: str, phone: str = "", channel: str = "phone", language: Optional[str] = None) -> None:
    init_call_tables()
    ts = int(time.time())
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO voice_calls(call_sid, phone, channel, language, started_at) VALUES (?,?,?,?,?)",
                (call_sid, phone, channel, normalise_language(language, default=None), ts),
            )
            conn.commit()
        finally:
            conn.close()
    _publish({"type": "call_started", "call_sid": call_sid, "phone": phone,
              "channel": channel, "language": language, "at": ts})


def set_call_language(call_sid: str, language: str) -> None:
    init_call_tables()
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE voice_calls SET language = ? WHERE call_sid = ?",
                (normalise_language(language), call_sid),
            )
            conn.commit()
        finally:
            conn.close()


def end_call(call_sid: str, status: str = "completed") -> None:
    init_call_tables()
    ts = int(time.time())
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE voice_calls SET status = ?, ended_at = ? WHERE call_sid = ?",
                (status, ts, call_sid),
            )
            conn.commit()
        finally:
            conn.close()
    _publish({"type": "call_ended", "call_sid": call_sid, "status": status, "at": ts})


def record_turn(
    *,
    call_sid: str,
    phone: str,
    turn_no: int,
    language: str,
    farmer_text: str,
    farmer_en: str,
    intent: str,
    slots: Dict[str, Any],
    reply_text: str,
    reply_en: str,
    data_quality: Optional[str],
    nlu_source: str,
    timings: Dict[str, int],
    audio_file: Optional[str],
    channel: str = "phone",
) -> Dict[str, Any]:
    init_call_tables()
    ts = int(time.time())
    language = normalise_language(language)
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO voice_calls(call_sid, phone, channel, language, started_at) VALUES (?,?,?,?,?)",
                (call_sid, phone, channel, language, ts),
            )
            conn.execute(
                """
                INSERT INTO voice_turns(call_sid, turn_no, language, farmer_text, farmer_en, intent,
                    slots_json, reply_text, reply_en, data_quality, nlu_source, nlu_ms, logic_ms,
                    tts_ms, total_ms, audio_file, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    call_sid, turn_no, language, farmer_text, farmer_en, intent,
                    json.dumps(slots, ensure_ascii=False), reply_text, reply_en, data_quality,
                    nlu_source, timings.get("nlu_ms"), timings.get("logic_ms"),
                    timings.get("tts_ms"), timings.get("total_ms"), audio_file, ts,
                ),
            )
            conn.execute(
                "UPDATE voice_calls SET turn_count = turn_count + 1, language = ? WHERE call_sid = ?",
                (language, call_sid),
            )
            conn.commit()
        finally:
            conn.close()

    turn = {
        "call_sid": call_sid, "phone": phone, "turn_no": turn_no, "language": language,
        "farmer_text": farmer_text, "farmer_en": farmer_en, "intent": intent, "slots": slots,
        "reply_text": reply_text, "reply_en": reply_en, "data_quality": data_quality,
        "nlu_source": nlu_source, "timings": timings, "audio_file": audio_file,
        "channel": channel, "created_at": ts,
    }
    _publish({"type": "turn", **turn})
    return turn


def _turn_row(row) -> Dict[str, Any]:
    return {
        "turn_no": row["turn_no"],
        "language": row["language"],
        "farmer_text": row["farmer_text"],
        "farmer_en": row["farmer_en"],
        "intent": row["intent"],
        "slots": json.loads(row["slots_json"]) if row["slots_json"] else {},
        "reply_text": row["reply_text"],
        "reply_en": row["reply_en"],
        "data_quality": row["data_quality"],
        "nlu_source": row["nlu_source"],
        "timings": {
            "nlu_ms": row["nlu_ms"], "logic_ms": row["logic_ms"],
            "tts_ms": row["tts_ms"], "total_ms": row["total_ms"],
        },
        "audio_file": row["audio_file"],
        "created_at": row["created_at"],
    }


def recent_calls(limit: int = 30) -> List[Dict[str, Any]]:
    init_call_tables()
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM voice_calls ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for r in rows:
                last = conn.execute(
                    "SELECT intent, farmer_en FROM voice_turns WHERE call_sid = ? "
                    "ORDER BY turn_no DESC LIMIT 1",
                    (r["call_sid"],),
                ).fetchone()
                out.append({
                    "call_sid": r["call_sid"], "phone": r["phone"], "channel": r["channel"],
                    "language": r["language"], "status": r["status"], "turn_count": r["turn_count"],
                    "started_at": r["started_at"], "ended_at": r["ended_at"],
                    "last_intent": last["intent"] if last else None,
                    "last_question": last["farmer_en"] if last else None,
                })
            return out
        finally:
            conn.close()


def get_call(call_sid: str) -> Optional[Dict[str, Any]]:
    init_call_tables()
    with DB_LOCK:
        conn = get_connection()
        try:
            call = conn.execute(
                "SELECT * FROM voice_calls WHERE call_sid = ?", (call_sid,)
            ).fetchone()
            if call is None:
                return None
            turns = conn.execute(
                "SELECT * FROM voice_turns WHERE call_sid = ? ORDER BY turn_no", (call_sid,)
            ).fetchall()
            return {
                "call_sid": call["call_sid"], "phone": call["phone"],
                "channel": call["channel"], "language": call["language"], "status": call["status"],
                "turn_count": call["turn_count"], "started_at": call["started_at"],
                "ended_at": call["ended_at"], "turns": [_turn_row(t) for t in turns],
            }
        finally:
            conn.close()


def call_statistics() -> Dict[str, Any]:
    init_call_tables()
    since = int(time.time()) - 86400
    with DB_LOCK:
        conn = get_connection()
        try:
            calls = conn.execute("SELECT COUNT(*) FROM voice_calls").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM voice_calls WHERE started_at >= ?", (since,)
            ).fetchone()[0]
            turns = conn.execute("SELECT COUNT(*) FROM voice_turns").fetchone()[0]
            median = conn.execute(
                "SELECT total_ms FROM voice_turns WHERE total_ms IS NOT NULL ORDER BY total_ms "
                "LIMIT 1 OFFSET (SELECT COUNT(*) FROM voice_turns WHERE total_ms IS NOT NULL) / 2"
            ).fetchone()
            intents = conn.execute(
                "SELECT intent, COUNT(*) AS n FROM voice_turns GROUP BY intent ORDER BY n DESC"
            ).fetchall()
            languages = conn.execute(
                "SELECT language, COUNT(*) AS n FROM voice_turns GROUP BY language ORDER BY n DESC"
            ).fetchall()
            return {
                "calls_total": calls,
                "calls_last_24h": today,
                "turns_total": turns,
                "median_turn_ms": median[0] if median else None,
                "intents": {r["intent"]: r["n"] for r in intents},
                "languages": {r["language"]: r["n"] for r in languages},
            }
        finally:
            conn.close()


def feed_since(seq: int) -> List[Dict[str, Any]]:
    with _feed_lock:
        return [e for e in _FEED if e["seq"] > seq]


def feed_head() -> int:
    with _feed_lock:
        return _feed_seq
