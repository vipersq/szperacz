import asyncio
import sys
import threading
import time
import types as pytypes
from datetime import date

import pytest
from telethon.tl import types

from szperacz import browser
from szperacz.connectors.telegram import TelegramConnector


def test_resolve_link_routes():
    assert browser.resolve_link("https://t.me/bbcnews/5") == ("app", "https://t.me/bbcnews/5?embed=1&mode=tme")
    assert browser.resolve_link("https://t.me/c/123/9") == ("external", "tg://privatepost?channel=123&post=9")
    assert browser.resolve_link("rsgb.org") == ("app", "https://rsgb.org")
    assert browser.telegram_app_link("https://t.me/bbcnews/5") == "tg://resolve?domain=bbcnews&post=5"


@pytest.fixture
def fake_webview(monkeypatch):
    """Atrapa pywebview: okno zapisuje załadowane adresy."""
    loaded, started = [], threading.Event()

    class Win:
        on_top = False

        def load_url(self, u): loaded.append(u)
        def restore(self): pass
        def show(self): pass
        def run_js(self, js): pass
        def get_current_url(self): return loaded[-1] if loaded else None

    mod = pytypes.ModuleType("webview")
    mod.create_window = lambda title, url, **kw: (loaded.append(url), Win())[1]

    def start(func=None, **kw):
        t = threading.Thread(target=func, daemon=True)
        t.start()
        started.set()
        time.sleep(1.5)       # okno „otwarte” przez chwilę
    mod.start = start
    menu = pytypes.ModuleType("webview.menu")
    menu.Menu = lambda *a, **k: None
    menu.MenuAction = lambda *a, **k: None
    menu.MenuSeparator = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", mod)
    monkeypatch.setitem(sys.modules, "webview.menu", menu)
    monkeypatch.setattr(browser, "PORT", 47999)
    return loaded, started


def test_single_window_receives_next_links(tmp_path, fake_webview):
    loaded, started = fake_webview
    t = threading.Thread(target=browser.main, args=(["https://t.me/a/1?embed=1", "--dane", str(tmp_path)],),
                         daemon=True)
    t.start()
    started.wait(3)
    time.sleep(0.3)
    assert browser._send("https://rsgb.org", tmp_path)          # drugi link → to samo okno
    time.sleep(0.3)
    assert loaded == ["https://t.me/a/1?embed=1", "https://rsgb.org"]
    assert (tmp_path / "browser").is_dir()                      # trwałe logowania stron
    # ktoś bez klucza nie przejmie okna
    (tmp_path / "inny").mkdir()
    assert not browser._send("https://zly.example", tmp_path / "inny")
    time.sleep(0.2)
    assert "https://zly.example" not in loaded


def test_period_ranges():
    pytest.importorskip("tkinter")
    from szperacz.gui import period_range  # noqa: PLC0415 — wymaga tkintera
    t = date(2026, 11, 7)
    assert period_range("dziś", t) == ("2026-11-07", "2026-11-07")
    assert period_range("wczoraj", t) == ("2026-11-06", "2026-11-06")
    assert period_range("ostatnie 7 dni", t) == ("2026-11-01", "2026-11-07")
    assert period_range("poprzedni miesiąc", t) == ("2026-10-01", "2026-10-31")
    assert period_range("poprzedni rok", t) == ("2025-01-01", "2025-12-31")
    assert period_range("ostatnie 3 miesiące", date(2026, 2, 15)) == ("2025-11-15", "2026-02-15")
    assert period_range("dowolny", t) == (None, None)


class FakeMediaClient:
    def __init__(self, media):
        self.media = media
        self.downloads = []

    async def get_entity(self, peer):
        return types.PeerChannel(1)

    async def get_messages(self, entity, ids):
        return pytypes.SimpleNamespace(id=ids, message="opis", media=self.media)

    async def download_media(self, obj, file=None, thumb=None):
        self.downloads.append(thumb)
        open(file, "wb").write(b"jpg")
        return file


def test_fetch_media_photo_cached(tmp_path):
    c = TelegramConnector(1, "h", "/tmp/x")
    c.client = FakeMediaClient(types.MessageMediaPhoto())
    r = asyncio.run(c.fetch_media("-1001", 5, tmp_path))
    assert r["media"] == "photo" and r["path"].endswith("m1001_5.jpg")
    asyncio.run(c.fetch_media("-1001", 5, tmp_path))
    assert len(c.client.downloads) == 1          # drugi raz z dysku


def test_fetch_media_text_only(tmp_path):
    c = TelegramConnector(1, "h", "/tmp/x")
    c.client = FakeMediaClient(None)
    r = asyncio.run(c.fetch_media("-1001", 6, tmp_path))
    assert r["media"] is None and r["path"] is None and c.client.downloads == []
