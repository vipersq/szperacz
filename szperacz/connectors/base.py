"""Interfejs konektora. Każdy komunikator implementuje te same metody,
dzięki czemu indeks i wyszukiwarka nie wiedzą, skąd pochodzą wiadomości."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..models import ChatInfo, Message


class Connector(ABC):
    name: str = "base"

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def open(self) -> None:  # pragma: no cover - domyślnie nic
        pass

    async def close(self) -> None:  # pragma: no cover
        pass

    @abstractmethod
    async def list_chats(self) -> list[ChatInfo]:
        ...

    @abstractmethod
    def fetch(self, chat: ChatInfo, after_id: int = 0,
              newest_limit: int | None = None) -> AsyncIterator[Message]:
        """Zwraca wiadomości z czatu.

        - after_id > 0: tylko nowsze niż after_id, od najstarszej (przyrostowo).
        - after_id == 0 i newest_limit: tylko N najnowszych (kolejność dowolna).
        - after_id == 0 bez limitu: cała historia od najstarszej.
        """
        ...
