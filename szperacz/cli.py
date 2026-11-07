"""Wiersz poleceń: szperacz login | czaty | sync | szukaj | pytaj | wszedzie | obserwuj | stat"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import config as cfgmod
from .index import Index
from .models import Hit, Query
from .service import describe_query, search_with_relax  # noqa: F401 (re-eksport)
from . import service


def _utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _telegram(cfg):
    cfgmod.require_telegram(cfg)
    from .connectors.telegram import TelegramConnector
    return TelegramConnector(cfg.telegram.api_id, cfg.telegram.api_hash,
                             cfg.telegram_session, cfg.telegram.phone)


def print_hits(hits: list[Hit], numbered: bool = False) -> None:
    if not hits:
        print("Brak wyników.")
        return
    for n, h in enumerate(hits, 1):
        when = h.date[:16].replace("T", " ")
        head = f"[{n}] " if numbered else ""
        print(f"{head}{when}  {h.chat_name}  ›  {h.sender_name or '?'}   ({h.source})")
        print(f"    {h.snippet}")
        if h.link:
            print(f"    {h.link}")
        print()


# ----------------------------------------------------------------- komendy
async def cmd_login(cfg, args) -> int:
    tg = _telegram(cfg)
    try:
        if args.qr:
            def show(url, _exp):
                import qrcode
                q = qrcode.QRCode(border=1)
                q.add_data(url)
                q.print_ascii(invert=True)
                print("Telegram na telefonie → Ustawienia → Urządzenia → Połącz urządzenie → zeskanuj (Ctrl+C = przerwij)")
            name = await tg.qr_login(show, lambda: False, log=print)
        else:
            name = await tg.login(log=print)
        print(f"Zalogowano do Telegrama jako: {name}")
        print(f"Sesja zapisana w: {cfg.telegram_session}.session  (nie udostępniaj tego pliku!)")
    finally:
        await tg.close()
    return 0


async def cmd_czaty(cfg, args) -> int:
    from .sync import ChatFilter
    flt = ChatFilter(cfg.telegram.include, cfg.telegram.exclude, cfg.telegram.skip_channels)
    async with _telegram(cfg) as tg:
        chats = await tg.list_chats()
    for c in chats:
        mark = "✓" if flt.allows(c) else "–"
        print(f"{mark} {c.type:9} {c.id:>16}  {c.name}")
    print(f"\n{len(chats)} czatów; ✓ = będzie indeksowany wg config.toml")
    return 0


async def cmd_sync(cfg, args) -> int:
    from .sync import ChatFilter, sync
    flt = ChatFilter(cfg.telegram.include, cfg.telegram.exclude, cfg.telegram.skip_channels)
    limit = args.limit if args.limit is not None else cfg.telegram.first_sync_limit
    index = Index(cfg.index_path)
    try:
        async with _telegram(cfg) as tg:
            s = await sync(tg, index, flt, first_sync_limit=limit, only=args.czat or None,
                           watched=cfg.telegram.watched, watched_limit=cfg.telegram.watched_limit)
    finally:
        index.close()
    print(f"\nGotowe: {s['new']} nowych/zmienionych wiadomości z {s['chats']} czatów.")
    if s["errors"]:
        print(f"Błędy w {len(s['errors'])} czatach (pominięte, spróbuj ponownie później).")
    return 0


def _query_from_args(args) -> Query:
    words = args.slowa or []
    hf = ht = None
    if args.godz:
        a, _, b = args.godz.partition("-")
        hf, ht = int(a), int(b or a)
    groups = [[w] for w in words]
    return Query(
        keyword_groups=groups if not args.fts else [],
        raw_fts=" ".join(words) if args.fts else None,
        chats=args.czat or [], senders=args.nadawca or [],
        date_from=args.od, date_to=args.do,
        order="oldest" if args.najstarsze else ("date" if args.najnowsze else "rank"), limit=args.n,
        hour_from=hf, hour_to=ht, chat_types=args.typ or [], content=args.tresc or [],
        direction="out" if args.moje else ("in" if args.od_innych else None),
    )


def cmd_szukaj(cfg, args) -> int:
    index = Index(cfg.index_path)
    try:
        r = service.search_words(index, _query_from_args(args))
        for n in r.notes:
            print(f"({n})\n")
        print_hits(r.hits)
    finally:
        index.close()
    return 0


def cmd_pytaj(cfg, args) -> int:
    prompt = " ".join(args.prompt)
    index = Index(cfg.index_path)
    try:
        r = service.ask(cfg, index, prompt, limit=args.n, with_answer=not args.bez_odpowiedzi)
        print(f"Szukam: {describe_query(r.query)}\n")
        for n in r.notes:
            print(f"({n})\n")
        if not r.hits:
            print("Brak pasujących wiadomości w indeksie.")
        elif r.answer is None:
            print_hits(r.hits)
        else:
            print(r.answer)
            print("\n— Źródła —\n")
            print_hits(r.hits, numbered=True)
    finally:
        index.close()
    return 0


async def cmd_wszedzie(cfg, args) -> int:
    prompt = " ".join(args.zapytanie)
    use_ai = bool(args.ai)
    if use_ai and not cfg.ai.api_key:
        print("Brak klucza Claude API — szukam dokładnie tej frazy.\n")
        use_ai = False
    async with _telegram(cfg) as tg:
        r = await service.global_search(cfg, tg, prompt, use_ai=use_ai,
                                        posts=not args.bez_publicznych, discover=not args.bez_kanalow,
                                        mine=not args.bez_moich,
                                        allow_paid_stars=args.zaplac, log=print)
        if r.payment_needed and not args.zaplac:
            print(f"\nAby mimo to przeszukać wszystkie publiczne kanały, uruchom ponownie z: "
                  f"--zaplac {r.payment_needed}   (koszt: {r.payment_needed} ⭐)")
    if use_ai:
        print(f"Fraza dla Telegrama: {r.tg_query}")
    print()
    for n in r.notes:
        print(f"• {n}")
    if r.discovered:
        print("• Znalezione publiczne kanały/grupy: " + ", ".join(c.name for c in r.discovered[:10]))
    print()
    if r.answer:
        print(r.answer)
        print("\n— Źródła —\n")
    print_hits(r.hits[: args.n], numbered=bool(r.answer))
    return 0


def cmd_obserwuj(cfg, args) -> int:
    from .connectors.telegram import parse_public_ref
    for ref in args.kanal:
        if not parse_public_ref(ref):
            print(f"Pomijam {ref}: podaj @nazwę albo link https://t.me/nazwa (tylko publiczne).")
            continue
        added = cfgmod.add_watched(cfgmod.find_config(args.config) or cfg.base_dir / "config.toml", ref)
        print(("Dodano: " if added else "Już obserwowany: ") + ref)
    print("Następny 'szperacz sync' pobierze ich wiadomości (bez dołączania).")
    return 0


def cmd_stat(cfg, args) -> int:
    index = Index(cfg.index_path)
    try:
        s = index.stats()
    finally:
        index.close()
    print(f"Indeks: {cfg.index_path}")
    print(f"Wiadomości razem: {s['total']}")
    for r in s["sources"]:
        print(f"  {r['source']}: {r['msgs']} wiadomości w {r['chats']} czatach, "
              f"{(r['oldest'] or '')[:10]} – {(r['newest'] or '')[:10]}")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="szperacz",
                                description="Wyszukiwarka wiadomości z komunikatorów.")
    p.add_argument("--config", help="ścieżka do config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="zaloguj się do Telegrama (jednorazowo)")
    s.add_argument("--qr", action="store_true", help="logowanie kodem QR zamiast kodu z SMS/aplikacji")
    sub.add_parser("czaty", help="pokaż listę czatów Telegrama")

    s = sub.add_parser("sync", help="pobierz nowe wiadomości do indeksu")
    s.add_argument("--czat", action="append", help="tylko czaty zawierające ten fragment nazwy (można powtarzać)")
    s.add_argument("--limit", type=int, help="przy pierwszej synchronizacji czatu: tylko N najnowszych (0 = wszystko)")

    s = sub.add_parser("szukaj", help="wyszukaj słowa (bez AI)")
    s.add_argument("slowa", nargs="*", help="słowa; końcówka * = dowolna odmiana, np. anten*")
    s.add_argument("--czat", action="append")
    s.add_argument("--nadawca", action="append")
    s.add_argument("--od", help="YYYY-MM-DD")
    s.add_argument("--do", help="YYYY-MM-DD")
    s.add_argument("--najnowsze", action="store_true", help="sortuj od najnowszych zamiast trafności")
    s.add_argument("--fts", action="store_true", help="surowe zapytanie FTS5 (AND/OR/NOT, NEAR)")
    s.add_argument("--najstarsze", action="store_true", help="sortuj od najstarszych")
    s.add_argument("--godz", metavar="OD-DO", help="pora dnia, np. 8-17 albo 22-5")
    s.add_argument("--typ", action="append", choices=["prywatny", "grupa", "kanał", "bot"],
                   help="rodzaj czatu (można powtarzać)")
    s.add_argument("--tresc", action="append",
                   choices=["text", "photo", "video", "document", "link", "audio", "poll", "geo"],
                   help="zawartość (można powtarzać)")
    s.add_argument("--moje", action="store_true", help="tylko moje wiadomości")
    s.add_argument("--od-innych", action="store_true", help="tylko wiadomości od innych")
    s.add_argument("-n", type=int, default=20)

    s = sub.add_parser("pytaj", help="zapytaj po ludzku (Claude)")
    s.add_argument("prompt", nargs="+")
    s.add_argument("--bez-odpowiedzi", action="store_true", help="tylko lista wyników, bez streszczenia")
    s.add_argument("-n", type=int, help="ile wiadomości przekazać do odpowiedzi")

    s = sub.add_parser("wszedzie", help="szukaj w CAŁYM Telegramie (publiczne kanały, Twoje czaty, publiczne grupy)")
    s.add_argument("zapytanie", nargs="+", help="fraza albo #hashtag")
    s.add_argument("--ai", action="store_true", help="pytanie po ludzku: Claude dobierze frazę i streści wyniki")
    s.add_argument("--zaplac", type=int, metavar="GWIAZDKI",
                   help="zgoda na płatne wyszukiwanie w publicznych kanałach po wyczerpaniu darmowego limitu")
    s.add_argument("--bez-publicznych", action="store_true", help="bez globalnej wyszukiwarki postów")
    s.add_argument("--bez-kanalow", action="store_true", help="bez szukania publicznych kanałów/grup po nazwie")
    s.add_argument("--bez-moich", action="store_true", help="bez przeszukiwania Twoich czatów")
    s.add_argument("-n", type=int, default=40)

    s = sub.add_parser("obserwuj", help="dodaj publiczny kanał/grupę do indeksu (bez dołączania)")
    s.add_argument("kanal", nargs="+", help="@nazwa albo https://t.me/nazwa")

    sub.add_parser("stat", help="statystyki indeksu")
    sub.add_parser("gui", help="otwórz okienko (domyślne przy uruchomieniu bez polecenia)")
    sub.add_parser("menu", help="menu tekstowe w konsoli")
    return p


MENU = """
==================== SZPERACZ ====================
 1) Zaloguj do Telegrama (jednorazowo)
 2) Pokaż czaty
 3) Synchronizuj (pobierz nowe wiadomości)
 4) Szukaj słów (bez AI)
 5) Zapytaj po ludzku (Claude)
 6) Statystyki indeksu
 7) Otwórz config.toml
 0) Wyjście
