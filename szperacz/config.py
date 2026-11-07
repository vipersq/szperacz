"""Wczytywanie konfiguracji z pliku config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


TEMPLATE = '# Skopiuj ten plik jako config.toml i uzupełnij.\n\n[ogolne]\n# Tu trafi indeks (indeks.sqlite) i sesja Telegrama (telegram.session).\nkatalog_danych = "dane"\n\n[telegram]\n# Z https://my.telegram.org -> API development tools\napi_id = 0\napi_hash = ""\n# Opcjonalnie, np. "+447700900123" (inaczej program zapyta przy logowaniu)\ntelefon = ""\n\n# Puste = indeksuj wszystkie czaty. Fragmenty nazw, wielkość liter i ogonki bez znaczenia.\ntylko_czaty = []\n# np. ["BBC", "Reuters"] — duże kanały informacyjne, których nie chcesz w indeksie\npomin_czaty = []\n# true = pomiń wszystkie kanały (broadcast); grupy i prywatne zostają\npomin_kanaly = false\n\n# Publiczne kanały/grupy, do których NIE należysz, a chcesz je indeksować\n# (czytane bez dołączania). Np. ["@bbcnews", "https://t.me/rsgb_main"]\nobserwowane = []\n# Ile ostatnich wiadomości pobrać z obserwowanego kanału przy pierwszej synchronizacji\nlimit_obserwowanych = 2000\n\n# Przy PIERWSZEJ synchronizacji czatu: ile najnowszych wiadomości pobrać.\n# 0 = cała historia (przy dużych grupach może trwać długo — Telegram ogranicza tempo).\nlimit_pierwszej_synchronizacji = 0\n\n[ai]\n# Klucz Claude API (console.anthropic.com). Lepiej ustawić zmienną środowiskową\n# ANTHROPIC_API_KEY niż wpisywać tutaj. Bez klucza działa "szukaj", nie działa "pytaj".\nklucz_api = ""\n# Tani i szybki model do zamiany promptu na zapytanie:\nmodel_zapytan = "claude-haiku-5-5"\n# Model do streszczenia wyników i odpowiedzi:\nmodel_odpowiedzi = "claude-sonnet-5-5"\nmax_wynikow_do_odpowiedzi = 40\n\n[lokalne_ai]\n# Lokalne AI przez Ollamę (albo LM Studio): ocenia wyniki (news / spam / 18+),\n# tłumaczy na polski i sprawdza zdjęcia z publicznych kanałów. Nic nie wychodzi do internetu.\nwlaczone = true\nadres_ollama = "http://127.0.0.1:11434"\nadres_lmstudio = "http://127.0.0.1:1234"\n# "auto" = wybierz z zainstalowanych (tekst: np. qwen3:14b, obraz: np. qwen3-vl:8b)\nmodel_tekst = "auto"\nmodel_obraz = "auto"\ntlumacz_automatycznie = true\njezyk_docelowy = "polski"\nsprawdzaj_zdjecia = true\n# "zwin" = ukryte wyniki na dole listy (przycisk „Pokaż ukryte”), "usun" = w ogóle ich nie pokazuj\nukryte = "zwin"\n\n[filtr]\n# Tarcza antyspamowa bez AI (ściana hashtagów, kasyna, 18+ itp.)\ntarcza = true\n# Kanały, które ukryłeś przyciskiem „🚫 Ukryj kanał” (id albo nazwa)\nukryte_kanaly = []\n'


class ConfigError(RuntimeError):
    pass


@dataclass
class TelegramCfg:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    include: list[str] = field(default_factory=list)   # puste = wszystkie czaty
    exclude: list[str] = field(default_factory=list)
    skip_channels: bool = False
    first_sync_limit: int = 0                         # 0 = cała historia
    watched: list[str] = field(default_factory=list)  # publiczne kanały bez dołączania
    watched_limit: int = 2000


@dataclass
class AiCfg:
    api_key: str | None = None
    parse_model: str = "claude-haiku-5-5"
    answer_model: str = "claude-sonnet-5-5"
    max_results_for_answer: int = 40


@dataclass
class LocalAiCfg:
    enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    lmstudio_url: str = "http://127.0.0.1:1234"
    text_model: str = "auto"
    vision_model: str = "auto"
    auto_translate: bool = True
    target_lang: str = "polski"
    check_images: bool = True
    hidden_mode: str = "zwin"       # "zwin" albo "usun"


@dataclass
class FilterCfg:
    shield: bool = True
    hidden_chats: list[str] = field(default_factory=list)


@dataclass
class Config:
    base_dir: Path
    data_dir: Path
    telegram: TelegramCfg
    ai: AiCfg
    local_ai: LocalAiCfg = field(default_factory=LocalAiCfg)
    filters: FilterCfg = field(default_factory=FilterCfg)

    @property
    def index_path(self) -> Path:
        return self.data_dir / "indeks.sqlite"

    @property
    def telegram_session(self) -> Path:
        return self.data_dir / "telegram"   # Telethon dopisze .session


def find_config(explicit: str | None = None) -> Path | None:
    for cand in (explicit, os.environ.get("SZPERACZ_CONFIG"), "config.toml"):
        if cand and Path(cand).is_file():
            return Path(cand).resolve()
    return None


def load(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    raw: dict = {}
    base = Path.cwd()
    if path:
        base = path.parent
        with open(path, "rb") as f:
            raw = tomllib.load(f)

    g = raw.get("ogolne", {})
    data_dir = Path(g.get("katalog_danych", "dane"))
    if not data_dir.is_absolute():
        data_dir = base / data_dir

    t = raw.get("telegram", {})
    tg = TelegramCfg(
        api_id=int(t["api_id"]) if t.get("api_id") else None,
        api_hash=t.get("api_hash") or None,
        phone=t.get("telefon") or None,
        include=list(t.get("tylko_czaty", [])),
        exclude=list(t.get("pomin_czaty", [])),
        skip_channels=bool(t.get("pomin_kanaly", False)),
        first_sync_limit=int(t.get("limit_pierwszej_synchronizacji", 0)),
        watched=[str(x) for x in t.get("obserwowane", []) if str(x).strip()],
        watched_limit=int(t.get("limit_obserwowanych", 2000)),
    )

    a = raw.get("ai", {})
    ai = AiCfg(
        api_key=os.environ.get("ANTHROPIC_API_KEY") or a.get("klucz_api") or None,
        parse_model=a.get("model_zapytan", AiCfg.parse_model),
        answer_model=a.get("model_odpowiedzi", AiCfg.answer_model),
        max_results_for_answer=int(a.get("max_wynikow_do_odpowiedzi", 40)),
    )
    la = raw.get("lokalne_ai", {})
    local = LocalAiCfg(
        enabled=bool(la.get("wlaczone", True)),
        ollama_url=str(la.get("adres_ollama", LocalAiCfg.ollama_url)).rstrip("/"),
        lmstudio_url=str(la.get("adres_lmstudio", LocalAiCfg.lmstudio_url)).rstrip("/"),
        text_model=str(la.get("model_tekst", "auto")) or "auto",
        vision_model=str(la.get("model_obraz", "auto")) or "auto",
        auto_translate=bool(la.get("tlumacz_automatycznie", True)),
        target_lang=str(la.get("jezyk_docelowy", "polski")),
        check_images=bool(la.get("sprawdzaj_zdjecia", True)),
        hidden_mode="usun" if str(la.get("ukryte", "zwin")).lower().startswith("u") else "zwin",
    )
    f = raw.get("filtr", {})
    flt = FilterCfg(shield=bool(f.get("tarcza", True)),
                    hidden_chats=[str(x) for x in f.get("ukryte_kanaly", []) if str(x).strip()])
    return Config(base_dir=base, data_dir=data_dir, telegram=tg, ai=ai, local_ai=local, filters=flt)


def _toml_value(v) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def update_values(path: Path, changes: dict[str, dict]) -> None:
    """Zmienia pojedyncze klucze w config.toml, zostawiając komentarze i resztę pliku.

    changes = {"telegram": {"api_id": 123, "api_hash": "..."}, "ai": {...}}
    Brakujące sekcje/klucze są dopisywane.
    """
    import re

    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else TEMPLATE
    lines = text.splitlines()
    for section, kv in changes.items():
        # zakres linii sekcji
        start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
        if start is None:
            lines += ["", f"[{section}]"]
            start = len(lines) - 1
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(r"\s*\[[^\]]+\]\s*$", lines[i])), len(lines))
        for key, value in kv.items():
            line = f"{key} = {_toml_value(value)}"
            rx = re.compile(rf"^\s*{re.escape(key)}\s*=")
            idx = next((i for i in range(start + 1, end) if rx.match(lines[i])), None)
            if idx is not None:
                lines[idx] = line
            else:
                lines.insert(end, line)
                end += 1
    out = "\n".join(lines) + "\n"
    tomllib.loads(out)  # nie zapisujemy zepsutego pliku
    Path(path).write_text(out, encoding="utf-8")


def add_watched(path: Path, ref: str) -> bool:
    """Dopisuje kanał do [telegram] obserwowane. False, jeśli już był."""
    with open(path, "rb") as f:
        cur = list(tomllib.load(f).get("telegram", {}).get("obserwowane", []))
    norm = lambda x: x.lower().replace("https://", "").replace("t.me/", "").lstrip("@")  # noqa: E731
    if any(norm(x) == norm(ref) for x in cur):
        return False
    update_values(path, {"telegram": {"obserwowane": cur + [ref]}})
    return True


def add_hidden_chat(path: Path, ref: str) -> bool:
    """Dopisuje kanał do [filtr] ukryte_kanaly. False, jeśli już był."""
    with open(path, "rb") as fh:
        cur = list(tomllib.load(fh).get("filtr", {}).get("ukryte_kanaly", []))
    if ref in cur:
        return False
    update_values(path, {"filtr": {"ukryte_kanaly": cur + [ref]}})
    return True


def require_telegram(cfg: Config) -> None:
    if not cfg.telegram.api_id or not cfg.telegram.api_hash:
        raise ConfigError(
            "Brak api_id / api_hash Telegrama w config.toml (sekcja [telegram]).\n"
            "Wygeneruj je na https://my.telegram.org → API development tools."
        )
