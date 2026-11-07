# Historia zmian

## v1.1.0 — 2026-11-07

#### Nowe
- **Zdjęcia w podglądzie**: zdjęcia, miniatury wideo, plików i naklejek pobierane na żądanie (cache `dane/media`).
- **Pełne opisy podglądów linków**: strona, tytuł, cały opis i adres. Klikalne linki w treści wiadomości.
- **Filtry**: okres (dziś, wczoraj, 7/30 dni, ten/poprzedni miesiąc, 3 miesiące, ten/poprzedni rok),
  pora dnia (także „przez północ”, np. 22–5), rodzaj czatu, zawartość (tekst, zdjęcia, wideo, pliki, linki,
  audio, ankiety, lokalizacje), moje / od innych, sortowanie (trafność / najnowsze / najstarsze), limit.
  Działają w oknie, w konsoli (`--tresc`, `--typ`, `--godz`, `--moje`, `--najstarsze`) i w trybie Cały Telegram.
- **Wbudowana przeglądarka** (pywebview, Edge WebView2): jedno okno dla wszystkich linków, menu nawigacji,
  zapamiętane logowania, publiczne posty w pełnym podglądzie, prywatne w aplikacji Telegram.
- Ikony rodzaju treści na liście wyników (📷 🎬 📎 🔗 📊 📍).
- Ankiety i lokalizacje są przeszukiwalne (pytanie i odpowiedzi, nazwa miejsca).

#### Zmiany
- Indeks zapisuje rodzaj mediów, opis linku i to, czy wiadomość jest Twoja. Istniejąca baza jest
  uzupełniana automatycznie, a starsze wiadomości dostają te dane przy pierwszym podglądzie.
- `Szperacz.bat` sam doinstaluje nowe biblioteki (Pillow, pywebview).

## v1.0.0 — 2026-11-07 — pierwsze wydanie

Wyszukiwarka wiadomości z Telegrama z pytaniami po ludzku. Program **tylko czyta**: niczego nie wysyła, nie edytuje i nie usuwa.

#### Najważniejsze
- **Lokalny indeks** (SQLite FTS5): błyskawiczne wyszukiwanie offline. Polskie znaki nie mają znaczenia (`lukasz` znajdzie „Łukasz”), a gwiazdka łapie odmianę (`anten*` → antenie, antenna).
- **✦ Zapytaj AI** (Claude): pytasz po ludzku, dostajesz odpowiedź z klikalnymi źródłami [1] [2]. Daty względne („w zeszłym tygodniu”) są rozwiązywane automatycznie.
- **🌐 Cały Telegram**: szukanie na żywo we wszystkich publicznych kanałach (Telegram Premium, darmowy dzienny limit), w publicznych grupach znalezionych po nazwie i we wszystkich Twoich czatach. `#hashtagi` bez limitu. Gwiazdki (Stars) nigdy nie schodzą bez Twojej zgody.
- **＋ Obserwuj**: publiczne kanały trafiają do indeksu bez dołączania.
- **Logowanie kodem QR**: skan telefonem w Telegramie (Ustawienia → Urządzenia → Połącz urządzenie). Alternatywnie kod, z informacją, gdzie Telegram go wysłał, i opcją „wyślij inaczej”.
- **Okno** z ciemnym motywem, **wiersz poleceń** (`szperacz szukaj / pytaj / wszedzie / sync`) i **exe dla Windows** (`zbuduj_exe.bat`).
- Przyrostowa synchronizacja: kolejne uruchomienia pobierają tylko nowe wiadomości.

#### Instalacja (Windows)
1. Zainstaluj Python 3.11+ z python.org (zaznacz „Add python.exe to PATH”).
2. Pobierz **Source code (zip)** poniżej i rozpakuj.
3. Kliknij dwa razy **`Szperacz.bat`**. Za pierwszym razem zainstaluje biblioteki.
4. W Ustawieniach wpisz `api_id` i `api_hash` z [my.telegram.org](https://my.telegram.org), potem **Zaloguj** → **⟳ Synchronizuj**.
5. Opcjonalnie: `zbuduj_exe.bat` → `Szperacz.exe` (Python już niepotrzebny).

Szczegóły w [README](https://github.com/vipersq/szperacz#readme).

#### Znane ograniczenia
- Edycje starszych wiadomości nie są jeszcze wykrywane.
- Pliki i zdjęcia są indeksowane po podpisie i nazwie pliku.
- Prywatnych grup i kanałów, w których Cię nie ma, nie da się przeszukać (ograniczenie Telegrama).

#### Dalej
Kolejne komunikatory: Signal, WhatsApp (import eksportu), Messenger, Discord.
