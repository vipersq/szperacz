"""Lokalny indeks wiadomości: SQLite + wyszukiwanie pełnotekstowe FTS5.

Polskie znaki: tokenizer FTS5 sam usuwa ogonki (ą->a, ż->z…), ale NIE zamienia
'ł' na 'l'. Dlatego do FTS trafia tekst z ł->l (funkcja fold_l), a zapytania
składane są tak samo. Dzięki temu "lukasz" znajduje "Łukasz", "zolw" — "żółw".
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import json

from .models import Hit, Message, Query

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    chat_name   TEXT NOT NULL,
    chat_type   TEXT,
    msg_id      INTEGER NOT NULL,
    date        TEXT NOT NULL,
    sender_id   TEXT,
    sender_name TEXT,
    text        TEXT NOT NULL,
    link        TEXT,
    UNIQUE (source, chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_date ON messages(date);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, sender_name, chat_name,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, sender_name, chat_name)
    VALUES (new.id, fold_l(new.text), fold_l(new.sender_name), fold_l(new.chat_name));
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.id;
    INSERT INTO messages_fts(rowid, text, sender_name, chat_name)
    VALUES (new.id, fold_l(new.text), fold_l(new.sender_name), fold_l(new.chat_name));
END;

CREATE TABLE IF NOT EXISTS sync_state (
    source      TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    chat_name   TEXT,
    last_msg_id INTEGER NOT NULL DEFAULT 0,
    synced_at   TEXT,
    PRIMARY KEY (source, chat_id)
);
"""

_L_FOLD = str.maketrans({"ł": "l", "Ł": "L"})


def fold_l(s: str | None) -> str | None:
    return s.translate(_L_FOLD) if s else s


def _fold_char(c: str) -> str:
    c = c.translate(_L_FOLD)
    d = unicodedata.normalize("NFKD", c)
    base = "".join(ch for ch in d if not unicodedata.combining(ch)) or c
    return base.casefold()[:1] or c


def fold(s: str | None) -> str:
    """Małe litery bez polskich znaków, ZNAK ZA ZNAK (ta sama długość co wejście):
    'Łukasz Żółć' -> 'lukasz zolc'. Stała długość pozwala mapować pozycje
    trafień z powrotem na oryginalny tekst."""
    if not s:
        return ""
    return "".join(_fold_char(c) for c in s)


# ------------------------------------------------------------- budowa zapytań
def _clean_term(term: str) -> tuple[str, bool] | None:
    term = term.strip()
    prefix = term.endswith("*")
    term = term.rstrip("*").replace('"', " ").strip()
    if not term:
        return None
    return term, prefix


def _fts_term(term: str) -> str | None:
    c = _clean_term(term)
    if not c:
        return None
    t, prefix = c
    t = fold_l(t)
    return f'"{t}"*' if prefix else f'"{t}"'


def build_fts(groups: list[list[str]], mode: str = "AND") -> str | None:
    parts = []
    for g in groups:
        terms = list(dict.fromkeys(t for t in (_fts_term(x) for x in g) if t))
        if not terms:
            continue
        parts.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
    if not parts:
        return None
    return f" {mode} ".join(parts)


def make_snippet(text: str, groups: list[list[str]], width: int = 160, lead: int | None = None) -> str:
    """Fragment oryginalnego tekstu wokół pierwszego trafienia, trafienia w «»."""
    terms = []
    for g in groups:
        for x in g:
            c = _clean_term(x)
            if c:
                terms.append(c)
    flat = " ".join(text.split())
    if not terms:
        return flat[:width] + ("…" if len(flat) > width else "")
    folded = fold(flat)
    pats = []
    for t, prefix in terms:
        ft = re.escape(fold(t))
        pats.append(rf"(?<!\w){ft}\w*" if prefix else rf"(?<!\w){ft}(?!\w)")
    rx = re.compile("|".join(pats))
    matches = list(rx.finditer(folded))
    if not matches:
        return flat[:width] + ("…" if len(flat) > width else "")
    lead = width // 3 if lead is None else lead
    start = max(0, matches[0].start() - lead)
    if start > 0:  # zacznij od początku słowa
        sp = flat.rfind(" ", 0, start + 1)
        start = sp + 1 if sp != -1 and matches[0].start() - sp < lead + 15 else start
    end = min(len(flat), start + width)
    out, pos = [], start
    for m in matches:
        if m.start() < start or m.end() > end:
            continue
        out.append(flat[pos:m.start()])
        out.append("«" + flat[m.start():m.end()] + "»")
        pos = m.end()
    out.append(flat[pos:end])
    return ("…" if start > 0 else "") + "".join(out) + ("…" if end < len(flat) else "")