=================================================="""


def _open_file(path) -> None:
    import os
    import subprocess
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        print(f"Edytuj plik: {path}")


def ensure_config() -> "Path":
    """Jeśli nie ma config.toml, tworzy go z szablonu obok programu."""
    from pathlib import Path
    found = cfgmod.find_config()
    if found:
        return found
    path = Path.cwd() / "config.toml"
    path.write_text(cfgmod.TEMPLATE, encoding="utf-8")
    print(f"Utworzono {path}\nWpisz api_id i api_hash z https://my.telegram.org, zapisz i wróć tutaj.")
    _open_file(path)
    return path


def menu() -> int:
    cfg_path = ensure_config()
    actions = {"1": ["login"], "2": ["czaty"], "3": ["sync"], "6": ["stat"]}
    while True:
        print(MENU)
        try:
            choice = input("Wybierz: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "0":
            return 0
        if choice == "7":
            _open_file(cfg_path)
            continue
        if choice in actions:
            argv = actions[choice]
        elif choice == "4":
            words = input("Słowa (końcówka * = dowolna odmiana, np. anten*): ").split()
            if not words:
                continue
            argv = ["szukaj", *words]
        elif choice == "5":
            prompt = input("O co chodzi? ").strip()
            if not prompt:
                continue
            argv = ["pytaj", prompt]
        else:
            continue
        print()
        try:
            main(["--config", str(cfg_path), *argv])
        except SystemExit:
            pass
        except Exception as e:
            print(f"\nNieoczekiwany błąd: {e!r}")
        try:
            input("\n[Enter] — powrót do menu")
        except (EOFError, KeyboardInterrupt):
            return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    if argv is None:
        argv = sys.argv[1:]
        if not argv:
            try:
                from .gui import main as gui_main
            except ImportError:      # brak Tkintera — menu tekstowe
                return menu()
            gui_main()
            return 0
    if argv == ["menu"]:
        return menu()
    if argv == ["gui"]:
        from .gui import main as gui_main
        gui_main()
        return 0
    args = build_parser().parse_args(argv)
    try:
        cfg = cfgmod.load(args.config)
        handler = globals()[f"cmd_{args.cmd}"]
        result = handler(cfg, args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return result or 0
    except KeyboardInterrupt:
        print("\nPrzerwano. Postęp synchronizacji jest zapisany.")
        return 130
    except Exception as e:
        from .ai import AiUnavailable
        from .connectors.telegram import LoginNeedsEmail, NotLoggedIn
        if isinstance(e, (cfgmod.ConfigError, NotLoggedIn, AiUnavailable, LoginNeedsEmail)):
            print(f"Błąd: {e}", file=sys.stderr)
            return 2
        if "database is locked" in str(e):
            print("Błąd: sesja Telegrama jest używana przez otwarte okno Szperacza. "
                  "Zamknij je albo wykonaj to w oknie.", file=sys.stderr)
            return 3
        raise
