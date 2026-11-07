"""Okienko Szperacza (Tkinter — wbudowany w Pythona, działa też w exe)."""
from __future__ import annotations

import asyncio
import inspect
import queue
import re
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import config as cfgmod
from . import service
from .index import Index, _clean_term, fold, make_snippet
from . import browser
from .browser import telegram_app_link
from . import shield
from .localai import RECOMMENDED_VISION, LocalAI
from .models import MEDIA_LABELS, Hit, Query

# ------------------------------------------------------------------ kolory
BG = "#14171c"
PANEL = "#1b1f26"
FIELD = "#232832"
BORDER = "#2e3440"
FG = "#e4e7eb"
MUTED = "#8a93a3"
ACCENT = "#4fb3ff"
ACCENT_DIM = "#2b6c9c"
HL = "#f0b429"
ERR = "#ff6b6b"
OK = "#5fd38d"


UI_FONT_BIG = None   # ustawiane w App._setup_style

URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)[^\s<>«»\"']+")

PERIODS = ["dowolny", "dziś", "wczoraj", "ostatnie 7 dni", "ostatnie 30 dni", "ten miesiąc",
           "poprzedni miesiąc", "ostatnie 3 miesiące", "ten rok", "poprzedni rok"]
HOURS = ["dowolna", "rano (6–11)", "popołudnie (12–17)", "wieczór (18–21)", "noc (22–5)",
         "godziny pracy (8–17)", "własne"]
HOUR_RANGES = {"rano (6–11)": (6, 11), "popołudnie (12–17)": (12, 17), "wieczór (18–21)": (18, 21),
               "noc (22–5)": (22, 5), "godziny pracy (8–17)": (8, 17)}
CHAT_TYPES = [("prywatny", "prywatne"), ("grupa", "grupy"), ("kanał", "kanały"), ("bot", "boty")]
CONTENT_TYPES = [("text", "sam tekst"), ("photo", "📷 zdjęcia"), ("video", "🎬 wideo"),
                 ("document", "📎 pliki"), ("link", "🔗 linki"), ("audio", "🎵 audio / głosowe"),
                 ("poll", "📊 ankiety"), ("geo", "📍 lokalizacje")]
ORDERS = [("rank", "trafność"), ("date", "najnowsze"), ("oldest", "najstarsze")]


