"""Konektor Telegram — logowanie jako Twoje konto (MTProto, biblioteka Telethon).

Tylko ODCZYT: konektor nie wysyła, nie edytuje i nie usuwa niczego.
Plik sesji (*.session) to klucz do konta — trzymaj go u siebie, nie udostępniaj.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from ..models import ChatInfo, Message
from .base import Connector


class NotLoggedIn(RuntimeError):
    pass


class LoginNeedsEmail(RuntimeError):
    pass


class QrCancelled(Exception):
    pass


RESEND = "__RESEND__"


def describe_sent_code(sent) -> str:
    """Po ludzku: gdzie Telegram wysłał kod logowania."""
    from telethon.tl.types import auth as A

    t = sent.type
    n = getattr(t, "length", None)
    digits = f"{n}-cyfrowy " if n else ""
    if isinstance(t, A.SentCodeTypeApp):
        where = (f"Telegram wysłał {digits}kod W APLIKACJI TELEGRAM (nie SMS-em) — na telefonie lub komputerze, "
                 "gdzie już jesteś zalogowany. Szukaj czatu od konta „Telegram” z niebieskim znaczkiem "
                 "(może być w Archiwum albo wyciszony).")
    elif isinstance(t, (A.SentCodeTypeSms, A.SentCodeTypeFirebaseSms, A.SentCodeTypeSmsWord, A.SentCodeTypeSmsPhrase)):
        where = f"Telegram wysłał {digits}kod SMS-em."
    elif isinstance(t, A.SentCodeTypeCall):
        where = f"Telegram zadzwoni i przeczyta {digits}kod."
    elif isinstance(t, A.SentCodeTypeFlashCall):
        where = "Telegram wykona krótkie połączenie — kodem jest numer, z którego dzwoni (wg wzoru: " + t.pattern + ")."
    elif isinstance(t, A.SentCodeTypeMissedCall):
        where = (f"Telegram wykona nieodebrane połączenie — kodem są ostatnie {t.length} cyfry numeru, "
                 f"z którego dzwoni (zaczyna się od {t.prefix}).")
    elif isinstance(t, A.SentCodeTypeEmailCode):
        where = f"Telegram wysłał {digits}kod NA E-MAIL: {t.email_pattern}"
    elif isinstance(t, A.SentCodeTypeFragmentSms):
        where = f"Kod przyszedł przez Fragment (numer anonimowy): {t.url}"
    else:
        where = f"Telegram wysłał kod ({type(t).__name__})."
    nxt = sent.next_type
    if nxt is not None:
        names = {"CodeTypeSms": "SMS-em", "CodeTypeCall": "połączeniem głosowym",
                 "CodeTypeFlashCall": "krótkim połączeniem", "CodeTypeMissedCall": "nieodebranym połączeniem",
                 "CodeTypeFragmentSms": "przez Fragment"}
        how = names.get(type(nxt).__name__, "inną drogą")
        wait = f" (za ok. {sent.timeout} s)" if getattr(sent, "timeout", None) else ""
        where += f"\n\nNie przyszło? Możesz poprosić o wysłanie {how}{wait}."
    return where


def _chat_type(dialog) -> str:
    if dialog.is_user:
        return "bot" if getattr(dialog.entity, "bot", False) else "prywatny"
    if dialog.is_group:
        return "grupa"
    return "kanał"


def _link(entity, msg_id: int) -> str | None:
    from telethon.tl.types import Channel

    if isinstance(entity, Channel):
        username = getattr(entity, "username", None)
        if not username and getattr(entity, "usernames", None):
            username = entity.usernames[0].username
        if username:
            return f"https://t.me/{username}/{msg_id}"
        return f"https://t.me/c/{entity.id}/{msg_id}"
    return None  # w czatach prywatnych / małych grupach Telegram nie ma linków do wiadomości


class TelegramConnector(Connector):
    name = "telegram"

    def __init__(self, api_id: int, api_hash: str, session_path: Path,
                 phone: str | None = None):
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.session_path = Path(session_path)
        self.phone = phone
        self.client = None

    def _make_client(self):
        from telethon import TelegramClient

        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        # flood_sleep_threshold: przy limitach Telegrama (FloodWait) czekamy
        # automatycznie do 5 min zamiast przerywać synchronizację.
        return TelegramClient(str(self.session_path), self.api_id, self.api_hash,
                              flood_sleep_threshold=300,
                              device_model="Szperacz", app_version="0.1")

    async def login(self, phone_cb=None, code_cb=None, password_cb=None, log=None) -> str:
        """Logowanie krok po kroku, z informacją, GDZIE Telegram wysłał kod.

        phone_cb()        -> numer telefonu (gdy nie ma go w ustawieniach)
        code_cb(info)     -> kod; info opisuje, gdzie go szukać. Zwrócenie RESEND
                             prosi Telegram o wysłanie kodu inną drogą (np. SMS).
        password_cb()     -> hasło 2FA
        Bez callbacków pyta w konsoli.
        """
        from telethon import errors, utils
        from telethon.tl import functions, types

        log = log or (lambda *_: None)
        phone_cb = phone_cb or (lambda: input("Numer telefonu (z kodem kraju, np. +447700900123): "))
        code_cb = code_cb or (lambda info: input(info + "\nKod (albo R = wyślij inaczej): "))
        if password_cb is None:
            import getpass
            password_cb = lambda: getpass.getpass("Hasło 2FA: ")  # noqa: E731

        self.client = self._make_client()
        client = self.client
        await client.connect()
        if await client.is_user_authorized():
            return utils.get_display_name(await client.get_me())

        phone = utils.parse_phone(self.phone or phone_cb()) or ""
        if not phone:
            raise errors.PhoneNumberInvalidError(None)
        log(f"Proszę Telegram o kod dla {phone[:4]}…{phone[-3:]}")
        sent = await client(functions.auth.SendCodeRequest(phone, self.api_id, self.api_hash,
                                                           types.CodeSettings()))
        if isinstance(sent, types.auth.SentCodeSuccess):
            return utils.get_display_name(await client.get_me())
        extra = ""
        while True:
            if isinstance(sent.type, types.auth.SentCodeTypeSetUpEmailRequired):
                raise LoginNeedsEmail(
                    "Telegram wymaga ustawienia e-maila do logowania, zanim wyśle kod do nowej aplikacji.\n\n"
                    "W aplikacji Telegram: Ustawienia → Prywatność i bezpieczeństwo → E-mail logowania "
                    "(Login Email) → ustaw e-mail. Potem kliknij Zaloguj jeszcze raz — kod przyjdzie na e-mail.")
            info = extra + describe_sent_code(sent)
            log(info.replace("\n", " "))
            code = (code_cb(info) or "").strip()
            if code.upper() in (RESEND, "R"):
                if not sent.next_type:
                    extra = "Telegram nie oferuje innego sposobu wysłania kodu. Poczekaj chwilę.\n\n"
                    continue
                sent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
                extra = "Wysłano ponownie.\n\n"
                continue
            code = code.replace(" ", "").replace("-", "")
            try:
                if isinstance(sent.type, types.auth.SentCodeTypeEmailCode):
                    await client(functions.auth.SignInRequest(
                        phone, sent.phone_code_hash,
                        email_verification=types.EmailVerificationCode(code)))
                else:
                    await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
                break
            except errors.SessionPasswordNeededError:
                for _ in range(3):
                    try:
                        await client.sign_in(password=password_cb())
                        break
                    except errors.PasswordHashInvalidError:
                        log("Nieprawidłowe hasło 2FA")
                        continue
                else:
                    raise errors.PasswordHashInvalidError(None)
                break
            except errors.PhoneCodeInvalidError:
                extra = "❌ Nieprawidłowy kod — spróbuj jeszcze raz.\n\n"
            except errors.PhoneCodeExpiredError:
                extra = "Kod wygasł — wysyłam nowy.\n\n"
                sent = await client(functions.auth.SendCodeRequest(phone, self.api_id, self.api_hash,
                                                                   types.CodeSettings()))
        return utils.get_display_name(await client.get_me())

    async def qr_login(self, show_qr, cancelled, password_cb=None, log=None) -> str:
        """Logowanie kodem QR: skanujesz go w aplikacji Telegram na telefonie
        (Ustawienia → Urządzenia → Połącz urządzenie). Żaden kod SMS nie jest potrzebny.

        show_qr(url, expires)  — pokaż/odśwież kod (wywoływane przy każdym nowym tokenie)
        cancelled()            — True = przerwij
        """
        import asyncio
        from datetime import datetime, timezone

        from telethon import errors, events, utils
        from telethon.tl import functions, types

        log = log or (lambda *_: None)
        self.client = self._make_client()
        client = self.client
        await client.connect()
        if await client.is_user_authorized():
            return utils.get_display_name(await client.get_me())

        qr = await client.qr_login()
        scanned = asyncio.Event()

        async def on_token(_):
            scanned.set()

        client.add_event_handler(on_token, events.Raw(types.UpdateLoginToken))
        try:
            show_qr(qr.url, qr.expires)
            log("Pokazano kod QR")
            while True:
                while not scanned.is_set():
                    if cancelled():
                        raise QrCancelled()
                    if datetime.now(timezone.utc) >= qr.expires:
                        await qr.recreate()
                        show_qr(qr.url, qr.expires)
                    try:
                        await asyncio.wait_for(scanned.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
                scanned.clear()
                log("Kod QR zeskanowany")
                try:
                    resp = await client(qr._request)
                    if isinstance(resp, types.auth.LoginTokenMigrateTo):
                        await client._switch_dc(resp.dc_id)
                        resp = await client(functions.auth.ImportLoginTokenRequest(resp.token))
                    if isinstance(resp, types.auth.LoginTokenSuccess):
                        await client._on_login(resp.authorization.user)
                        break
                    # inny wynik (np. nowy token) — czekamy dalej
                    if isinstance(resp, types.auth.LoginToken):
                        qr._resp = resp
                        show_qr(qr.url, qr.expires)
                except errors.SessionPasswordNeededError:
                    if password_cb is None:
                        import getpass
                        password_cb = lambda: getpass.getpass("Hasło 2FA: ")  # noqa: E731
                    for _ in range(3):
                        try:
                            await client.sign_in(password=password_cb())
                            break
                        except errors.PasswordHashInvalidError:
                            log("Nieprawidłowe hasło 2FA")
                    else:
                        raise errors.PasswordHashInvalidError(None)
                    break
        finally:
            client.remove_event_handler(on_token)
        return utils.get_display_name(await client.get_me())

    async def open(self) -> None:
        self.client = self._make_client()
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise NotLoggedIn("Program nie jest jeszcze zalogowany do Telegrama.\n\n"
                             "W okienku kliknij „Zaloguj” (prawy górny róg). "
                             "W konsoli: szperacz login")

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    async def resolve_public(self, ref: str):
        """'@kanal', 'kanal' albo 'https://t.me/kanal' -> encja publicznego kanału/grupy."""
        name = parse_public_ref(ref)
        if not name:
            raise ValueError(f"To nie wygląda na publiczny kanał: {ref!r}")
        return await self.client.get_entity(name)

    async def list_chats(self, watched: list[str] | None = None,
                         log=None) -> list[ChatInfo]:
        chats: list[ChatInfo] = []
        seen: set[str] = set()
        async for d in self.client.iter_dialogs():
            chats.append(ChatInfo(source=self.name, id=str(d.id), name=d.name or "(bez nazwy)",
                                  type=_chat_type(d), raw=d.entity))
            seen.add(str(d.id))
        # Obserwowane publiczne kanały/grupy — czytane BEZ dołączania.
        from telethon import utils
        for ref in watched or []:
            try:
                ent = await self.resolve_public(ref)
            except Exception as e:  # noqa: BLE001
                if log:
                    log(f"  obserwowany {ref}: nie znaleziono ({type(e).__name__})")
                continue
            cid = str(utils.get_peer_id(ent))
            if cid in seen:
                continue
            seen.add(cid)
            ctype = "grupa" if getattr(ent, "megagroup", False) else "kanał"
            chats.append(ChatInfo(source=self.name, id=cid, name=utils.get_display_name(ent),
                                  type=ctype, raw=ent, watched=True))
        return chats

    async def fetch(self, chat: ChatInfo, after_id: int = 0,
                    newest_limit: int | None = None) -> AsyncIterator[Message]:
        entity = chat.raw
        if after_id == 0 and newest_limit:
            it = self.client.iter_messages(entity, limit=newest_limit)
        else:
            it = self.client.iter_messages(entity, min_id=after_id, reverse=True)
        async for m in it:
            msg = _convert(m, chat.id, chat.name, chat.type, entity)
            if msg:
                yield msg

    # ------------------------------------------------------ cały Telegram
    async def global_search(self, query: str, *, posts: bool = True, discover: bool = True,
                            mine: bool = True, watched: list[str] | None = None,
                            limit: int = 50, per_chat: int = 15, max_discovered: int = 8,
                            allow_paid_stars: int | None = None,
                            log=None) -> "GlobalResult":
        """Szukanie na serwerach Telegrama (na żywo, bez lokalnego indeksu).

        posts    – globalna wyszukiwarka postów we WSZYSTKICH publicznych kanałach
                   (Premium: darmowy dzienny limit, potem gwiazdki — nigdy automatycznie).
                   Zapytanie zaczynające się od # = wyszukiwanie hashtagu.
        discover – publiczne kanały/grupy znalezione po nazwie + przeszukanie ich treści.
        mine     – przeszukanie wszystkich Twoich czatów na serwerze.
        watched  – obserwowane publiczne kanały (z ustawień) przeszukane od środka.
        """
        from telethon import utils

        res = GlobalResult()
        q = query.strip()
        if not q:
            return res
        log = log or (lambda *_: None)

        def add(msg, origin):
            if msg and (msg.chat_id, msg.msg_id) not in res._seen:
                res._seen.add((msg.chat_id, msg.msg_id))
                msg.chat_type = f"{msg.chat_type or ''} · {origin}".strip(" ·")
                res.messages.append(msg)

        # 1) globalna wyszukiwarka postów w publicznych kanałach
        if posts:
            await self._search_posts(q, res, add, limit, allow_paid_stars, log)
        if allow_paid_stars:      # płatne ponowienie dotyczy tylko postów
            res.messages.sort(key=lambda m: m.date, reverse=True)
            return res

        # 2) Twoje czaty (wyszukiwanie po stronie serwera)
        if mine and not q.startswith("#"):
            log("Szukam we wszystkich Twoich czatach…")
            try:
                async for m in self.client.iter_messages(None, search=q, limit=limit):
                    chat = m.chat
                    name = utils.get_display_name(chat) if chat else "?"
                    add(_convert(m, str(m.chat_id), name, None, chat), "Twój czat")
            except Exception as e:  # noqa: BLE001
                res.notes.append(f"Twoje czaty: błąd ({type(e).__name__}).")

        # 3) publiczne kanały/grupy znalezione po nazwie + obserwowane
        targets = []
        if discover and not q.startswith("#"):
            log("Szukam publicznych kanałów i grup pasujących do zapytania…")
            try:
                from telethon.tl.functions.contacts import SearchRequest
                found = await self.client(SearchRequest(q=q, limit=30))
                for ch in found.chats:
                    if getattr(ch, "username", None) or getattr(ch, "usernames", None):
                        targets.append((ch, "znaleziony po nazwie"))
                        res.discovered.append(ChatInfo(
                            self.name, str(utils.get_peer_id(ch)), utils.get_display_name(ch),
                            "grupa" if getattr(ch, "megagroup", False) else "kanał", raw=ch))
                targets = targets[:max_discovered]
            except Exception as e:  # noqa: BLE001
                res.notes.append(f"Wyszukiwanie kanałów: błąd ({type(e).__name__}).")
        for ref in watched or []:
            try:
                targets.append((await self.resolve_public(ref), "obserwowany"))
            except Exception:  # noqa: BLE001
                res.notes.append(f"Obserwowany {ref}: nie znaleziono.")
        terms = q
        for ent, origin in targets:
            name = utils.get_display_name(ent)
            log(f"  przeszukuję: {name}")
            ctype = "grupa" if getattr(ent, "megagroup", False) else "kanał"
            try:
                async for m in self.client.iter_messages(ent, search=terms, limit=per_chat):
                    add(_convert(m, str(utils.get_peer_id(ent)), name, ctype, ent), origin)
            except Exception as e:  # noqa: BLE001
                res.notes.append(f"{name}: nie da się przeszukać ({type(e).__name__}).")

        res.messages.sort(key=lambda m: m.date, reverse=True)
        return res

    async def _search_posts(self, q, res, add, limit, allow_paid_stars, log):
        from telethon import utils
        from telethon.tl import functions, types

        hashtag = q[1:].strip() if q.startswith("#") else None
        if not hashtag:
            try:
                fl = await self.client(functions.channels.CheckSearchPostsFloodRequest(query=q))
                res.flood = fl
                if not getattr(fl, "query_is_free", False) and fl.remains <= 0 and not allow_paid_stars:
                    res.payment_needed = getattr(fl, "stars_amount", None)
                    res.notes.append(_flood_note(fl))
                    return
            except Exception as e:  # noqa: BLE001
                if _err(e) == "PREMIUM_ACCOUNT_REQUIRED":
                    res.notes.append("Globalne wyszukiwanie postów wymaga Telegram Premium "
                                     "(wyszukiwanie #hashtagów działa bez limitu).")
                    return
                # starsze serwery / inne błędy — próbujemy mimo to
        log("Szukam we wszystkich publicznych kanałach…")
        try:
            r = await self.client(functions.channels.SearchPostsRequest(
                offset_rate=0, offset_peer=types.InputPeerEmpty(), offset_id=0,
                limit=min(limit, 100), hashtag=hashtag, query=None if hashtag else q,
                allow_paid_stars=allow_paid_stars))
        except Exception as e:  # noqa: BLE001
            code = _err(e)
            if code == "PREMIUM_ACCOUNT_REQUIRED":
                res.notes.append("Globalne wyszukiwanie postów wymaga Telegram Premium "
                                 "(wyszukiwanie #hashtagów działa bez limitu).")
            else:
                res.notes.append(f"Wyszukiwanie postów: błąd ({code or type(e).__name__}).")
            return
        if getattr(r, "search_flood", None):
            res.flood = r.search_flood
        chats = {c.id: c for c in getattr(r, "chats", [])}
        users = {u.id: u for u in getattr(r, "users", [])}
        n = 0
        for m in r.messages:
            peer = getattr(m, "peer_id", None)
            ch = chats.get(getattr(peer, "channel_id", None))
            if ch is None or not isinstance(m, types.Message):
                continue
            text = m.message or ""
            if not text:
                continue
            sender = None
            fid = getattr(m, "from_id", None)
            if fid is not None and getattr(fid, "user_id", None) in users:
                sender = utils.get_display_name(users[fid.user_id])
            add(Message(source=self.name, chat_id=str(utils.get_peer_id(ch)),
                        chat_name=utils.get_display_name(ch), chat_type="kanał",
                        msg_id=m.id, date=m.date, text=text,
                        sender_name=sender or utils.get_display_name(ch),
                        link=_link(ch, m.id)), "wszystkie publiczne")
            n += 1
        res.notes.insert(0, f"Publiczne kanały: {n} postów." + (
            f" Darmowych wyszukiwań na dziś: {res.flood.remains}/{res.flood.total_daily}."
            if res.flood and not hashtag else ""))


class GlobalResult:
    def __init__(self):
        self.messages: list[Message] = []
        self.discovered: list[ChatInfo] = []
        self.notes: list[str] = []
        self.flood = None
        self.payment_needed: int | None = None   # ile gwiazdek kosztuje to wyszukiwanie
        self._seen: set = set()


def parse_public_ref(ref: str) -> str | None:
    import re
    ref = ref.strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/(?:s/)?([A-Za-z0-9_]{4,})", ref)
    if m:
        name = m.group(1)
    else:
        name = ref.lstrip("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", name) or name.lower() in ("joinchat", "c", "addlist"):
        return None
    return name


def _err(e) -> str | None:
    return getattr(e, "message", None) or (str(e).split(" ")[0] if str(e).isupper() else None)


def _flood_note(fl) -> str:
    from datetime import datetime
    when = ""
    if getattr(fl, "wait_till", None):
        when = " Kolejne darmowe od " + datetime.fromtimestamp(fl.wait_till).strftime("%d.%m %H:%M") + "."
    price = f" Płatne wyszukiwanie: {fl.stars_amount} ⭐." if getattr(fl, "stars_amount", None) else ""
    return f"Wykorzystano dzienny limit darmowych wyszukiwań w publicznych kanałach ({fl.total_daily}).{when}{price}"


def _convert(m, chat_id: str, chat_name: str, chat_type: str | None, entity) -> Message | None:
    """Wiadomość Telethona -> nasz Message (None dla serwisowych/pustych)."""
    from telethon import utils

    if getattr(m, "action", None):        # wiadomości serwisowe (dołączył, zmienił nazwę…)
        return None
    text = m.message or ""
    fname = None
    try:
        if m.file is not None:
            fname = m.file.name
    except Exception:  # noqa: BLE001
        fname = None
    if fname:
        text = f"{text} [plik: {fname}]".strip()
    if not text:
        return None
    sender = m.sender
    if sender is not None:
        sender_name = utils.get_display_name(sender) or None
    elif getattr(m, "post", False):
        sender_name = chat_name
    else:
        sender_name = None
    return Message(
        source="telegram", chat_id=chat_id, chat_name=chat_name, chat_type=chat_type,
        msg_id=m.id, date=m.date, text=text,
        sender_id=str(m.sender_id) if m.sender_id is not None else None,
        sender_name=sender_name, link=_link(entity, m.id),
    )
