"""Tarcza antyspamowa — szybkie reguły bez AI.

Spam w wyszukiwaniu po całym Telegramie (zwłaszcza po #hashtagach) to głównie konta
„towarzyskie”, kasyna i narkotyki, które doklejają do obrazka dziesiątki popularnych
hashtagów (#kraków, #polska…), żeby wyskakiwać w wynikach. Reguły słownikowe
pochodzą z Kronos-7 (evsearch.py) i są rozszerzone o polski i angielski.

Wynik: kategoria "dorosli" | "spam" | None (wygląda normalnie) + krótki powód.
Lokalne AI (jeśli włączone) może potem tę ocenę poprawić.
"""
from __future__ import annotations

import re
import unicodedata

ADULT = re.compile(
    r"знакомств|знайомств|интим|інтим|эскорт|ескорт|досуг|дозвілл|проститу|индивидуалк|индивидуалок|шлюх|порн|"
    r"нюдс|\bню\b|эротик|еротик|onlyfans|онлифанс|онліфанс|18\s*\+|\bxxx\b|nsfw|escort|hookup|nudes?\b|"
    r"\bsex|секс|porn|erotic|erotyk|roksa|towarzysk|randk[ai]\b|sponsoring|sugar\s*daddy|fetysz|fetish|"
    r"cam\s*girl|webcam|striptiz|striptease|nagie|nago\b|\bcipk|\bbrazzers|\bmilf\b|\bhentai\b", re.I)
SCAM = re.compile(
    r"казино|casino|букмекер|ставки на спорт|закладк|\bмеф\b|мефедрон|кокаин|кокаїн|\bweed\b|"
    r"заработ\w* (?:от|в день)|заробіт\w*|darmowe spiny|free spins|bonus bez depozytu|"
    r"pump\s*signals?|100x\s*gwarant|guaranteed profit|crypto signals?|airdrop claim|"
    r"kup (?:followers|obserwuj)|tanie (?:konta|followers)|dokumenty kolekcjonerskie|fałszyw\w+ dowod", re.I)
TITLE_BAIT = re.compile(
    r"девушк|дівчат|девочк|\bgirls?\b|красотк|красун|знакомств|знайомств|массаж|масаж|relax|релакс|\bvip\b|"
    r"sexy|секс|\bhot\b|\b18\b|казино|casino|flirt|\blove\s*heat\b|dating|singles?\b|online\s+\w+\s+x\s+\w+", re.I)
PERSONA = re.compile(r"^\W*[A-ZŁŚŻŹĆ][a-ząćęłńóśźżA-Z]+\s+(?:online|live|official|vip|hot|xo+)\b", re.I)
HASHTAG = re.compile(r"#[\wЀ-ӿĀ-ſ]+")


def _decorative_ratio(name: str) -> float:
    """Udział ozdobnych znaków (emoji, symbole, „fantazyjne” litery) w nazwie kanału."""
    if not name:
        return 0.0
    odd = 0
    letters = 0
    for ch in name:
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            letters += 1
            # matematyczne/ozdobne odmiany liter (𝓐, 𝔸, ᴀ…) — typowe dla spamu
            if "MATHEMATICAL" in unicodedata.name(ch, "") or "SMALL CAPITAL" in unicodedata.name(ch, ""):
                odd += 1
        elif cat in ("So", "Sk", "Sm", "Mn", "Me", "Cf") or ch in "︎️":
            odd += 1
    total = odd + letters
    return odd / total if total else 0.0


def _scripts(text: str) -> set[str]:
    s = set()
    for ch in text:
        if not ch.isalpha():
            continue
        n = unicodedata.name(ch, "")
        for script in ("LATIN", "CYRILLIC", "ARABIC", "DEVANAGARI", "CJK", "HANGUL", "THAI", "GREEK", "HEBREW",
                       "BENGALI", "GURMUKHI", "MALAYALAM", "TAMIL"):
            if n.startswith(script):
                s.add(script)
                break
    return s


def hashtag_wall(text: str) -> bool:
    """Prawie sam ciąg hashtagów (np. „#tieling #kraków #haddiyan”), bez prawdziwej treści."""
    tags = HASHTAG.findall(text or "")
    if len(tags) < 3:
        return False
    rest = HASHTAG.sub(" ", text or "")
    rest = re.sub(r"\[(?:zdjęcie|wideo|GIF|plik:[^\]]*)\]|[\W\d_]+", " ", rest)
    words = [w for w in rest.split() if len(w) > 2]
    return len(words) <= 2


def classify(text: str, chat_name: str = "", sender: str = "", media: str | None = None,
             public_origin: bool = False) -> tuple[str | None, str]:
    """Ocena bez AI. public_origin=True dla wyników z kanałów, do których nie należysz
    (tam spamu jest najwięcej, więc reguły są ostrzejsze)."""
    t = text or ""
    names = f"{chat_name} {sender}"
    if ADULT.search(t) or ADULT.search(names):
        return "dorosli", "słowa typowe dla treści dla dorosłych"
    if SCAM.search(t) or SCAM.search(names):
        return "spam", "kasyno / narkotyki / „zarobek” / oszustwo"
    if not public_origin:
        return None, ""
    tags = HASHTAG.findall(t)
    wall = hashtag_wall(t)
    deco = _decorative_ratio(chat_name)
    scripts = _scripts(" ".join(tags))
    if wall and (media in ("photo", "video", "gif") or deco > 0.15):
        return "spam", "obrazek + ściana przypadkowych hashtagów"
    if wall and len(scripts) >= 2:
        return "spam", "hashtagi w kilku alfabetach naraz (dopychanie do wyników)"
    if TITLE_BAIT.search(chat_name or "") and (wall or media in ("photo", "video")):
        return "spam", "nazwa kanału typowa dla spamu"
    if PERSONA.search(chat_name or "") and media in ("photo", "video", "gif") and tags:
        return "spam", "konto-persona („Imię Online”) z obrazkiem i hashtagami"
    if deco > 0.3 and (wall or len(tags) >= 3):
        return "spam", "ozdobna nazwa kanału + hashtagi"
    return None, ""


def is_public_origin(chat_type: str | None) -> bool:
    """Wynik pochodzi z kanału/grupy spoza Twoich czatów (tryb Cały Telegram)."""
    ct = chat_type or ""
    return "wszystkie publiczne" in ct or "znaleziony po nazwie" in ct


HIDDEN_CATEGORIES = {"spam", "dorosli", "oszustwo"}
LABELS = {"news": "📰 news", "wiadomosc": "💬 rozmowa", "reklama": "📣 reklama", "spam": "🗑 spam",
          "dorosli": "🔞 18+", "oszustwo": "⚠ oszustwo", "inne": "• inne"}
