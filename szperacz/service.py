"""Logika wspólna dla wiersza poleceń i okienka."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .index import Index
from .models import Hit, Query


@dataclass
class Result:
    query: Query
    hits: list[Hit]
    answer: str | None = None
    notes: list[str] = field(default_factory=list)


def search_with_relax(index: Index, q: Query) -> tuple[list[Hit], bool]:
    """Najpierw wszystkie grupy słów (AND); jeśli pusto — dowolna grupa (OR)."""
    hits = index.search(q, "AND")
    if hits or len(q.keyword_groups) < 2:
        return hits, False
    return index.search(q, "OR"), True


def search_words(index: Index, q: Query) -> Result:
    notes = []
    if q.raw_fts:
        return Result(q, index.search(q))
    hits, relaxed = search_with_relax(index, q)
    if relaxed:
        notes.append("Nie znaleziono wszystkich słów naraz — pokazuję wiadomości z którymkolwiek.")
    return Result(q, hits, notes=notes)


def ask(cfg, index: Index, prompt: str, *, limit: int | None = None,
        with_answer: bool = True, extra: Query | None = None) -> Result:
    """Prompt po ludzku -> zapytanie (Claude) -> wyniki -> odpowiedź (Claude).

    extra: filtry ustawione ręcznie (np. w okienku) — mają pierwszeństwo przed AI.
    """
    from . import ai

    q = ai.parse_prompt(prompt, api_key=cfg.ai.api_key, model=cfg.ai.parse_model,
                        known_chats=index.known_chats(), known_senders=index.known_senders())
    q.limit = limit or cfg.ai.max_results_for_answer
    if extra:
        if extra.chats:
            q.chats = extra.chats
        if extra.senders:
            q.senders = extra.senders
        if extra.date_from:
            q.date_from = extra.date_from
        if extra.date_to:
            q.date_to = extra.date_to
        if extra.order == "date":
            q.order = "date"

    notes: list[str] = []
    hits, relaxed = search_with_relax(index, q)
    if not hits and (q.chats or q.senders) and not (extra and (extra.chats or extra.senders)):
        # nazwa czatu/osoby mogła być źle odgadnięta — próbujemy bez niej
        hits, relaxed = search_with_relax(index, replace(q, chats=[], senders=[]))
        if hits:
            notes.append("Bez filtra czatu/nadawcy — z nim nie było wyników.")
    if relaxed:
        notes.append("Luźniejsze dopasowanie: dowolna z grup słów.")

    answer = None
    if hits and with_answer:
        answer = ai.answer(prompt, hits, api_key=cfg.ai.api_key, model=cfg.ai.answer_model)
    return Result(q, hits, answer, notes)


def describe_query(q: Query) -> str:
    parts = []
    if q.keyword_groups:
        parts.append(" ORAZ ".join("(" + " | ".join(g) + ")" for g in q.keyword_groups))
    if q.chats:
        parts.append("czat: " + ", ".join(q.chats))
    if q.senders:
        parts.append("nadawca: " + ", ".join(q.senders))
    if q.date_from or q.date_to:
        parts.append(f"daty: {q.date_from or '…'} – {q.date_to or '…'}")
    if q.order == "date":
        parts.append("od najnowszych")
    return "; ".join(parts) or "(bez filtrów)"


def _to_hits(msgs, groups) -> list[Hit]:
    from .index import _iso, make_snippet
    return [Hit(row_id=-1, source=m.source, chat_name=m.chat_name, chat_type=m.chat_type,
                sender_name=m.sender_name, date=_iso(m.date), text=m.text,
                snippet=make_snippet(m.text, groups), link=m.link) for m in msgs]


@dataclass
class GlobalOutcome(Result):
    tg_query: str = ""
    payment_needed: int | None = None
    discovered: list = field(default_factory=list)


async def global_search(cfg, connector, prompt: str, *, use_ai: bool = False,
                        posts: bool = True, mine: bool = True, discover: bool = True,
                        allow_paid_stars: int | None = None, log=None) -> GlobalOutcome:
    """Szukanie w całym Telegramie na żywo (publiczne kanały, Twoje czaty, znalezione grupy)."""
    import asyncio

    from . import ai

    q = prompt.strip()
    if use_ai and cfg.ai.api_key and not q.startswith("#"):
        loop = asyncio.get_running_loop()
        q = await loop.run_in_executor(None, lambda: ai.telegram_query(
            prompt, api_key=cfg.ai.api_key, model=cfg.ai.parse_model))
    r = await connector.global_search(q, posts=posts, mine=mine, discover=discover,
                                      watched=cfg.telegram.watched,
                                      allow_paid_stars=allow_paid_stars, log=log)
    groups = [[w] for w in q.lstrip("#").split()]
    hits = _to_hits(r.messages, groups)
    out = GlobalOutcome(query=Query(keyword_groups=groups), hits=hits, notes=list(r.notes),
                        tg_query=q, payment_needed=r.payment_needed, discovered=r.discovered)
    if use_ai and cfg.ai.api_key and hits:
        loop = asyncio.get_running_loop()
        out.answer = await loop.run_in_executor(None, lambda: ai.answer(
            prompt, hits[:cfg.ai.max_results_for_answer], api_key=cfg.ai.api_key,
            model=cfg.ai.answer_model))
    return out
