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
        if extra.order in ("date", "oldest"):
            q.order = extra.order
        for f in ("hour_from", "hour_to", "direction"):
            if getattr(extra, f) is not None:
                setattr(q, f, getattr(extra, f))
        if extra.chat_types:
            q.chat_types = list(extra.chat_types)
        if extra.content:
            q.content = list(extra.content)

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
    elif q.order == "oldest":
        parts.append("od najstarszych")
    if q.hour_from is not None or q.hour_to is not None:
        parts.append(f"godz. {q.hour_from if q.hour_from is not None else 0}–{q.hour_to if q.hour_to is not None else 23}")
    if q.chat_types:
        parts.append("typ: " + ", ".join(q.chat_types))
    if q.content:
        parts.append("treść: " + ", ".join(q.content))
    if q.direction:
        parts.append("moje" if q.direction == "out" else "od innych")
    return "; ".join(parts) or "(bez filtrów)"


def _to_hits(msgs, groups) -> list[Hit]:
    from .index import _iso, make_snippet
    return [Hit(row_id=-1, source=m.source, chat_name=m.chat_name, chat_type=m.chat_type,
                sender_name=m.sender_name, date=_iso(m.date), text=m.text,
                snippet=make_snippet(m.text, groups), link=m.link, chat_id=m.chat_id,
                msg_id=m.msg_id, media=m.media, extra=m.extra, is_out=m.is_out) for m in msgs]


def message_matches(m, q: Query) -> bool:
    """Te same filtry co w indeksie, dla wyników pobranych na żywo."""
    from datetime import date as _date

    from .index import fold

    local = m.date.astimezone()
    if q.date_from and local.date() < _date.fromisoformat(q.date_from[:10]):
        return False
    if q.date_to and local.date() > _date.fromisoformat(q.date_to[:10]):
        return False
    if q.hour_from is not None or q.hour_to is not None:
        hf = 0 if q.hour_from is None else q.hour_from
        ht = 23 if q.hour_to is None else q.hour_to
        h = local.hour
        if (hf <= ht and not hf <= h <= ht) or (hf > ht and not (h >= hf or h <= ht)):
            return False
    if q.chats and not any(fold(c) in fold(m.chat_name) for c in q.chats):
        return False
    if q.senders and not any(fold(x) in fold(m.sender_name or "") for x in q.senders):
        return False
    if q.chat_types and not any((m.chat_type or "").startswith(t) for t in q.chat_types):
        return False
    if q.content:
        ok = False
        for c in q.content:
            if c == "link":
                ok |= m.media == "webpage" or "http" in m.text or "t.me/" in m.text
            elif c == "text":
                ok |= m.media in (None, "", "webpage")
            elif c == "video":
                ok |= m.media in ("video", "gif")
            elif c == "audio":
                ok |= m.media in ("audio", "voice")
            else:
                ok |= m.media == c
        if not ok:
            return False
    if q.direction == "out" and not m.is_out:
        return False
    if q.direction == "in" and m.is_out:
        return False
    return True


@dataclass
class GlobalOutcome(Result):
    tg_query: str = ""
    payment_needed: int | None = None
    discovered: list = field(default_factory=list)


async def global_search(cfg, connector, prompt: str, *, use_ai: bool = False,
                        posts: bool = True, mine: bool = True, discover: bool = True,
                        allow_paid_stars: int | None = None, log=None,
                        filters: Query | None = None, local_ai=None) -> GlobalOutcome:
    """Szukanie w całym Telegramie na żywo (publiczne kanały, Twoje czaty, znalezione grupy)."""
    import asyncio

    from . import ai

    q = prompt.strip()
    loop = asyncio.get_running_loop()
    lai_ok = bool(local_ai is not None and (local_ai.status().get("text")))
    ai_notes = []
    if use_ai and not q.startswith("#") and len(q.split()) > 3:   # krótkie zapytania zostawiamy jak są
        try:
            if lai_ok:
                q = await loop.run_in_executor(None, lambda: local_ai.telegram_query(prompt))
            elif cfg.ai.api_key:
                q = await loop.run_in_executor(None, lambda: ai.telegram_query(
                    prompt, api_key=cfg.ai.api_key, model=cfg.ai.parse_model))
        except Exception as e:  # noqa: BLE001 — AI nie działa: szukamy dokładnie wpisanej frazy
            import logging
            logging.getLogger("szperacz").warning("fraza AI: %s", e, exc_info=e)
            ai_notes.append(f"AI nie dobrało frazy ({type(e).__name__}) — szukam dokładnie wpisanej.")
            q = prompt.strip()
    r = await connector.global_search(q, posts=posts, mine=mine, discover=discover,
                                      watched=cfg.telegram.watched,
                                      allow_paid_stars=allow_paid_stars, log=log)
    groups = [[w] for w in q.lstrip("#").split()]
    msgs = r.messages
    notes = ai_notes + list(r.notes)
    if filters is not None and filters.has_filters():
        before = len(msgs)
        msgs = [m for m in msgs if message_matches(m, filters)]
        if before != len(msgs):
            notes.append(f"Filtry: pokazano {len(msgs)} z {before} znalezionych.")
    if filters is not None and filters.order == "oldest":
        msgs = sorted(msgs, key=lambda m: m.date)
    hits = _to_hits(msgs, groups)
    out = GlobalOutcome(query=Query(keyword_groups=groups), hits=hits, notes=notes,
                        tg_query=q, payment_needed=r.payment_needed, discovered=r.discovered)
    if use_ai and hits and (lai_ok or cfg.ai.api_key):
        try:
            if lai_ok:
                out.answer = await loop.run_in_executor(None, lambda: local_ai.answer(prompt, hits))
            else:
                out.answer = await loop.run_in_executor(None, lambda: ai.answer(
                    prompt, hits[:cfg.ai.max_results_for_answer], api_key=cfg.ai.api_key,
                    model=cfg.ai.answer_model))
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("szperacz").warning("streszczenie AI: %s", e, exc_info=e)
            out.notes.append(f"AI nie streściło wyników ({type(e).__name__}).")
    return out