def _loads(s):
    try:
        return json.loads(s) if s else None
    except ValueError:
        return None


def _iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class Index:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("fold", 1, fold, deterministic=True)
        self.db.create_function("fold_l", 1, fold_l, deterministic=True)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Dodaje kolumny z nowszych wersji do istniejącej bazy (dane zostają)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(messages)")}
        for name, decl in (("media", "TEXT"), ("extra", "TEXT"), ("is_out", "INTEGER DEFAULT 0")):
            if name not in cols:
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {name} {decl}")
        self.db.commit()

    # ------------------------------------------------------------------ zapis
    def add(self, msgs: Iterable[Message]) -> int:
        rows = [(
            m.source, m.chat_id, m.chat_name, m.chat_type, m.msg_id, _iso(m.date),
            m.sender_id, m.sender_name, m.text, m.link,
            m.media, json.dumps(m.extra, ensure_ascii=False) if m.extra else None, int(bool(m.is_out)),
        ) for m in msgs]
        if not rows:
            return 0
        self.db.executemany("""
            INSERT INTO messages (source, chat_id, chat_name, chat_type, msg_id, date,
                                  sender_id, sender_name, text, link, media, extra, is_out)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source, chat_id, msg_id) DO UPDATE SET
                text=excluded.text, sender_name=excluded.sender_name,
                chat_name=excluded.chat_name, link=excluded.link,
                media=COALESCE(excluded.media, messages.media),
                extra=COALESCE(excluded.extra, messages.extra)
            WHERE messages.text IS NOT excluded.text
               OR messages.sender_name IS NOT excluded.sender_name
               OR messages.chat_name IS NOT excluded.chat_name
               OR messages.media IS NOT excluded.media
               OR messages.extra IS NOT excluded.extra
        """, rows)
        self.db.commit()
        return len(rows)

    def update_media(self, source: str, chat_id: str, msg_id: int, media: str | None,
                     extra: dict | None) -> None:
        """Uzupełnia typ mediów starszych wiadomości (z wersji sprzed 1.1) po podglądzie."""
        self.db.execute("UPDATE messages SET media=?, extra=? WHERE source=? AND chat_id=? AND msg_id=?",
                        (media or "", json.dumps(extra, ensure_ascii=False) if extra else None,
                         source, chat_id, int(msg_id)))
        self.db.commit()

    def get_last_id(self, source: str, chat_id: str) -> int:
        r = self.db.execute("SELECT last_msg_id FROM sync_state WHERE source=? AND chat_id=?",
                            (source, chat_id)).fetchone()
        return int(r[0]) if r else 0

    def set_last_id(self, source: str, chat_id: str, chat_name: str, last_id: int) -> None:
        self.db.execute("""
            INSERT INTO sync_state(source, chat_id, chat_name, last_msg_id, synced_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(source, chat_id) DO UPDATE SET
                chat_name=excluded.chat_name,
                last_msg_id=MAX(sync_state.last_msg_id, excluded.last_msg_id),
                synced_at=excluded.synced_at
        """, (source, chat_id, chat_name, last_id, _iso(datetime.now(timezone.utc))))
        self.db.commit()

    # ---------------------------------------------------------------- odczyt
    def search(self, q: Query, fts_mode: str = "AND") -> list[Hit]:
        fts = q.raw_fts or build_fts(q.keyword_groups, fts_mode)
        where, params = [], []

        for col, values in (("m.chat_name", q.chats), ("m.sender_name", q.senders)):
            values = [v for v in values if v and v.strip()]
            if values:
                where.append("(" + " OR ".join(f"fold({col}) LIKE ?" for _ in values) + ")")
                params += [f"%{fold(v.strip())}%" for v in values]
        if q.sources:
            where.append("m.source IN (" + ",".join("?" * len(q.sources)) + ")")
            params += list(q.sources)
        if q.date_from:
            where.append("m.date >= ?")
            params.append(date.fromisoformat(q.date_from[:10]).isoformat())
        if q.date_to:
            nxt = date.fromisoformat(q.date_to[:10]) + timedelta(days=1)
            where.append("m.date < ?")
            params.append(nxt.isoformat())
        if q.hour_from is not None or q.hour_to is not None:
            hf = 0 if q.hour_from is None else int(q.hour_from)
            ht = 23 if q.hour_to is None else int(q.hour_to)
            hour = "CAST(strftime('%H', m.date, 'localtime') AS INTEGER)"
            if hf <= ht:
                where.append(f"{hour} BETWEEN ? AND ?")
            else:  # przez północ, np. 22–6
                where.append(f"({hour} >= ? OR {hour} <= ?)")
            params += [hf, ht]
        if q.chat_types:
            where.append("(" + " OR ".join("m.chat_type LIKE ?" for _ in q.chat_types) + ")")
            params += [f"{t}%" for t in q.chat_types]
        if q.content:
            conds = []
            for c in q.content:
                if c == "link":
                    conds.append("(m.media = 'webpage' OR m.text LIKE '%http%' OR m.text LIKE '%t.me/%')")
                elif c == "text":
                    conds.append("(m.media IS NULL OR m.media IN ('', 'webpage'))")
                elif c == "video":
                    conds.append("m.media IN ('video', 'gif')")
                elif c == "audio":
                    conds.append("m.media IN ('audio', 'voice')")
                else:
                    conds.append("m.media = ?")
                    params.append(c)
            where.append("(" + " OR ".join(conds) + ")")
        if q.direction == "out":
            where.append("m.is_out = 1")
        elif q.direction == "in":
            where.append("COALESCE(m.is_out, 0) = 0")

        if fts:
            sql = """
                SELECT m.id, m.source, m.chat_name, m.chat_type, m.sender_name, m.date,
                       m.text, m.link, m.chat_id, m.msg_id, m.media, m.extra, m.is_out
                FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
                WHERE messages_fts MATCH ?
            """
            params = [fts] + params
            order = {"rank": "bm25(messages_fts, 10.0, 2.0, 1.0)", "oldest": "m.date ASC"}.get(q.order, "m.date DESC")
        else:
            sql = """
                SELECT m.id, m.source, m.chat_name, m.chat_type, m.sender_name, m.date,
                       m.text, m.link, m.chat_id, m.msg_id, m.media, m.extra, m.is_out
                FROM messages m WHERE 1=1
            """
            order = "m.date ASC" if q.order == "oldest" else "m.date DESC"
        if where:
            sql += " AND " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        params.append(int(q.limit))

        rows = self.db.execute(sql, params).fetchall()
        return [Hit(row_id=r["id"], source=r["source"], chat_name=r["chat_name"],
                    chat_type=r["chat_type"], sender_name=r["sender_name"],
                    date=r["date"], text=r["text"],
                    snippet=make_snippet(r["text"], q.keyword_groups),
                    link=r["link"], chat_id=r["chat_id"], msg_id=r["msg_id"], media=r["media"],
                    extra=_loads(r["extra"]), is_out=bool(r["is_out"])) for r in rows]

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        per = self.db.execute("""
            SELECT source, COUNT(DISTINCT chat_id) AS chats, COUNT(*) AS msgs,
                   MIN(date) AS oldest, MAX(date) AS newest
            FROM messages GROUP BY source
        """).fetchall()
        return {"total": total, "sources": [dict(r) for r in per]}

    def known_chats(self, limit: int = 300) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT chat_name FROM messages GROUP BY source, chat_id "
            "ORDER BY MAX(date) DESC LIMIT ?", (limit,))]

    def known_senders(self, limit: int = 300) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT sender_name FROM messages WHERE sender_name IS NOT NULL "
            "GROUP BY sender_name ORDER BY COUNT(*) DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        self.db.close()
