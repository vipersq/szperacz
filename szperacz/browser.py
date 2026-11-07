"""Wbudowana przeglądarka Szperacza (pywebview → na Windows silnik Edge WebView2).

Działa w osobnym procesie (pywebview musi mieć własny główny wątek, a ten zajmuje
już okno Tkinter). Jeden egzemplarz: kolejne linki trafiają do otwartego okna przez
lokalne gniazdo 127.0.0.1 z kluczem z pliku dane/.browser_key.

Logowania na stronach (np. web.telegram.org) zostają zapamiętane w dane/browser/.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = 47811
HOME_URL = "https://web.telegram.org/k/"


# ---------------------------------------------------------------- linki
def resolve_link(url: str) -> tuple[str, str]:
    """Jak otworzyć link: ("app", url) — w przeglądarce Szperacza,
    ("external", url) — w programie systemowym (np. aplikacja Telegram dla tg://)."""
    url = (url or "").strip()
    if url.startswith("tg://"):
        return "external", url
    m = re.match(r"^https?://t\.me/c/(\d+)/(\d+)", url)
    if m:  # prywatna grupa/kanał — w sieci nie ma podglądu, otwiera aplikacja Telegram
        return "external", f"tg://privatepost?channel={m.group(1)}&post={m.group(2)}"
    m = re.match(r"^https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)/?$", url)
    if m:  # publiczny post — pełny podgląd ze zdjęciami
        return "app", f"https://t.me/{m.group(1)}/{m.group(2)}?embed=1&mode=tme"
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return "app", url


def telegram_app_link(url: str) -> str | None:
    """Link do otwarcia wiadomości w aplikacji Telegram (tg://)."""
    m = re.match(r"^https?://t\.me/c/(\d+)/(\d+)", url or "")
    if m:
        return f"tg://privatepost?channel={m.group(1)}&post={m.group(2)}"
    m = re.match(r"^https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)", url or "")
    if m:
        return f"tg://resolve?domain={m.group(1)}&post={m.group(2)}"
    return None


def _key(data_dir: Path) -> bytes:
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / ".browser_key"
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip().encode()


def _send(url: str, data_dir: Path) -> bool:
    from multiprocessing.connection import Client
    try:
        with Client(("127.0.0.1", PORT), authkey=_key(data_dir)) as c:
            c.send({"url": url})
        return True
    except (ConnectionRefusedError, OSError, EOFError):
        return False
    except Exception:  # noqa: BLE001 — np. zły klucz (inny program na porcie)
        return False


def _spawn(url: str, data_dir: Path) -> subprocess.Popen:
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--przegladarka", url, "--dane", str(data_dir)]
    else:
        cmd = [sys.executable, "-m", "szperacz.browser", url, "--dane", str(data_dir)]
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.Popen(cmd, cwd=os.getcwd(), **kw)


def open_url(url: str, data_dir: Path, on_fail=None) -> None:
    """Otwiera link w przeglądarce Szperacza (bez blokowania okna).
    on_fail(powód) — wołane, gdy wbudowana przeglądarka nie wystartowała (wtedy
    link otwiera się w zwykłej przeglądarce systemowej)."""
    how, target = resolve_link(url)
    if how == "external":
        _open_external(target)
        return

    def work():
        if _send(target, data_dir):
            return
        try:
            proc = _spawn(target, data_dir)
        except Exception as e:  # noqa: BLE001
            webbrowser.open(target)
            if on_fail:
                on_fail(str(e))
            return
        # pierwszy link przekazujemy w parametrze, czekamy tylko, czy proces nie padł
        for _ in range(40):
            time.sleep(0.25)
            code = proc.poll()
            if code is not None and code != 0:
                webbrowser.open(target)
                if on_fail:
                    on_fail(f"kod wyjścia {code}")
                return

    threading.Thread(target=work, daemon=True).start()


def _open_external(url: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            webbrowser.open(url)
    except OSError:
        webbrowser.open(url)


# -------------------------------------------------------- proces okna
def main(argv: list[str] | None = None) -> int:
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator

    argv = list(sys.argv[1:] if argv is None else argv)
    if "--przegladarka" in argv:
        argv.remove("--przegladarka")
    data_dir = Path("dane")
    if "--dane" in argv:
        i = argv.index("--dane")
        data_dir = Path(argv[i + 1])
        del argv[i:i + 2]
    start_url = argv[0] if argv else HOME_URL
    key = _key(data_dir)

    win = webview.create_window(
        "Szperacz — przeglądarka", start_url, width=1150, height=850,
        background_color="#14171c", text_select=True, zoomable=True,
        menu=[
            Menu("Nawigacja", [
                MenuAction("◀  Wstecz", lambda: win.run_js("history.back()")),
                MenuAction("▶  Dalej", lambda: win.run_js("history.forward()")),
                MenuAction("⟳  Odśwież", lambda: win.run_js("location.reload()")),
                MenuSeparator(),
                MenuAction("Wpisz adres…", lambda: _ask_address(win)),
                MenuAction("Telegram Web", lambda: win.load_url(HOME_URL)),
            ]),
            Menu("Strona", [
                MenuAction("Kopiuj adres", lambda: _copy(win.get_current_url() or "")),
                MenuAction("Otwórz w aplikacji Telegram",
                           lambda: _open_tg(win.get_current_url() or "")),
                MenuAction("Otwórz w zewnętrznej przeglądarce",
                           lambda: webbrowser.open(win.get_current_url() or start_url)),
            ]),
        ])

    def serve():
        from multiprocessing.connection import Listener
        try:
            listener = Listener(("127.0.0.1", PORT), authkey=key)
        except OSError:
            return  # port zajęty — działamy bez przyjmowania kolejnych linków
        while True:
            try:
                with listener.accept() as conn:
                    msg = conn.recv()
            except Exception:  # noqa: BLE001 — np. połączenie bez poprawnego klucza
                continue
            url = msg.get("url") if isinstance(msg, dict) else None
            if url:
                win.load_url(url)
                try:
                    win.restore()
                    win.show()
                    win.on_top = True
                    win.on_top = False
                except Exception:  # noqa: BLE001
                    pass

    storage = data_dir.resolve() / "browser"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(serve, private_mode=False, storage_path=str(storage))
    return 0


def _ask_address(win):
    url = win.evaluate_js("prompt('Adres strony:', location.href)")
    if url:
        how, target = resolve_link(url)
        if how == "external":
            _open_external(target)
        else:
            win.load_url(target)


def _open_tg(url: str):
    url = re.sub(r"\?embed=1.*$", "", url)
    tg = telegram_app_link(url)
    _open_external(tg or url)


def _copy(text: str):
    try:
        if sys.platform.startswith("win"):
            subprocess.run("clip", input=text.encode("utf-16-le"), check=False,
                           creationflags=0x08000000)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
