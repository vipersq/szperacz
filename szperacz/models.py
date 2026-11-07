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
    order: str = "rank"            # "rank" albo "date"
    limit: int = 30


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
