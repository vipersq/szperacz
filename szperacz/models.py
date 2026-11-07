"""Wspólny format danych dla wszystkich komunikatorów."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChatInfo:
    """Jeden czat / grupa / kanał w danym komunikatorze."""
    source: str            # np. "telegram"
    id: str                # identyfikator czatu w danym komunikatorze
    name: str
    type: str              # "prywatny", "grupa", "kanał", "bot"
    raw: Any = field(default=None, repr=False)  # obiekt natywny konektora
    watched: bool = False  # publiczny kanał obserwowany bez dołączania


@dataclass
class Message:
    """Jedna wiadomość, znormalizowana."""
    source: str
    chat_id: str
    chat_name: str
    msg_id: int
    date: datetime         # świadoma strefy, UTC
    text: str
    sender_id: str | None = None
    sender_name: str | None = None
    link: str | None = None
    chat_type: str | None = None
    media: str | None = None        # photo / video / audio / voice / document / webpage / poll / geo / sticker
    extra: dict | None = None       # np. {"title","description","site","url"} dla podglądu linku, {"file"} dla plików
    is_out: bool = False            # wysłana przeze mnie


MEDIA_LABELS = {
    "photo": "📷", "video": "🎬", "audio": "🎵", "voice": "🎤", "document": "📎",
    "webpage": "🔗", "poll": "📊", "geo": "📍", "sticker": "🙂", "gif": "🎞",
}


@dataclass
class Query:
    """Zapytanie do indeksu.

    keyword_groups: grupy słów — wewnątrz grupy alternatywy (OR),
    grupy łączone przez AND. Słowo zakończone '*' = dopasowanie prefiksu
    (przydatne przy polskiej odmianie: "anten*" łapie antena/antenie/anteny).
    """
    keyword_groups: list[list[str]] = field(default_factory=list)
    raw_fts: str | None = None
    chats: list[str] = field(default_factory=list)      # fragmenty nazw czatów
    senders: list[str] = field(default_factory=list)    # fragmenty nazw nadawców
    sources: list[str] = field(default_factory=list)
    date_from: str | None = None   # YYYY-MM-DD włącznie
    date_to: str | None = None     # YYYY-MM-DD włącznie
    order: str = "rank"            # "rank", "date" (najnowsze) albo "oldest"
    limit: int = 30
    hour_from: int | None = None   # godziny lokalne 0–23 (włącznie); można „przez północ”: 22–6
    hour_to: int | None = None
    chat_types: list[str] = field(default_factory=list)   # "prywatny", "grupa", "kanał", "bot"
    content: list[str] = field(default_factory=list)      # "photo","video","document","link","audio","text"
    direction: str | None = None   # "out" = moje, "in" = od innych

    def has_filters(self) -> bool:
        return bool(self.chats or self.senders or self.date_from or self.date_to or self.chat_types
                    or self.content or self.direction or self.hour_from is not None
                    or self.hour_to is not None)


@dataclass
class Hit:
    row_id: int
    source: str
    chat_name: str
    chat_type: str | None
    sender_name: str | None
    date: str
    text: str
    snippet: str
    link: str | None
    chat_id: str | None = None
    msg_id: int | None = None
    media: str | None = None
    extra: dict | None = None
    is_out: bool = False
    verdict: dict | None = None     # ocena: {"cat","src","why","lang","pl"} (tarcza / lokalne AI)
