"""Synchronizacja: konektor -> lokalny indeks (przyrostowo)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .connectors.base import Connector
from .index import Index, fold
from .models import ChatInfo

BATCH = 500


@dataclass
class ChatFilter:
    include: list[str]
    exclude: list[str]
    skip_channels: bool = False

    def allows(self, chat: ChatInfo) -> bool:
        name = fold(chat.name)
        if any(fold(x) in name for x in self.exclude if x):
            return False
        if chat.watched:          # obserwowane dodajesz świadomie — zawsze indeksowane
            return True
        if self.skip_channels and chat.type == "kanał":
            return False
        if any(fold(x) in name for x in self.exclude if x):
            return False
        if self.include and not any(fold(x) in name or x == chat.id for x in self.include if x):
            return False
        return True


async def sync(connector: Connector, index: Index, flt: ChatFilter,
               first_sync_limit: int = 0, only: list[str] | None = None,
               log: Callable[[str], None] = print, watched: list[str] | None = None,
               watched_limit: int = 2000) -> dict:
    if watched:
        chats = await connector.list_chats(watched=watched, log=log)
    else:
        chats = await connector.list_chats()
    if only:
        chats = [c for c in chats if any(fold(o) in fold(c.name) or o == c.id for o in only)]
    else:
        chats = [c for c in chats if flt.allows(c)]

    log(f"{connector.name}: {len(chats)} czatów do synchronizacji")
    summary = {"chats": len(chats), "new": 0, "errors": []}

    for i, chat in enumerate(chats, 1):
        last = index.get_last_id(connector.name, chat.id)
        lim = first_sync_limit
        if chat.watched and (lim == 0 or lim > watched_limit):
            lim = watched_limit   # duże publiczne kanały: tylko ostatnie N przy pierwszym razie
        newest_limit = lim if (last == 0 and lim > 0) else None
        # Gdy pobieramy od najstarszej, można zapisywać postęp w trakcie;
        # gdy pobieramy N najnowszych (od końca) — tylko na końcu.
        progressive = newest_limit is None
        batch, max_id, count = [], last, 0
        try:
            async for m in connector.fetch(chat, after_id=last, newest_limit=newest_limit):
                batch.append(m)
                max_id = max(max_id, m.msg_id)
                if len(batch) >= BATCH:
                    count += index.add(batch)
                    batch = []
                    if progressive:
                        index.set_last_id(connector.name, chat.id, chat.name, max_id)
            count += index.add(batch)
            index.set_last_id(connector.name, chat.id, chat.name, max_id)
        except Exception as e:  # jeden zepsuty czat nie zatrzymuje reszty
            if batch:
                count += index.add(batch)
                if progressive:
                    index.set_last_id(connector.name, chat.id, chat.name, max_id)
            summary["errors"].append((chat.name, repr(e)))
            log(f"  [{i}/{len(chats)}] {chat.name}: BŁĄD {e!r}")
            continue
        summary["new"] += count
        if count:
            log(f"  [{i}/{len(chats)}] {chat.name}: +{count}")
    return summary