def period_range(name: str, today: date | None = None) -> tuple[str | None, str | None]:
    """Zakres dat (RRRR-MM-DD) dla nazwy okresu z listy PERIODS."""
    from datetime import timedelta
    t = today or date.today()
    first = t.replace(day=1)
    if name == "dziś":
        return t.isoformat(), t.isoformat()
    if name == "wczoraj":
        y = t - timedelta(days=1)
        return y.isoformat(), y.isoformat()
    if name == "ostatnie 7 dni":
        return (t - timedelta(days=6)).isoformat(), t.isoformat()
    if name == "ostatnie 30 dni":
        return (t - timedelta(days=29)).isoformat(), t.isoformat()
    if name == "ten miesiąc":
        return first.isoformat(), t.isoformat()
    if name == "poprzedni miesiąc":
        last = first - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    if name == "ostatnie 3 miesiące":
        m, y = t.month - 3, t.year
        if m <= 0:
            m, y = m + 12, y - 1
        return date(y, m, min(t.day, 28)).isoformat(), t.isoformat()
    if name == "ten rok":
        return date(t.year, 1, 1).isoformat(), t.isoformat()
    if name == "poprzedni rok":
        return date(t.year - 1, 1, 1).isoformat(), date(t.year - 1, 12, 31).isoformat()
    return None, None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def log_error(e: BaseException) -> None:
    """Zapisuje pełny błąd do szperacz.log (obok config.toml) — do diagnozy."""
    import traceback
    try:
        with open("szperacz.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    except Exception:  # noqa: BLE001
        pass


def log_line(msg: str) -> None:
    try:
        with open("szperacz.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except Exception:  # noqa: BLE001
        pass


class LoginCancelled(Exception):
    pass


# ---------------------------------------------------------- praca w tle
class Background:
    """Jeden wątek z pętlą asyncio (Telegram) + pula wątków (Claude, SQLite).
    Wyniki wracają do wątku okna przez kolejkę."""

    def __init__(self, root: tk.Tk, on_busy):
        self.root = root
        self.on_busy = on_busy
        self.busy = 0
        self.q: queue.Queue = queue.Queue()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        root.after(80, self._poll)

    def run(self, fn, on_done=None, on_error=None):
        async def wrap():
            try:
                if inspect.iscoroutinefunction(fn):
                    r = await fn()
                else:
                    r = await self.loop.run_in_executor(None, fn)
                self.q.put(("done", on_done, r))
            except BaseException as e:  # noqa: BLE001
                self.q.put(("err", on_error, e))

        self.busy += 1
        self.on_busy(True)
        asyncio.run_coroutine_threadsafe(wrap(), self.loop)

    def call_ui(self, fn, *args):
        """Z wątku w tle: wykonaj fn w wątku okna (bez czekania)."""
        self.q.put(("ui", fn, args))

    def ask_ui(self, fn):
        """Z wątku w tle: wykonaj fn w wątku okna i poczekaj na wynik."""
        ev, box = threading.Event(), {}

        def job():
            try:
                box["v"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
            finally:
                ev.set()

        self.q.put(("ui", job, ()))
        ev.wait()
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def _poll(self):
        try:
            while True:
                kind, cb, val = self.q.get_nowait()
                try:
                    if kind == "ui":
                        cb(*val)
                        continue
                    self.busy -= 1
                    if self.busy <= 0:
                        self.busy = 0
                        self.on_busy(False)
                    if kind == "done" and cb:
                        cb(val)
                    elif kind == "err":
                        log_error(val)
                        (cb or show_error)(val)
                except Exception as e:  # noqa: BLE001 — błąd w oknie nie może zatrzymać pętli
                    log_error(e)
                    show_error(e)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


def friendly_error(e: BaseException) -> str:
    from .ai import AiUnavailable
    from .connectors.telegram import LoginNeedsEmail, NotLoggedIn

    if isinstance(e, (cfgmod.ConfigError, NotLoggedIn, AiUnavailable, LoginNeedsEmail)):
        return str(e)
    if isinstance(e, LoginCancelled):
        return "Logowanie przerwane."
    name = type(e).__name__
    if name == "AuthenticationError":
        return "Claude API odrzuciło klucz. Sprawdź klucz w Ustawieniach."
    if name in ("PhoneCodeInvalidError", "PhoneCodeExpiredError"):
        return "Nieprawidłowy lub wygasły kod z Telegrama. Spróbuj zalogować się ponownie."
    if name == "FloodWaitError":
        sec = getattr(e, "seconds", 0)
        return (f"Telegram każe odczekać {sec // 60} min {sec % 60} s przed kolejną próbą "
                f"(zbyt wiele prób logowania/kodów pod rząd).")
    if name == "PhoneNumberInvalidError":
        return "Telegram nie rozpoznał numeru telefonu. Wpisz go z kodem kraju, np. +447700900123 (Ustawienia)."
    if name == "PhoneNumberBannedError":
        return "Telegram zablokował ten numer."
    if name == "SessionPasswordNeededError":
        return "Konto ma hasło dwuetapowe (2FA) — spróbuj zalogować się ponownie i podaj hasło."
    if name == "PasswordHashInvalidError":
        return "Nieprawidłowe hasło dwuetapowe (2FA)."
    if name == "ApiIdInvalidError":
        return "Telegram odrzucił api_id / api_hash. Sprawdź je w Ustawieniach."
    if "database is locked" in str(e):
        return ("Sesja Telegrama jest używana przez inny otwarty Szperacz "
                "(drugie okno, Szperacz.exe albo synchronizacja w konsoli). Zamknij go i spróbuj ponownie.")
    if isinstance(e, (ConnectionError, OSError)):
        return f"Brak połączenia: {e}"
    return f"{name}: {e}"


def show_error(e: BaseException):
    messagebox.showerror("Szperacz", friendly_error(e))


def local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def term_spans(text: str, groups: list[list[str]]) -> list[tuple[int, int]]:
    terms = [c for g in groups for x in g if (c := _clean_term(x))]
    if not terms:
        return []
    pats = [rf"(?<!\w){re.escape(fold(t))}\w*" if p else rf"(?<!\w){re.escape(fold(t))}(?!\w)"
            for t, p in terms]
    return [(m.start(), m.end()) for m in re.finditer("|".join(pats), fold(text))]


# ----------------------------------------------------------------- okienka
class Dialog(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=18)
        self.body.pack(fill="both", expand=True)

    def center(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()


def ask_string(parent, title, prompt, secret=False) -> str | None:
    d = Dialog(parent, title)
    ttk.Label(d.body, text=prompt, wraplength=360, justify="left").pack(anchor="w")
    var = tk.StringVar()
    e = ttk.Entry(d.body, textvariable=var, width=36, show="•" if secret else "", font=UI_FONT_BIG)
    e.pack(fill="x", pady=(10, 14))
    out = {"v": None}

    def ok(*_):
        out["v"] = var.get().strip()
        d.destroy()

    row = ttk.Frame(d.body)
    row.pack(fill="x")
    ttk.Button(row, text="Anuluj", command=d.destroy).pack(side="right")
    ttk.Button(row, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=(0, 8))
    e.bind("<Return>", ok)
    d.bind("<Escape>", lambda *_: d.destroy())
    d.center()
    e.focus_set()
    parent.wait_window(d)
    return out["v"] or None


def ask_code(parent, info: str) -> str | None:
    """Okienko na kod logowania: opis, gdzie go szukać + „wyślij inaczej”."""
    from .connectors.telegram import RESEND
    d = Dialog(parent, "Kod z Telegrama")
    ttk.Label(d.body, text="KOD LOGOWANIA", style="Section.TLabel").pack(anchor="w")
    ttk.Label(d.body, text=info, wraplength=440, justify="left").pack(anchor="w", pady=(6, 10))
    var = tk.StringVar()
    e = ttk.Entry(d.body, textvariable=var, width=14, font=(UI_FONT_BIG[0], 18) if UI_FONT_BIG else None,
                  justify="center")
    e.pack(anchor="w", pady=(0, 14))
    out = {"v": None}

    def ok(*_):
        out["v"] = var.get().strip() or None
        d.destroy()

    def resend():
        out["v"] = RESEND
        d.destroy()

    row = ttk.Frame(d.body)
    row.pack(fill="x")
    ttk.Button(row, text="Nie przyszło – wyślij inaczej", command=resend).pack(side="left")
    ttk.Button(row, text="Anuluj", command=d.destroy).pack(side="right")
    ttk.Button(row, text="Zaloguj", style="Accent.TButton", command=ok).pack(side="right", padx=(0, 8))
    e.bind("<Return>", ok)
    d.bind("<Escape>", lambda *_: d.destroy())
    d.center()
    e.focus_set()
    parent.wait_window(d)
    return out["v"]


class QrDialog(Dialog):
    """Okno logowania kodem QR. Rysuje kod na Canvasie (bez dodatkowych bibliotek graficznych)."""

    def __init__(self, app):
        super().__init__(app.root, "Logowanie do Telegrama")
        self.app = app
        self.cancel = False
        self.choice = None
        b = self.body
        ttk.Label(b, text="ZESKANUJ TELEFONEM", style="Section.TLabel").pack(anchor="w")
        ttk.Label(b, wraplength=400, justify="left", text=(
            "1. Otwórz Telegram na telefonie\n"
            "2. Ustawienia → Urządzenia → Połącz urządzenie (Link Desktop Device)\n"
            "3. Skieruj aparat na kod poniżej")).pack(anchor="w", pady=(6, 12))
        self.canvas = tk.Canvas(b, width=300, height=300, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(pady=(0, 8))
        self.status = ttk.Label(b, text="Łączę z Telegramem…", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(b)
        row.pack(fill="x")
        ttk.Button(row, text="Wolę zalogować się kodem", command=self.use_code).pack(side="left")
        ttk.Button(row, text="Anuluj", command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.center()

    def set_qr(self, url, expires):
        if not self.winfo_exists():
            return
        try:
            import qrcode
        except ImportError:
            self.status.configure(text="Brak biblioteki qrcode — uruchom Szperacz.bat (doinstaluje ją).")
            return
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(url)
        q.make(fit=True)
        m = q.get_matrix()
        c = self.canvas
        c.delete("all")
        size = 300
        cell = size / len(m)
        for y, row in enumerate(m):
            for x, v in enumerate(row):
                if v:
                    c.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell,
                                       fill="#000000", outline="#000000")
        try:
            left = int((expires - datetime.now(expires.tzinfo)).total_seconds())
            self.status.configure(text=f"Kod odświeża się automatycznie (ważny ok. {left} s). Czekam na skan…")
        except Exception:  # noqa: BLE001
            self.status.configure(text="Czekam na skan…")

    def set_status(self, text):
        if self.winfo_exists():
            self.status.configure(text=text)

    def use_code(self):
        self.choice = "code"
        self.close()

    def close(self):
        self.cancel = True
        if self.winfo_exists():
            self.destroy()


class SettingsDialog(Dialog):
    def __init__(self, app, first_run=False):
        super().__init__(app.root, "Ustawienia")
        self.app = app
        cfg = app.cfg
        b = self.body

        if first_run:
            ttk.Label(b, text="Witaj w Szperaczu!", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(b, text="Na start potrzebne są klucze Telegrama. Zajmie to dwie minuty.",
                      style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))

        ttk.Label(b, text="TELEGRAM", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 4))
        link = ttk.Label(b, text="→ wygeneruj na my.telegram.org (API development tools)",
                         style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="e")
        link.bind("<Button-1>", lambda *_: webbrowser.open("https://my.telegram.org/apps"))

        self.v = {
            "api_id": tk.StringVar(value=str(cfg.telegram.api_id or "")),
            "api_hash": tk.StringVar(value=cfg.telegram.api_hash or ""),
            "telefon": tk.StringVar(value=cfg.telegram.phone or ""),
            "pomin_czaty": tk.StringVar(value=", ".join(cfg.telegram.exclude)),
            "obserwowane": tk.StringVar(value=", ".join(cfg.telegram.watched)),
            "limit": tk.StringVar(value=str(cfg.telegram.first_sync_limit)),
            "klucz": tk.StringVar(value=_file_api_key(app.cfg_path)),
        }
        self.skip_channels = tk.BooleanVar(value=cfg.telegram.skip_channels)

        rows = [
            ("api_id", "api_id", {}),
            ("api_hash", "api_hash", {}),
            ("Telefon (opcjonalnie)", "telefon", {}),
            ("Pomiń czaty (po przecinku)", "pomin_czaty", {}),
            ("Obserwowane publiczne kanały (@nazwa, po przecinku)", "obserwowane", {}),
            ("Pierwsza synchronizacja: N najnowszych (0 = wszystko)", "limit", {"width": 10}),
        ]
        r = 3
        for label, key, kw in rows:
            ttk.Label(b, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 14))
            ttk.Entry(b, textvariable=self.v[key], width=kw.get("width", 44)).grid(row=r, column=1, sticky="we" if "width" not in kw else "w", pady=3)
            r += 1
        ttk.Checkbutton(b, text="Pomiń kanały informacyjne (broadcast)", variable=self.skip_channels).grid(
            row=r, column=1, sticky="w", pady=(2, 10))
        r += 1

        ttk.Label(b, text="CLAUDE (pytania po ludzku)", style="Section.TLabel").grid(row=r, column=0, sticky="w", pady=(8, 4))
        link2 = ttk.Label(b, text="→ klucz na console.anthropic.com", style="Link.TLabel", cursor="hand2")
        link2.grid(row=r, column=1, sticky="e")
        link2.bind("<Button-1>", lambda *_: webbrowser.open("https://console.anthropic.com/settings/keys"))
        r += 1
        ttk.Label(b, text="Klucz API").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v["klucz"], width=44, show="•").grid(row=r, column=1, sticky="we", pady=3)
        r += 1
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            ttk.Label(b, text="Uwaga: ustawiona jest zmienna ANTHROPIC_API_KEY — ma pierwszeństwo.",
                      style="Muted.TLabel").grid(row=r, column=1, sticky="w")
            r += 1
        ttk.Label(b, text="Klucz zapisuje się w config.toml na tym komputerze. Bez klucza działa szukanie słów.",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 12))
        r += 1

        la, st = cfg.local_ai, (app.lai_status or {})
        ttk.Label(b, text="LOKALNE AI (Ollama / LM Studio)", style="Section.TLabel").grid(row=r, column=0, sticky="w", pady=(4, 4))
        info = (f"wykryto: {st.get('backend')}, {len(st.get('models') or [])} modeli" if st.get("backend")
                else (st.get("error") or "sprawdzam…"))
        ttk.Label(b, text=info, style="Muted.TLabel").grid(row=r, column=1, sticky="e")
        r += 1
        self.la_enabled = tk.BooleanVar(value=la.enabled)
        self.la_translate = tk.BooleanVar(value=la.auto_translate)
        self.la_images = tk.BooleanVar(value=la.check_images)
        self.la_shield = tk.BooleanVar(value=cfg.filters.shield)
        self.la_hidden = tk.StringVar(value=la.hidden_mode)
        self.v["la_url"] = tk.StringVar(value=la.ollama_url)
        self.v["la_text"] = tk.StringVar(value=la.text_model)
        self.v["la_vision"] = tk.StringVar(value=la.vision_model)
        models = ["auto"] + list(st.get("models") or [])
        ttk.Checkbutton(b, text="Włącz lokalne AI (ocena wyników, tłumaczenie, sprawdzanie zdjęć)",
                        variable=self.la_enabled).grid(row=r, column=0, columnspan=2, sticky="w", pady=3)
        r += 1
        ttk.Label(b, text="Adres Ollamy").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(b, textvariable=self.v["la_url"], width=44).grid(row=r, column=1, sticky="we", pady=3)
        r += 1
        ttk.Label(b, text=f"Model tekstu (auto: {st.get('text') or '—'})").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(b, textvariable=self.v["la_text"], values=models, width=42).grid(row=r, column=1, sticky="we", pady=3)
        r += 1
        ttk.Label(b, text=f"Model obrazu (auto: {st.get('vision') or '—'})").grid(row=r, column=0, sticky="w", pady=3)
        vrow = ttk.Frame(b)
        vrow.grid(row=r, column=1, sticky="we", pady=3)
        ttk.Combobox(vrow, textvariable=self.v["la_vision"], values=models, width=26).pack(side="left", fill="x", expand=True)
        ttk.Button(vrow, text=f"Pobierz {RECOMMENDED_VISION}",
                   command=lambda: (self.destroy(), app.pull_model(RECOMMENDED_VISION))).pack(side="left", padx=(6, 0))
        r += 1
        opt = ttk.Frame(b)
        opt.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 2))
        ttk.Checkbutton(opt, text="Tłumacz automatycznie na polski", variable=self.la_translate).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opt, text="Sprawdzaj zdjęcia z publicznych kanałów", variable=self.la_images).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opt, text="Tarcza antyspamowa", variable=self.la_shield).pack(side="left")
        r += 1
        hrow = ttk.Frame(b)
        hrow.grid(row=r, column=0, columnspan=2, sticky="w", pady=(2, 12))
        ttk.Label(hrow, text="Spam i 18+:").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(hrow, text="zwiń na dole listy", value="zwin", variable=self.la_hidden).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(hrow, text="nie pokazuj wcale", value="usun", variable=self.la_hidden).pack(side="left")
        r += 1

        row = ttk.Frame(b)
        row.grid(row=r, column=0, columnspan=2, sticky="e")
        ttk.Button(row, text="Otwórz config.toml", command=lambda: _open_path(app.cfg_path)).pack(side="left", padx=(0, 20))
        ttk.Button(row, text="Anuluj", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Zapisz", style="Accent.TButton", command=self.save).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda *_: self.destroy())
        self.center()

    def save(self):
        api_id = self.v["api_id"].get().strip()
        limit = self.v["limit"].get().strip() or "0"
        if api_id and not api_id.isdigit():
            messagebox.showerror("Ustawienia", "api_id to sama liczba.", parent=self)
            return
        if not limit.isdigit():
            messagebox.showerror("Ustawienia", "Limit musi być liczbą (0 = wszystko).", parent=self)
            return
        excl = [x.strip() for x in self.v["pomin_czaty"].get().split(",") if x.strip()]
        watched = [x.strip() for x in self.v["obserwowane"].get().split(",") if x.strip()]
        try:
            cfgmod.update_values(self.app.cfg_path, {
                "telegram": {
                    "api_id": int(api_id) if api_id else 0,
                    "api_hash": self.v["api_hash"].get().strip(),
                    "telefon": self.v["telefon"].get().strip(),
                    "pomin_czaty": excl,
                    "obserwowane": watched,
                    "pomin_kanaly": self.skip_channels.get(),
                    "limit_pierwszej_synchronizacji": int(limit),
                },
                "ai": {"klucz_api": self.v["klucz"].get().strip()},
                "lokalne_ai": {
                    "wlaczone": self.la_enabled.get(),
                    "adres_ollama": self.v["la_url"].get().strip() or "http://127.0.0.1:11434",
                    "model_tekst": self.v["la_text"].get().strip() or "auto",
                    "model_obraz": self.v["la_vision"].get().strip() or "auto",
                    "tlumacz_automatycznie": self.la_translate.get(),
                    "sprawdzaj_zdjecia": self.la_images.get(),
                    "ukryte": self.la_hidden.get(),
                },
                "filtr": {"tarcza": self.la_shield.get()},
            })
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ustawienia", f"Nie udało się zapisać: {e}", parent=self)
            return
        self.app.reload_config()
        self.destroy()


def _file_api_key(path: Path) -> str:
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f).get("ai", {}).get("klucz_api", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _open_path(path):
    import os
    import subprocess
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


class LogWindow(Dialog):
    def __init__(self, app, title):
        super().__init__(app.root, title)
        self.resizable(True, True)
        self.text = tk.Text(self.body, width=80, height=18, bg=FIELD, fg=FG, relief="flat",
                            font=app._font_mono, insertbackground=FG, padx=10, pady=8)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("err", foreground=ERR)
        self.text.tag_configure("ok", foreground=OK)
        self.bar = ttk.Progressbar(self.body, mode="indeterminate")
        self.bar.pack(fill="x", pady=(10, 8))
        self.bar.start(12)
        self.btn = ttk.Button(self.body, text="Pracuję…", state="disabled", command=self.destroy)
        self.btn.pack(anchor="e")
        self.update_idletasks()
        self.geometry(f"+{app.root.winfo_rootx() + 80}+{app.root.winfo_rooty() + 80}")

    def append(self, line: str):
        if not self.winfo_exists():
            return
        tag = "err" if "BŁĄD" in line else ""
        self.text.insert("end", line + "\n", tag)
        self.text.see("end")

    def finish(self, line: str, ok=True):
        if not self.winfo_exists():
            return
        self.bar.stop()
        self.bar.pack_forget()
        self.text.insert("end", "\n" + line + "\n", "ok" if ok else "err")
        self.text.see("end")
        self.btn.configure(text="Zamknij", state="normal")


class ChatsWindow(Dialog):
    def __init__(self, app):
        super().__init__(app.root, "Czaty Telegrama")
        self.app = app
        self.resizable(True, True)
        ttk.Label(self.body, text="Zaznacz czaty (Ctrl/Shift + klik) i zsynchronizuj tylko je. "
                                  "✓ = objęty zwykłą synchronizacją wg ustawień.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("ok", "type", "name"), show="headings", height=18)
        for col, title, w, anchor in (("ok", "✓", 40, "center"), ("type", "Typ", 90, "w"), ("name", "Nazwa", 420, "w")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "name"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(10, 0))
        self.info = ttk.Label(row, text="Pobieram listę czatów…", style="Muted.TLabel")
        self.info.pack(side="left")
        ttk.Button(row, text="Zamknij", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Synchronizuj zaznaczone", style="Accent.TButton",
                   command=self.sync_selected).pack(side="right", padx=(0, 8))
        self.update_idletasks()
        self.geometry(f"+{app.root.winfo_rootx() + 60}+{app.root.winfo_rooty() + 60}")
        app.bg.run(app.fetch_chats, self.fill)

    def fill(self, chats):
        if not self.winfo_exists():
            return
        from .sync import ChatFilter
        t = self.app.cfg.telegram
        flt = ChatFilter(t.include, t.exclude, t.skip_channels)
        for c in chats:
            self.tree.insert("", "end", iid=c.id, values=("✓" if flt.allows(c) else "",
                                                          c.type + (" (obserw.)" if c.watched else ""), c.name))
        self.info.configure(text=f"{len(chats)} czatów")

    def sync_selected(self):
        ids = list(self.tree.selection())
        if not ids:
            messagebox.showinfo("Czaty", "Najpierw zaznacz czaty na liście.", parent=self)
            return
        self.destroy()
        self.app.start_sync(only=ids)


# ------------------------------------------------------------- główne okno
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg_path = ensure_config_quiet()
        self.cfg = cfgmod.load(str(self.cfg_path))
        self.index = Index(self.cfg.index_path)
        self.lai = LocalAI(self.cfg.local_ai, self.cfg.data_dir / "ai_cache.sqlite")
        self.lai_status = None
        self.all_hits: list[Hit] = []
        self.show_hidden = False
        self._revealed: set = set()
        self._ai_run = 0
        self.hits: list[Hit] = []
        self.last_groups: list[list[str]] = []
        self.answer_visible = False

        self._setup_style()
        self.bg = Background(root, self._set_busy)
        self._build()
        self.refresh_meta()
        if not self.cfg.telegram.api_id:
            root.after(400, lambda: SettingsDialog(self, first_run=True))

    # --------------------------------------------------------------- styl
    def _setup_style(self):
        r = self.root
        fams = set(tkfont.families(r))
        base = next((f for f in ("Segoe UI", "Inter", "Ubuntu", "DejaVu Sans") if f in fams), "TkDefaultFont")
        mono = next((f for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono") if f in fams), "TkFixedFont")
        self._font = (base, 10)
        self._font_big = (base, 12)
        global UI_FONT_BIG
        UI_FONT_BIG = self._font_big
        self._font_title = (base, 15, "bold")
        self._font_mono = (mono, 10)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=base, size=10)
            except tk.TclError:
                pass

        r.configure(bg=BG)
        r.option_add("*Background", BG)
        r.option_add("*Foreground", FG)
        r.option_add("*TCombobox*Listbox.background", FIELD)
        r.option_add("*TCombobox*Listbox.foreground", FG)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)

        s = ttk.Style(r)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=PANEL, focuscolor=ACCENT,
                    selectbackground=ACCENT_DIM, selectforeground=FG, insertcolor=FG, font=self._font)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("Title.TLabel", font=self._font_title)
        s.configure("Logo.TLabel", font=self._font_title, foreground=ACCENT)
        s.configure("Section.TLabel", foreground=ACCENT, font=(self._font[0], 9, "bold"))
        s.configure("Link.TLabel", foreground=ACCENT)
        s.configure("TButton", background=FIELD, foreground=FG, padding=(12, 6), borderwidth=1)
        s.map("TButton", background=[("active", BORDER), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT_DIM, foreground="#ffffff")
        s.map("Accent.TButton", background=[("active", ACCENT), ("disabled", PANEL)])
        s.configure("TEntry", padding=6)
        s.configure("Big.TEntry", padding=8)
        s.configure("TCombobox", padding=5, arrowcolor=FG)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
        s.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=FIELD)
        s.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", ACCENT_DIM)])
        s.configure("Panel.TCheckbutton", background=PANEL, foreground=FG, indicatorbackground=FIELD)
        s.map("Panel.TCheckbutton", background=[("active", PANEL)], indicatorbackground=[("selected", ACCENT_DIM)])
        s.configure("Panel.TRadiobutton", background=PANEL, foreground=FG, indicatorbackground=FIELD)
        s.map("Panel.TRadiobutton", background=[("active", PANEL)], indicatorbackground=[("selected", ACCENT_DIM)])
        s.configure("TSpinbox", arrowcolor=FG, padding=3)
        s.configure("Mode.TRadiobutton", background=FIELD, foreground=MUTED, padding=(12, 7),
                    indicatorsize=0, borderwidth=1)
        s.map("Mode.TRadiobutton", background=[("selected", ACCENT_DIM), ("active", BORDER)],
              foreground=[("selected", "#ffffff"), ("active", FG)])
        s.layout("Mode.TRadiobutton", [("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Radiobutton.label", {"sticky": "nswe"})]})])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=26, borderwidth=0)
        s.map("Treeview", background=[("selected", ACCENT_DIM)], foreground=[("selected", "#ffffff")])
        s.configure("Treeview.Heading", background=FIELD, foreground=MUTED, relief="flat", padding=(8, 5))
        s.map("Treeview.Heading", background=[("active", BORDER)])
        s.configure("TPanedwindow", background=BG)
        s.configure("Sash", sashthickness=6, background=BG)
        s.configure("TProgressbar", background=ACCENT, troughcolor=PANEL, borderwidth=0, thickness=4)
        s.configure("Vertical.TScrollbar", background=FIELD, troughcolor=PANEL, arrowcolor=MUTED, borderwidth=0)

    # -------------------------------------------------------------- układ
    def _build(self):
        r = self.root
        r.title("Szperacz")
        r.geometry("1240x780")
        r.minsize(900, 560)

        top = ttk.Frame(r, padding=(18, 14, 18, 8))
        top.pack(fill="x")

        head = ttk.Frame(top)
        head.pack(fill="x")
        ttk.Label(head, text="◉ SZPERACZ", style="Logo.TLabel").pack(side="left")
        ttk.Label(head, text="  wszystkie Twoje rozmowy w jednym miejscu", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        for text, cmd in (("Ustawienia", self.open_settings),
                          ("🌐 Przeglądarka", lambda: browser.open_url(browser.HOME_URL, self.cfg.data_dir, self._browser_failed)),
                          ("Czaty…", self.open_chats),
                          ("⟳ Synchronizuj", self.start_sync), ("Zaloguj", self.login)):
            ttk.Button(head, text=text, command=cmd).pack(side="right", padx=(6, 0))

        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(14, 0))
        self.mode = tk.StringVar(value="words")
        modes = ttk.Frame(bar)
        modes.pack(side="left", padx=(0, 10))
        ttk.Radiobutton(modes, text="Słowa", value="words", variable=self.mode,
                        style="Mode.TRadiobutton", command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(modes, text="✦ Zapytaj AI", value="ai", variable=self.mode,
                        style="Mode.TRadiobutton", command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(modes, text="🌐 Cały Telegram", value="global", variable=self.mode,
                        style="Mode.TRadiobutton", command=self._mode_changed).pack(side="left")
        self.qvar = tk.StringVar()
        self.qentry = ttk.Entry(bar, textvariable=self.qvar, style="Big.TEntry", font=self._font_big)
        self.qentry.pack(side="left", fill="x", expand=True)
        self.qentry.bind("<Return>", lambda *_: self.do_search())
        self.search_btn = ttk.Button(bar, text="Szukaj", style="Accent.TButton", command=self.do_search)
        self.search_btn.pack(side="left", padx=(10, 0), ipady=3)

        self.hint = ttk.Label(top, style="Muted.TLabel")
        self.hint.pack(anchor="w", pady=(6, 0))

        flt = ttk.Frame(top)
        flt.pack(fill="x", pady=(10, 0))
        self.chat_var, self.sender_var = tk.StringVar(), tk.StringVar()
        self.from_var, self.to_var = tk.StringVar(), tk.StringVar()
        ttk.Label(flt, text="Czat").pack(side="left")
        self.chat_cb = ttk.Combobox(flt, textvariable=self.chat_var, width=24)
        self.chat_cb.pack(side="left", padx=(6, 12))
        ttk.Label(flt, text="Nadawca").pack(side="left")
        self.sender_cb = ttk.Combobox(flt, textvariable=self.sender_var, width=18)
        self.sender_cb.pack(side="left", padx=(6, 12))
        ttk.Label(flt, text="Okres").pack(side="left")
        self.period_var = tk.StringVar(value=PERIODS[0])
        per = ttk.Combobox(flt, textvariable=self.period_var, values=PERIODS, width=16, state="readonly")
        per.pack(side="left", padx=(6, 8))
        per.bind("<<ComboboxSelected>>", lambda *_: self._period_changed())
        ttk.Label(flt, text="od").pack(side="left")
        ttk.Entry(flt, textvariable=self.from_var, width=11).pack(side="left", padx=(4, 6))
        ttk.Label(flt, text="do").pack(side="left")
        ttk.Entry(flt, textvariable=self.to_var, width=11).pack(side="left", padx=(4, 12))
        self.more_btn = ttk.Button(flt, text="Filtry ▾", width=11, command=self._toggle_more)
        self.more_btn.pack(side="right")
        ttk.Button(flt, text="Wyczyść", command=self.clear_filters).pack(side="right", padx=(0, 6))
        self.flt_frame = flt

        # panel „więcej filtrów”
        more = ttk.Frame(top, style="Panel.TFrame", padding=(14, 10))
        self.more_frame, self.more_visible = more, False
        self.hour_var = tk.StringVar(value=HOURS[0])
        self.hf_var, self.ht_var = tk.StringVar(value="0"), tk.StringVar(value="23")
        self.type_vars = {k: tk.BooleanVar() for k, _ in CHAT_TYPES}
        self.content_vars = {k: tk.BooleanVar() for k, _ in CONTENT_TYPES}
        self.dir_var = tk.StringVar(value="")
        self.order_var = tk.StringVar(value=ORDERS[0][1])
        self.limit_var = tk.StringVar(value="300")

        def lab(row, col, text):
            ttk.Label(more, text=text, style="PanelMuted.TLabel").grid(row=row, column=col, sticky="w", padx=(0, 8), pady=3)

        lab(0, 0, "Pora dnia")
        hrow = ttk.Frame(more, style="Panel.TFrame")
        hrow.grid(row=0, column=1, sticky="w", pady=3)
        hc = ttk.Combobox(hrow, textvariable=self.hour_var, values=HOURS, width=20, state="readonly")
        hc.pack(side="left")
        hc.bind("<<ComboboxSelected>>", lambda *_: self._hours_changed())
        ttk.Label(hrow, text="  godz.", style="PanelMuted.TLabel").pack(side="left")
        ttk.Spinbox(hrow, from_=0, to=23, textvariable=self.hf_var, width=4, command=self._hours_custom).pack(side="left", padx=4)
        ttk.Label(hrow, text="–", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(hrow, from_=0, to=23, textvariable=self.ht_var, width=4, command=self._hours_custom).pack(side="left", padx=4)

        lab(0, 2, "Kto")
        drow = ttk.Frame(more, style="Panel.TFrame")
        drow.grid(row=0, column=3, sticky="w", pady=3)
        for val, txt in (("", "wszyscy"), ("out", "moje"), ("in", "od innych")):
            ttk.Radiobutton(drow, text=txt, value=val, variable=self.dir_var, style="Panel.TRadiobutton").pack(side="left", padx=(0, 10))

        lab(1, 0, "Rodzaj czatu")
        trow = ttk.Frame(more, style="Panel.TFrame")
        trow.grid(row=1, column=1, sticky="w", pady=3)
        for k, txt in CHAT_TYPES:
            ttk.Checkbutton(trow, text=txt, variable=self.type_vars[k], style="Panel.TCheckbutton").pack(side="left", padx=(0, 10))

        lab(1, 2, "Sortuj")
        orow = ttk.Frame(more, style="Panel.TFrame")
        orow.grid(row=1, column=3, sticky="w", pady=3)
        ttk.Combobox(orow, textvariable=self.order_var, values=[o[1] for o in ORDERS], width=12,
                     state="readonly").pack(side="left")
        ttk.Label(orow, text="   limit", style="PanelMuted.TLabel").pack(side="left")
        ttk.Spinbox(orow, from_=20, to=2000, increment=50, textvariable=self.limit_var, width=6).pack(side="left", padx=6)

        lab(2, 0, "Zawartość")
        crow = ttk.Frame(more, style="Panel.TFrame")
        crow.grid(row=2, column=1, columnspan=3, sticky="w", pady=3)
        for k, txt in CONTENT_TYPES:
            ttk.Checkbutton(crow, text=txt, variable=self.content_vars[k], style="Panel.TCheckbutton").pack(side="left", padx=(0, 10))
        more.columnconfigure(1, weight=1)
        # opcje trybu „Cały Telegram” (zamiast filtrów lokalnych)
        g = ttk.Frame(top)
        self.g_posts, self.g_mine = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.g_disc, self.g_ai = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        ttk.Label(g, text="Szukaj w:").pack(side="left", padx=(0, 8))
        ttk.Checkbutton(g, text="wszystkich publicznych kanałach", variable=self.g_posts).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(g, text="publicznych grupach/kanałach znalezionych po nazwie", variable=self.g_disc).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(g, text="moich czatach", variable=self.g_mine).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(g, text="✦ AI dobiera frazę i streszcza", variable=self.g_ai).pack(side="left")
        self.g_frame = g
        for cb in (self.chat_cb, self.sender_cb):
            cb.bind("<Return>", lambda *_: self.do_search())

        # część środkowa
        self.vpane = ttk.PanedWindow(r, orient="vertical")
        self.vpane.pack(fill="both", expand=True, padx=18, pady=(8, 0))

        self.answer_frame = ttk.Frame(self.vpane, style="Panel.TFrame", padding=(14, 10))
        ttk.Label(self.answer_frame, text="✦ ODPOWIEDŹ CLAUDE", style="Section.TLabel",
                  background=PANEL).pack(anchor="w")
        self.answer = tk.Text(self.answer_frame, height=7, wrap="word", bg=PANEL, fg=FG, relief="flat",
                              font=self._font_big, padx=2, pady=6, cursor="arrow",
                              highlightthickness=0, borderwidth=0)
        self.answer.pack(fill="both", expand=True)
        self.answer.tag_configure("ref", foreground=ACCENT, underline=True)
        self.answer.tag_configure("muted", foreground=MUTED, font=self._font)
        self.answer.tag_bind("ref", "<Enter>", lambda *_: self.answer.configure(cursor="hand2"))
        self.answer.tag_bind("ref", "<Leave>", lambda *_: self.answer.configure(cursor="arrow"))
        self.answer.tag_bind("ref", "<Button-1>", self._on_ref_click)
        self.answer.configure(state="disabled")

        self.hpane = ttk.PanedWindow(self.vpane, orient="horizontal")
        self.vpane.add(self.hpane, weight=3)

        left = ttk.Frame(self.hpane, style="Panel.TFrame")
        self.tree = ttk.Treeview(left, columns=("n", "date", "chat", "sender", "snip"),
                                 show="headings", selectmode="browse")
        for col, title, w, st in (("n", "#", 38, False), ("date", "Data", 135, False),
                                  ("chat", "Czat", 150, False), ("sender", "Nadawca", 130, False),
                                  ("snip", "Fragment", 380, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=w, minwidth=36, stretch=st, anchor="w")
        self.tree.tag_configure("odd", background="#1f242c")
        self.tree.tag_configure("hiddenrow", foreground="#6b7280", background="#191c22")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda *_: self._open_selected_link())
        self.hpane.add(left, weight=3)

        right = ttk.Frame(self.hpane, style="Panel.TFrame", padding=(16, 12))
        self.detail = tk.Text(right, wrap="word", bg=PANEL, fg=FG, relief="flat", font=self._font_big,
                              padx=2, pady=2, highlightthickness=0, borderwidth=0, cursor="arrow",
                              spacing1=2, spacing3=2)
        self.detail.pack(fill="both", expand=True)
        self.detail.tag_configure("meta", foreground=MUTED, font=self._font)
        self.detail.tag_configure("who", foreground=FG, font=(self._font[0], 12, "bold"))
        self.detail.tag_configure("hl", background="#4a3b10", foreground=HL)
        self.detail.tag_configure("link", foreground=ACCENT, underline=True, font=self._font)
        self.detail.tag_bind("link", "<Enter>", lambda *_: self.detail.configure(cursor="hand2"))
        self.detail.tag_bind("link", "<Leave>", lambda *_: self.detail.configure(cursor="arrow"))
        self.detail.tag_configure("url", foreground=ACCENT, underline=True)
        for t in ("url", "link"):
            self.detail.tag_bind(t, "<Enter>", lambda *_: self.detail.configure(cursor="hand2"))
            self.detail.tag_bind(t, "<Leave>", lambda *_: self.detail.configure(cursor="arrow"))
        self.detail.tag_configure("cardsite", foreground=MUTED, font=(self._font[0], 9, "bold"), lmargin1=12)
        self.detail.tag_configure("cardtitle", foreground=FG, font=(self._font[0], 12, "bold"), lmargin1=12, lmargin2=12)
        self.detail.tag_configure("card", foreground="#c3c9d2", lmargin1=12, lmargin2=12, spacing3=4)
        self.detail.tag_configure("warn", foreground="#f59e0b", font=self._font)
        self.detail.tag_configure("section", foreground=ACCENT, font=(self._font[0], 9, "bold"))
        self.detail.tag_configure("summary", foreground=FG, font=(self._font[0], 12, "italic"))
        self.detail.tag_configure("follow", foreground=OK, font=self._font)
        self.detail.tag_bind("follow", "<Enter>", lambda *_: self.detail.configure(cursor="hand2"))
        self.detail.tag_bind("follow", "<Leave>", lambda *_: self.detail.configure(cursor="arrow"))
        self.detail.tag_bind("follow", "<Button-1>", lambda *_: self.follow_channel())
        self.hpane.add(right, weight=2)

        # pasek stanu
        status = ttk.Frame(r, padding=(18, 8))
        status.pack(fill="x", side="bottom", before=self.vpane)
        self.status = ttk.Label(status, style="Muted.TLabel")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=160)
        self.count = ttk.Label(status, style="Muted.TLabel")
        self.count.pack(side="right")
        self.hidden_btn = ttk.Button(status, text="", command=self._toggle_hidden)
        self.ai_label = ttk.Label(status, style="Muted.TLabel", cursor="hand2")
        self.ai_label.pack(side="left", padx=(16, 0))
        self.ai_label.bind("<Button-1>", lambda *_: self._ai_label_click())

        r.after(60, lambda: self.hpane.sashpos(0, int(self.hpane.winfo_width() * 0.64)))
        r.bind("<Control-f>", lambda *_: self.qentry.focus_set())
        r.bind("<Escape>", lambda *_: self.qvar.set(""))
        self._mode_changed()
        self._welcome()
        self.qentry.focus_set()
        self.root.after(300, self.check_local_ai)

    # ------------------------------------------------------------ pomocnicze
    def _set_busy(self, busy: bool):
        if busy:
            self.progress.pack(side="right", padx=(0, 14))
            self.progress.start(12)
            self.search_btn.configure(state="disabled")
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.search_btn.configure(state="normal")

    def _mode_changed(self):
        glob = self.mode.get() == "global"
        if glob:
            self.g_frame.pack(fill="x", pady=(8, 0), before=self.flt_frame)
        else:
            self.g_frame.pack_forget()
        if glob:
            self.hint.configure(text="Na żywo na serwerach Telegrama — także kanały i grupy, do których nie należysz. "
                                     "#hashtag = bez limitu. Wszystkie publiczne kanały naraz wymagają Premium.")
        elif self.mode.get() == "ai":
            self.hint.configure(text="Pisz po ludzku, np.: „co Marek pisał o przekaźniku w zeszłym tygodniu” "
                                     "albo „kiedy jest najbliższe spotkanie klubu”")
        else:
            self.hint.configure(text="Słowa kluczowe. Gwiazdka łapie odmianę: anten* → antena, antenie, antenna. "
                                     "Ogonki bez znaczenia. Puste pole + filtr = wszystkie wiadomości z czatu/osoby.")

    def _welcome(self):
        st = self.index.stats()
        self._set_detail_text([
            ("Jak zacząć\n\n", "who"),
            ("1.  Ustawienia → wpisz api_id i api_hash z my.telegram.org\n", ""),
            ("2.  Zaloguj → numer telefonu i kod z Telegrama\n", ""),
            ("3.  ⟳ Synchronizuj → pobranie wiadomości do lokalnego indeksu\n", ""),
            ("4.  Szukaj słów albo przełącz na ✦ Zapytaj AI\n\n", ""),
            (f"W indeksie: {st['total']} wiadomości.\n", "meta"),
            ("Program tylko czyta — niczego nie wysyła i nie usuwa.", "meta"),
        ])

    def _set_detail_text(self, parts):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        for text, tag in parts:
            d.insert("end", text, tag or ())
        d.configure(state="disabled")

    def refresh_meta(self):
        st = self.index.stats()
        if st["total"] == 0:
            msg = "Indeks pusty — Zaloguj, potem ⟳ Synchronizuj."
        else:
            parts = [f"{s['source']}: {s['msgs']:,} wiad. w {s['chats']} czatach".replace(",", " ")
                     for s in st["sources"]]
            newest = max((s["newest"] or "") for s in st["sources"])
            msg = " · ".join(parts) + f" · najnowsza: {local_time(newest)}"
        sess = Path(str(self.cfg.telegram_session) + ".session")
        msg += "   |   Telegram: " + ("sesja zapisana" if sess.exists() else "niezalogowany")
        self.status.configure(text=msg)
        self.chat_cb.configure(values=self.index.known_chats())
        self.sender_cb.configure(values=self.index.known_senders())

    def reload_config(self):
        self.cfg = cfgmod.load(str(self.cfg_path))
        self.bg.run(self.drop_shared)   # nowe api_id/hash → nowe połączenie
        self.lai = LocalAI(self.cfg.local_ai, self.cfg.data_dir / "ai_cache.sqlite")
        self.check_local_ai()
        self.refresh_meta()

    def clear_filters(self):
        for v in (self.chat_var, self.sender_var, self.from_var, self.to_var):
            v.set("")
        self.period_var.set(PERIODS[0])
        self.hour_var.set(HOURS[0])
        self.hf_var.set("0")
        self.ht_var.set("23")
        for v in (*self.type_vars.values(), *self.content_vars.values()):
            v.set(False)
        self.dir_var.set("")
        self.order_var.set(ORDERS[0][1])
        self._update_more_label()

    def _toggle_more(self):
        if self.more_visible:
            self.more_frame.pack_forget()
        else:
            self.more_frame.pack(fill="x", pady=(8, 0), after=self.flt_frame)
        self.more_visible = not self.more_visible
        self._update_more_label()

    def _update_more_label(self):
        n = (self.hour_var.get() != HOURS[0]) + any(v.get() for v in self.type_vars.values()) \
            + any(v.get() for v in self.content_vars.values()) + bool(self.dir_var.get()) \
            + (self.order_var.get() != ORDERS[0][1])
        arrow = "▴" if self.more_visible else "▾"
        self.more_btn.configure(text=f"Filtry{f' ({n})' if n else ''} {arrow}")

    def _period_changed(self):
        a, b = period_range(self.period_var.get())
        if a is not None or self.period_var.get() == PERIODS[0]:
            self.from_var.set(a or "")
            self.to_var.set(b or "")

    def _hours_changed(self):
        rng = HOUR_RANGES.get(self.hour_var.get())
        if rng:
            self.hf_var.set(str(rng[0]))
            self.ht_var.set(str(rng[1]))
        elif self.hour_var.get() == HOURS[0]:
            self.hf_var.set("0")
            self.ht_var.set("23")

    def _hours_custom(self):
        self.hour_var.set("własne")

    def _filters(self) -> Query | None:
        q = Query()
        for var, attr in ((self.from_var, "date_from"), (self.to_var, "date_to")):
            val = var.get().strip()
            if val:
                try:
                    date.fromisoformat(val)
                except ValueError:
                    messagebox.showerror("Szperacz", f"Data „{val}” — wpisz w formacie RRRR-MM-DD, np. 2026-10-01.")
                    return None
                setattr(q, attr, val)
        if self.chat_var.get().strip():
            q.chats = [self.chat_var.get().strip()]
        if self.sender_var.get().strip():
            q.senders = [self.sender_var.get().strip()]
        if self.hour_var.get() != HOURS[0]:
            try:
                q.hour_from = max(0, min(23, int(self.hf_var.get())))
                q.hour_to = max(0, min(23, int(self.ht_var.get())))
            except ValueError:
                messagebox.showerror("Szperacz", "Godziny wpisz jako liczby 0–23.")
                return None
        q.chat_types = [k for k, v in self.type_vars.items() if v.get()]
        q.content = [k for k, v in self.content_vars.items() if v.get()]
        q.direction = self.dir_var.get() or None
        q.order = next((k for k, txt in ORDERS if txt == self.order_var.get()), "rank")
        try:
            q.limit = max(10, min(5000, int(self.limit_var.get())))
        except ValueError:
            q.limit = 300
        self._update_more_label()
        return q

    # ------------------------------------------------------------- szukanie
    def do_search(self):
        text = self.qvar.get().strip()
        q = self._filters()
        if q is None:
            return
        if self.mode.get() == "words":
            q.keyword_groups = [[w] for w in text.split()]
            if not text and not q.has_filters():
                self.qentry.focus_set()
                return
            r = service.search_words(self.index, q)
            self._hide_answer()
            self.show_results(r)
            return

        if not text:
            self.qentry.focus_set()
            return
        if self.mode.get() == "global":
            self.global_search(text, filters=q)
            return
        if not self.cfg.ai.api_key:
            if messagebox.askyesno("Zapytaj AI", "Do pytań po ludzku potrzebny jest klucz Claude API.\n"
                                                 "Otworzyć Ustawienia?"):
                self.open_settings()
            return
        cfg = self.cfg

        def work():
            ix = Index(cfg.index_path)
            try:
                return service.ask(cfg, ix, text, extra=q)
            finally:
                ix.close()

        self._show_answer()
        self._set_answer([("Myślę…", "muted")])
        self.bg.run(work, self.show_results, self._ai_error)

    def global_search(self, text, allow_paid_stars=None, filters=None):
        if not self._need_telegram():
            return
        use_ai = self.g_ai.get() and bool(self.cfg.ai.api_key)
        cfg = self.cfg
        opts = dict(posts=self.g_posts.get(), mine=self.g_mine.get(), discover=self.g_disc.get())

        async def work():
            tg = await self.shared_tg()
            return await service.global_search(
                cfg, tg, text, use_ai=use_ai, allow_paid_stars=allow_paid_stars,
                log=lambda m: self.bg.call_ui(self._progress_note, m), filters=filters, **opts)

        self._show_answer()
        self._set_answer([("Szukam w Telegramie…", "muted")])
        self.bg.run(work, lambda r: self._global_done(r, text), self._ai_error)

    def _progress_note(self, msg):
        if self.answer_visible:
            self._set_answer([(msg.strip(), "muted")])

    def _global_done(self, r, text):
        self._show_answer()
        self.show_results(r)
        parts = _split_refs(r.answer) if r.answer else []
        if not r.hits:
            parts.append(("Nic nie znaleziono.", ""))
        meta = []
        if r.tg_query and r.tg_query != text:
            meta.append(f"Fraza dla Telegrama: {r.tg_query}")
        meta += r.notes
        if r.discovered:
            meta.append("Znalezione publiczne kanały/grupy: " + ", ".join(c.name for c in r.discovered[:8]))
        if meta:
            parts.append((("\n\n" if parts else "") + "\n".join("• " + m for m in meta), "muted"))
        self._set_answer(parts)
        if r.payment_needed:
            if messagebox.askyesno("Wszystkie publiczne kanały",
                                   f"Darmowe wyszukiwania w publicznych kanałach na dziś się skończyły.\n\n"
                                   f"Zapłacić {r.payment_needed} ⭐ (Telegram Stars) za to jedno wyszukiwanie?\n"
                                   f"Pozostałe wyniki (Twoje czaty, znalezione grupy) już są na liście."):
                prev = list(self.hits)
                self._paid_prev = prev
                self.global_search_paid(text, r.payment_needed)

    def global_search_paid(self, text, stars):
        cfg = self.cfg

        async def work():
            tg = await self.shared_tg()
            return await service.global_search(cfg, tg, text, use_ai=False, allow_paid_stars=stars,
                                               posts=True, mine=False, discover=False)

        def done(r):
            seen = {(h.chat_name, h.text) for h in r.hits}
            r.hits = r.hits + [h for h in getattr(self, "_paid_prev", []) if (h.chat_name, h.text) not in seen]
            self._global_done(r, text)

        self.bg.run(work, done, self._ai_error)

    def _ai_error(self, e):
        self._set_answer([(friendly_error(e), "muted")])

    def show_results(self, r: service.Result):
        self.all_hits = list(r.hits)
        self.last_groups = r.query.keyword_groups
        self._result_notes = list(r.notes)
        self._keep_order = self.mode.get() == "ai" and not isinstance(r, service.GlobalOutcome)
        self.show_hidden = False
        for h in self.all_hits:
            self._apply_shield(h)
        self._render_list(select_first=True)

        if self.mode.get() == "ai" and not isinstance(r, service.GlobalOutcome):
            parts = []
            if r.answer:
                parts += _split_refs(r.answer)
            elif not r.hits:
                parts.append(("Nie znalazłem pasujących wiadomości w indeksie.", ""))
            parts.append(("\n\nSzukano: " + service.describe_query(r.query), "muted"))
            self._set_answer(parts)

        if not r.hits:
            self._set_detail_text([("Brak wyników.\n\n", "who"),
                                   ("Spróbuj gwiazdki (anten*), innego słowa albo usuń filtry.", "meta")])
        self._start_ai_review()

    # ---------------------------------------------------- tarcza i lokalne AI
    def _is_hidden_chat(self, h) -> bool:
        hidden = set(self.cfg.filters.hidden_chats)
        return bool(hidden) and (str(h.chat_id) in hidden or h.chat_name in hidden)

    def _apply_shield(self, h):
        if h.verdict and h.verdict.get("src") == "AI":
            return
        cat, why = (None, "")
        if self.cfg.filters.shield:
            cat, why = shield.classify(h.text, h.chat_name, h.sender_name or "", h.media,
                                       shield.is_public_origin(h.chat_type))
        h.verdict = {"cat": cat, "src": "tarcza", "why": why} if cat else None

    def _hidden(self, h) -> bool:
        if self._is_hidden_chat(h):
            return True
        return bool(h.verdict and h.verdict.get("cat") in shield.HIDDEN_CATEGORIES)

    def _render_list(self, select_first=False):
        sel_hit = None
        sel = self.tree.selection()
        if sel and not select_first:
            try:
                sel_hit = self.hits[int(sel[0])]
            except (IndexError, ValueError):
                sel_hit = None
        if self._keep_order:
            shown = list(self.all_hits)
            hidden = []
        else:
            shown = [h for h in self.all_hits if not self._hidden(h)]
            hidden = [h for h in self.all_hits if self._hidden(h)]
            if self.show_hidden and self.cfg.local_ai.hidden_mode != "usun":
                shown += hidden
        self.hits = shown
        self.tree.delete(*self.tree.get_children())
        for n, h in enumerate(shown, 1):
            v = h.verdict or {}
            if v.get("pl") and self.cfg.local_ai.auto_translate:
                snip = "⇄ " + " ".join(v["pl"].split())[:160]
            else:
                snip = make_snippet(h.text, self.last_groups, width=140, lead=14)
            icon = MEDIA_LABELS.get(h.media or "")
            if icon:
                snip = f"{icon} {snip}"
            if v.get("cat") in ("news",):
                snip = "📰 " + snip
            tags = ["odd"] if n % 2 == 0 else []
            if self._hidden(h):
                tags = ["hiddenrow"]
            self.tree.insert("", "end", iid=str(n - 1), tags=tuple(tags),
                             values=(n, local_time(h.date), h.chat_name, h.sender_name or "?", snip))
        info = f"{len(shown) if not hidden or self.show_hidden else len(shown)} wyników"
        if self._result_notes:
            info = " ".join(self._result_notes) + "   " + info
        self.count.configure(text=info)
        if hidden and self.cfg.local_ai.hidden_mode != "usun":
            self.hidden_btn.configure(text=("Schowaj ukryte" if self.show_hidden else f"Pokaż ukryte ({len(hidden)})"))
            self.hidden_btn.pack(side="right", padx=(0, 12))
        elif hidden:
            self.hidden_btn.pack_forget()
            self.count.configure(text=info + f"  ·  ukryto {len(hidden)} (spam / 18+)")
        else:
            self.hidden_btn.pack_forget()
        if shown:
            idx = shown.index(sel_hit) if sel_hit in shown else 0
            if select_first or sel_hit is None or sel_hit in shown:
                self.tree.selection_set(str(idx))
                self.tree.see(str(idx))
        elif self.all_hits:
            self._set_detail_text([("Wszystkie wyniki ukryte jako spam / 18+.\n\n", "who"),
                                   ("Kliknij „Pokaż ukryte” na dole, żeby je zobaczyć.", "meta")])

    def _toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self._render_list()

    def check_local_ai(self):
        def work():
            return self.lai.status(refresh=True)

        def done(st):
            self.lai_status = st
            self._update_ai_label()

        self.bg.run(work, done, lambda e: None)

    def _update_ai_label(self):
        st = self.lai_status or {}
        if not st or not self.cfg.local_ai.enabled:
            self.ai_label.configure(text="Lokalne AI: wyłączone" if not self.cfg.local_ai.enabled else "")
        elif st.get("text"):
            vis = st.get("vision")
            self.ai_label.configure(text=f"🧠 {st['text']}" + (f" · 👁 {vis}" if vis else f"  ·  brak modelu obrazu — pobierz {RECOMMENDED_VISION}"))
        else:
            self.ai_label.configure(text="Lokalne AI: " + (st.get("error") or "niedostępne"))

    def _ai_label_click(self):
        st = self.lai_status or {}
        if st.get("backend") == "ollama" and not st.get("vision"):
            if messagebox.askyesno("Model obrazu", f"Pobrać model {RECOMMENDED_VISION} (ok. 6 GB) do Ollamy?\n"
                                                   "Służy do sprawdzania zdjęć z publicznych kanałów przed pokazaniem."):
                self.pull_model(RECOMMENDED_VISION)
        else:
            self.open_settings()

    def pull_model(self, name):
        win = LogWindow(self, f"Pobieram {name}")

        def work():
            self.lai.pull(name, lambda m: self.bg.call_ui(win.append, m))
            return name

        def done(n):
            win.finish(f"Pobrano {n}.", ok=True)
            self.check_local_ai()

        self.bg.run(work, done, lambda e: win.finish(friendly_error(e), ok=False))

    def _start_ai_review(self):
        st = self.lai_status or {}
        if not (self.cfg.local_ai.enabled and st.get("text") and self.all_hits):
            return
        self._ai_run += 1
        run = self._ai_run
        hits = list(self.all_hits[:200])
        by_key = {id(h): h for h in hits}
        items = [{"key": id(h), "text": h.text, "chat": h.chat_name, "sender": h.sender_name or ""} for h in hits]

        def apply(partial):
            if run != self._ai_run:
                return
            for k, v in partial.items():
                h = by_key.get(k)
                if h is None:
                    continue
                cat = v.get("kategoria")
                h.verdict = {"cat": cat, "src": "AI", "why": "lokalne AI", "lang": v.get("jezyk"),
                             "pl": v.get("tlumaczenie") or ""}
            self._render_list()
            self.count.configure(text=self.count.cget("text") + "   🧠 AI ocenia…")

        def work():
            return self.lai.classify(items, on_partial=lambda part: self.bg.call_ui(apply, part))

        def done(_):
            if run == self._ai_run:
                self._render_list()

        self.bg.run(work, done, lambda e: (log_error(e), self._render_list()))

    def _show_answer(self):
        if not self.answer_visible:
            self.vpane.insert(0, self.answer_frame, weight=1)
            self.answer_visible = True
            self.root.after(30, lambda: self.vpane.sashpos(0, 230))

    def _hide_answer(self):
        if self.answer_visible:
            self.vpane.forget(self.answer_frame)
            self.answer_visible = False

    def _set_answer(self, parts):
        a = self.answer
        a.configure(state="normal")
        a.delete("1.0", "end")
        for text, tag in parts:
            a.insert("end", text, tag or ())
        a.configure(state="disabled")

    def _on_ref_click(self, event):
        idx = self.answer.index(f"@{event.x},{event.y}")
        rng = self.answer.tag_prevrange("ref", idx + "+1c")
        if not rng:
            return
        m = re.search(r"\d+", self.answer.get(*rng))
        if m:
            i = int(m.group()) - 1
            if 0 <= i < len(self.hits):
                self.tree.selection_set(str(i))
                self.tree.see(str(i))

    def _on_select(self, *_):
        sel = self.tree.selection()
        if not sel:
            return
        h = self.hits[int(sel[0])]
        self._sel_token = getattr(self, "_sel_token", 0) + 1
        token = self._sel_token
        self._render_detail(h, media_state="loading" if self._wants_media(h) else None)
        if self._wants_media(h):
            cached = self._media_cache_path(h)
            if cached.exists():
                self._render_detail(h, media_state={"path": str(cached), "media": h.media, "extra": h.extra})
            # krótka zwłoka: przewijanie strzałkami nie zasypuje Telegrama zapytaniami
            self.root.after(350, lambda: self._load_media(h, token))

    def _wants_media(self, h) -> bool:
        if h.source != "telegram" or not h.chat_id or not h.msg_id:
            return False
        if h.media is None:          # stara wiadomość z indeksu — nie wiemy, czy ma zdjęcie
            return True
        return h.media in ("photo", "webpage", "video", "gif", "document", "sticker")

    def _media_cache_path(self, h) -> Path:
        return self.cfg.data_dir / "media" / f"{str(h.chat_id).replace('-', 'm')}_{h.msg_id}.jpg"

    def _load_media(self, h, token):
        if token != self._sel_token:
            return
        sess = Path(str(self.cfg.telegram_session) + ".session")
        if not sess.exists():
            self._render_detail(h, media_state=None)
            return
        cfg = self.cfg

        async def work():
            tg = await self.shared_tg()
            return await tg.fetch_media(h.chat_id, h.msg_id, cfg.data_dir / "media")

        def done(info):
            if info.get("media") is None and h.media:    # brak odpowiedzi ≠ brak mediów
                info = {**info, "media": h.media, "extra": info.get("extra") or h.extra}
            if info.get("media") != h.media or info.get("extra") != h.extra:
                h.media, h.extra = info.get("media") or "", info.get("extra") or h.extra
                if h.row_id and h.row_id > 0:
                    try:
                        self.index.update_media(h.source, h.chat_id, h.msg_id, h.media, h.extra)
                    except Exception:  # noqa: BLE001
                        pass
            if token == self._sel_token:
                self._render_detail(h, media_state=info)

        def err(e):
            log_error(e)
            if token == self._sel_token:
                self._render_detail(h, media_state={"error": friendly_error(e)})

        self.bg.run(work, done, err)

    def _render_detail(self, h, media_state=None):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        self._detail_links = {}
        d.insert("end", (h.sender_name or "?") + ("  (ja)" if h.is_out else "") + "\n", "who")
        origin = h.chat_type.split("·", 1)[1].strip() if h.chat_type and "·" in h.chat_type else h.source
        kind = MEDIA_LABELS.get(h.media or "", "")
        d.insert("end", f"{h.chat_name}  ·  {local_time(h.date)}  ·  {origin}  {kind}\n\n", "meta")

        v = h.verdict or {}
        if v.get("cat"):
            src = "lokalne AI" if v.get("src") == "AI" else f"tarcza: {v.get('why')}"
            d.insert("end", f"Ocena: {shield.LABELS.get(v['cat'], v['cat'])}  ({src})\n\n",
                     "warn" if v["cat"] in shield.HIDDEN_CATEGORIES else "meta")

        # zdjęcie / miniatura (z publicznych kanałów — dopiero po sprawdzeniu)
        if media_state == "loading":
            d.insert("end", "Ładuję zdjęcie…\n\n", "meta")
        elif isinstance(media_state, dict):
            if media_state.get("path"):
                self._insert_gated_image(h, media_state["path"])
            elif media_state.get("error"):
                d.insert("end", f"(nie udało się pobrać zdjęcia: {media_state['error']})\n\n", "meta")

        # tłumaczenie (lokalne AI)
        tr = self._translations.get(self._hkey(h)) if hasattr(self, "_translations") else None
        if tr and tr.get("tlumaczenie"):
            d.insert("end", f"TŁUMACZENIE NA POLSKI  (z: {tr.get('jezyk') or '?'}, lokalne AI)\n", "section")
            if tr.get("streszczenie"):
                d.insert("end", tr["streszczenie"] + "\n\n", "summary")
            d.insert("end", tr["tlumaczenie"] + "\n\n")
            d.insert("end", "ORYGINAŁ\n", "section")
        elif tr is None and self._wants_translation(h):
            d.insert("end", "⇄ tłumaczę na polski…\n\n", "meta")
            self._load_translation(h)

        # pełna treść z podświetleniem i klikalnymi linkami
        start = d.index("end-1c")
        d.insert("end", h.text)
        for a, b in term_spans(h.text, self.last_groups):
            d.tag_add("hl", f"{start}+{a}c", f"{start}+{b}c")
        for m in URL_RE.finditer(h.text):
            self._tag_link(f"{start}+{m.start()}c", f"{start}+{m.end()}c", m.group().rstrip(".,);:!?"))
        d.insert("end", "\n")

        # pełny opis podglądu linku / plik / ankieta
        extra = (media_state.get("extra") if isinstance(media_state, dict) else None) or h.extra or {}
        media = (media_state.get("media") if isinstance(media_state, dict) else None) or h.media
        if media == "webpage" and extra:
            d.insert("end", "\n")
            if extra.get("site"):
                d.insert("end", extra["site"].upper() + "\n", "cardsite")
            if extra.get("title"):
                d.insert("end", extra["title"] + "\n", "cardtitle")
            if extra.get("description"):
                d.insert("end", extra["description"] + "\n", "card")
            if extra.get("url"):
                st = d.index("end-1c")
                d.insert("end", extra["url"] + "\n")
                self._tag_link(st, d.index("end-2c"), extra["url"])
        elif media in ("document", "video", "audio", "voice") and extra:
            bits = [extra.get("file") or extra.get("title")]
            if extra.get("duration"):
                bits.append(f"{extra['duration'] // 60}:{extra['duration'] % 60:02d}")
            if extra.get("size"):
                bits.append(_human_size(extra["size"]))
            d.insert("end", "\n" + MEDIA_LABELS.get(media, "") + " " + "  ·  ".join(b for b in bits if b) + "\n", "meta")
        elif media == "geo" and extra.get("lat") is not None:
            url = f"https://www.openstreetmap.org/?mlat={extra['lat']}&mlon={extra['lon']}#map=15/{extra['lat']}/{extra['lon']}"
            st = d.index("end-1c")
            d.insert("end", "\n📍 Pokaż na mapie\n")
            self._tag_link(f"{st}+1c", d.index("end-1c"), url)

        # akcje
        d.insert("end", "\n")
        if h.link:
            self._action(d, "↗ Otwórz podgląd", lambda: self._open_selected_link())
            tg = telegram_app_link(h.link)
            if tg:
                d.insert("end", "     ")
                self._action(d, "📱 W aplikacji Telegram", lambda: browser.open_url(tg, self.cfg.data_dir))
            uname = _username_from_link(h.link)
            if (h.verdict or {}).get("cat") in shield.HIDDEN_CATEGORIES:
                uname = None          # spamu nie proponujemy do obserwowania
            if uname and uname.lower() not in {fold(x).lstrip("@").replace("https://t.me/", "") for x in self.cfg.telegram.watched}:
                d.insert("end", "     ")
                d.insert("end", f"＋ Obserwuj @{uname}", ("follow",))
                self._follow_target = uname
        else:
            d.insert("end", "Czat prywatny / mała grupa — Telegram nie daje linków do takich wiadomości.", "meta")
        if h.chat_id and (shield.is_public_origin(h.chat_type) or v.get("cat") in shield.HIDDEN_CATEGORIES):
            d.insert("end", "\n\n")
            self._action(d, "🚫 Ukryj ten kanał na zawsze", lambda: self.hide_chat(h))
        d.configure(state="disabled")

    # ------------------------------------------------ tłumaczenie i zdjęcia
    @staticmethod
    def _hkey(h):
        return (h.source, str(h.chat_id), h.msg_id, h.text[:50])

    def _wants_translation(self, h) -> bool:
        st = self.lai_status or {}
        if not (self.cfg.local_ai.enabled and self.cfg.local_ai.auto_translate and st.get("text")):
            return False
        v = h.verdict or {}
        if v.get("cat") in shield.HIDDEN_CATEGORIES:
            return False
        if v.get("lang"):
            return not v["lang"].startswith("pl")
        letters = re.sub(r"[^\w]", "", h.text)
        return len(letters) > 15

    def _load_translation(self, h):
        if not hasattr(self, "_translations"):
            self._translations = {}
        key = self._hkey(h)
        if key in self._translations or getattr(self, "_tr_pending", None) == key:
            return
        self._tr_pending = key

        def work():
            return self.lai.translate(h.text)

        def done(tr):
            self._translations[key] = tr
            self._tr_pending = None
            if tr.get("jezyk") and h.verdict is not None:
                h.verdict.setdefault("lang", tr["jezyk"])
            sel = self.tree.selection()
            if sel and self.hits[int(sel[0])] is h:
                self._rerender_current(h)

        def err(e):
            log_error(e)
            self._translations[key] = {"tlumaczenie": "", "jezyk": ""}
            self._tr_pending = None

        self.bg.run(work, done, err)

    def _rerender_current(self, h):
        cached = self._media_cache_path(h) if self._wants_media(h) else None
        state = {"path": str(cached), "media": h.media, "extra": h.extra} if cached and cached.exists() else None
        self._render_detail(h, media_state=state)

    def _insert_gated_image(self, h, path):
        key = self._hkey(h)
        public = shield.is_public_origin(h.chat_type) or (h.verdict or {}).get("cat") in shield.HIDDEN_CATEGORIES
        if not public or key in self._revealed:
            self._insert_image(path)
            return
        st = self.lai_status or {}
        checks = getattr(self, "_img_checks", {})
        self._img_checks = checks
        if self.cfg.local_ai.enabled and self.cfg.local_ai.check_images and st.get("vision"):
            res = checks.get(path)
            if res is None:
                self.detail.insert("end", "👁 Lokalne AI sprawdza zdjęcie…\n\n", "meta")
                checks[path] = "pending"

                def work():
                    return self.lai.check_image(path)

                def done(r):
                    checks[path] = r
                    sel = self.tree.selection()
                    if sel and self.hits[int(sel[0])] is h:
                        self._rerender_current(h)

                def err(e):
                    log_error(e)
                    checks[path] = {"bezpieczne": False, "opis": "", "blad": friendly_error(e)}

                self.bg.run(work, done, err)
                return
            if res == "pending":
                self.detail.insert("end", "👁 Lokalne AI sprawdza zdjęcie…\n\n", "meta")
                return
            if res.get("bezpieczne"):
                self._insert_image(path)
                if res.get("opis"):
                    self.detail.insert("end", f"👁 {res['opis']}\n\n", "meta")
                return
            why = ("treści dla dorosłych" if res.get("dorosli") else "drastyczne" if res.get("przemoc_drastyczna")
                   else "spam / reklama" if res.get("spam") else res.get("blad") or "niepewne")
            self.detail.insert("end", f"🔞 Zdjęcie ukryte (lokalne AI: {why}).  ", "warn")
        else:
            self.detail.insert("end", "🖼 Zdjęcie z kanału spoza Twoich czatów jest zasłonięte.  ", "meta")
        self._action(self.detail, "Pokaż mimo to", lambda: (self._revealed.add(key), self._rerender_current(h)))
        if st.get("backend") == "ollama" and not st.get("vision"):
            self.detail.insert("end", "   ")
            self._action(self.detail, f"Pobierz model obrazu ({RECOMMENDED_VISION})",
                         lambda: self.pull_model(RECOMMENDED_VISION))
        self.detail.insert("end", "\n\n")

    def hide_chat(self, h):
        ref = str(h.chat_id)
        if not messagebox.askyesno("Ukryj kanał", f"Ukrywać zawsze wyniki z „{h.chat_name}”?\n"
                                                  "Cofniesz to w config.toml ([filtr] ukryte_kanaly)."):
            return
        cfgmod.add_hidden_chat(self.cfg_path, ref)
        self.cfg = cfgmod.load(str(self.cfg_path))
        self._render_list()

    def _insert_image(self, path):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.detail.insert("end", "(do podglądu zdjęć uruchom Szperacz.bat — doinstaluje Pillow)\n\n", "meta")
            return
        try:
            img = Image.open(path)
            w = max(240, self.detail.winfo_width() - 40)
            img.thumbnail((w, 460))
            self._detail_img = ImageTk.PhotoImage(img)
            self.detail.image_create("end", image=self._detail_img)
            self.detail.insert("end", "\n\n")
        except Exception as e:  # noqa: BLE001
            self.detail.insert("end", f"(nie da się wyświetlić zdjęcia: {e})\n\n", "meta")

    def _tag_link(self, a, b, url):
        n = len(self._detail_links)
        tag = f"url{n}"
        self._detail_links[tag] = url
        self.detail.tag_add("url", a, b)
        self.detail.tag_add(tag, a, b)
        self.detail.tag_bind(tag, "<Button-1>", lambda *_: browser.open_url(url, self.cfg.data_dir, self._browser_failed))

    def _action(self, d, text, fn):
        tag = f"act{len(self._detail_links)}"
        self._detail_links[tag] = text
        d.insert("end", text, ("link", tag))
        d.tag_bind(tag, "<Button-1>", lambda *_: fn())

    def follow_channel(self):
        name = getattr(self, "_follow_target", None)
        if not name:
            return
        added = cfgmod.add_watched(self.cfg_path, "@" + name)
        self.reload_config()
        messagebox.showinfo("Obserwowane", (f"Dodano @{name} do obserwowanych." if added else f"@{name} już jest obserwowany.")
                            + "\n\nPrzy następnej ⟳ Synchronizacji jego wiadomości trafią do indeksu "
                              "(bez dołączania do kanału) i będzie go można przeszukiwać lokalnie i przez AI.")
        self._on_select()

    def _open_selected_link(self):
        sel = self.tree.selection()
        if sel:
            h = self.hits[int(sel[0])]
            if h.link:
                browser.open_url(h.link, self.cfg.data_dir, self._browser_failed)

    def _browser_failed(self, reason):
        log_line(f"Wbudowana przeglądarka nie wystartowała: {reason}")
        self.bg.call_ui(lambda: self.count.configure(
            text="Wbudowana przeglądarka nie wystartowała — otwieram w zwykłej (szczegóły w szperacz.log)"))

    # ------------------------------------------------------------- Telegram
    def _connector(self):
        cfgmod.require_telegram(self.cfg)
        from .connectors.telegram import TelegramConnector
        t = self.cfg.telegram
        return TelegramConnector(t.api_id, t.api_hash, self.cfg.telegram_session, t.phone)

    def _need_telegram(self) -> bool:
        if not self.cfg.telegram.api_id or not self.cfg.telegram.api_hash:
            SettingsDialog(self, first_run=True)
            return False
        return True

    def login(self):
        """Domyślnie logowanie kodem QR (najpewniejsze); kod jako alternatywa."""
        if not self._need_telegram():
            return
        tg = self._connector()
        dlg = QrDialog(self)

        async def work():
            await self.drop_shared()
            try:
                return await tg.qr_login(
                    show_qr=lambda url, exp: self.bg.call_ui(dlg.set_qr, url, exp),
                    cancelled=lambda: dlg.cancel,
                    password_cb=lambda: self._ask_password(),
                    log=lambda m: (log_line(m), "zeskanowany" in m and self.bg.call_ui(
                        dlg.set_status, "Zeskanowano — loguję…")))
            finally:
                await tg.close()

        def done(name):
            dlg.close()
            self._logged_in(name)

        def err(e):
            from .connectors.telegram import QrCancelled
            if isinstance(e, QrCancelled):
                if dlg.choice == "code":
                    self.login_code()
                return
            dlg.close()
            show_error(e)

        self.bg.run(work, done, err)

    def _ask_password(self):
        v = self.bg.ask_ui(lambda: ask_string(self.root, "Hasło 2FA",
                                              "Konto ma weryfikację dwuetapową (hasło chmury). Hasło:", True))
        if not v:
            raise LoginCancelled()
        return v

    def _logged_in(self, name):
        log_line(f"Zalogowano: {name}")
        self.refresh_meta()
        messagebox.showinfo("Szperacz", f"Zalogowano jako: {name}\n\nTeraz kliknij ⟳ Synchronizuj.")

    def login_code(self):
        if not self._need_telegram():
            return
        tg = self._connector()

        def ask(title, prompt, secret=False):
            def cb():
                v = self.bg.ask_ui(lambda: ask_string(self.root, title, prompt, secret))
                if not v:
                    raise LoginCancelled()
                return v.replace(" ", "") if not secret else v
            return cb

        def code_cb(info):
            v = self.bg.ask_ui(lambda: ask_code(self.root, info))
            if not v:
                raise LoginCancelled()
            return v

        async def work():
            await self.drop_shared()
            try:
                return await tg.login(
                    phone_cb=ask("Logowanie", "Numer telefonu z kodem kraju, np. +447700900123"),
                    code_cb=code_cb,
                    password_cb=ask("Hasło 2FA", "Konto ma weryfikację dwuetapową (hasło chmury). Hasło:", secret=True),
                    log=log_line,
                )
            finally:
                await tg.close()

        self.bg.run(work, self._logged_in)

    async def fetch_chats(self):
        tg = await self.shared_tg()
        return await tg.list_chats(watched=self.cfg.telegram.watched)

    # Jedno wspólne połączenie z Telegramem dla całego okna: plik sesji może być
    # otwarty tylko raz, a synchronizacja i szukanie mają działać jednocześnie.
    async def shared_tg(self):
        import asyncio
        if getattr(self, "_tg_lock", None) is None:
            self._tg_lock = asyncio.Lock()
        async with self._tg_lock:
            tg = getattr(self, "_shared", None)
            if tg is None or tg.client is None or not tg.client.is_connected():
                tg = self._connector()
                await tg.open()
                self._shared = tg
            return tg

    async def drop_shared(self):
        tg = getattr(self, "_shared", None)
        self._shared = None
        if tg is not None:
            try:
                await tg.close()
            except Exception:  # noqa: BLE001
                pass

    def open_chats(self):
        if self._need_telegram():
            ChatsWindow(self)

    def start_sync(self, only=None):
        if not self._need_telegram():
            return
        from .sync import ChatFilter, sync
        cfg = self.cfg
        t = cfg.telegram
        flt = ChatFilter(t.include, t.exclude, t.skip_channels)
        if getattr(self, "_syncing", False):
            messagebox.showinfo("Synchronizacja", "Synchronizacja już trwa — poczekaj, aż się skończy.\n"
                                                  "W tym czasie możesz normalnie szukać.")
            return
        self._syncing = True
        win = LogWindow(self, "Synchronizacja")

        async def work():
            ix = Index(cfg.index_path)
            try:
                tg = await self.shared_tg()
                return await sync(tg, ix, flt, first_sync_limit=t.first_sync_limit, only=only,
                                  log=lambda s: self.bg.call_ui(win.append, s),
                                  watched=t.watched, watched_limit=t.watched_limit)
            finally:
                ix.close()

        def done(s):
            self._syncing = False
            msg = f"Gotowe: {s['new']} nowych wiadomości z {s['chats']} czatów."
            if s["errors"]:
                msg += f"  Błędy w {len(s['errors'])} czatach."
            win.finish(msg, ok=not s["errors"])
            self.refresh_meta()

        def err(e):
            self._syncing = False
            win.finish(friendly_error(e), ok=False)
            self.refresh_meta()

        self.bg.run(work, done, err)

    def open_settings(self):
        SettingsDialog(self)


def _username_from_link(link: str | None) -> str | None:
    m = re.match(r"https://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/\d+", link or "")
    return m.group(1) if m and m.group(1) != "c" else None


def _split_refs(text: str):
    out, pos = [], 0
    for m in re.finditer(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", text):
        out.append((text[pos:m.start()], ""))
        out.append((m.group(), "ref"))
        pos = m.end()
    out.append((text[pos:], ""))
    return out


def ensure_config_quiet() -> Path:
    found = cfgmod.find_config()
    if found:
        return found
    path = Path.cwd() / "config.toml"
    path.write_text(cfgmod.TEMPLATE, encoding="utf-8")
    return path


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    # exe / skrót: pracuj w folderze programu, żeby znaleźć config.toml i dane
    if getattr(sys, "frozen", False):
        import os
        os.chdir(Path(sys.executable).parent)
    root = tk.Tk()
    if not _single_instance():
        root.withdraw()
        messagebox.showinfo("Szperacz", "Szperacz jest już otwarty — sprawdź pasek zadań.\n\n"
                                        "Dwa okna naraz nie mogą korzystać z tej samej sesji Telegrama.")
        return
    App(root)
    root.mainloop()


_LOCK_FILE = None


def _single_instance() -> bool:
    """Blokada pliku dane/.szperacz.lock — drugi egzemplarz okna się nie uruchomi."""
    global _LOCK_FILE
    try:
        cfg = cfgmod.load(str(cfgmod.find_config()) if cfgmod.find_config() else None)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        f = open(cfg.data_dir / ".szperacz.lock", "a+")
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE = f
        return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001 — w razie wątpliwości nie blokujemy startu
        return True


if __name__ == "__main__":
    main()
