@echo off
rem Szperacz - uruchamianie dwuklikiem (otwiera okienko).
rem Pierwsze uruchomienie / aktualizacja: przygotowuje srodowisko .venv.
rem Z poleceniem dziala w konsoli, np.:  Szperacz.bat szukaj anten*
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "DEPS=.venv\szperacz-deps-3.txt"

if not exist ".venv\Scripts\szperacz-gui.exe" goto setup
if not exist "%DEPS%" goto install
goto run

:setup
echo Przygotowuje srodowisko (tylko za pierwszym razem)...
if exist ".venv\Scripts\python.exe" goto install
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo Nie znaleziono Pythona. Zainstaluj Python 3.11 lub nowszy z https://www.python.org
    echo i zaznacz przy instalacji "Add python.exe to PATH".
    pause
    exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo Potrzebny Python 3.11 lub nowszy. Masz:
    %PY% --version
    pause
    exit /b 1
)
%PY% -m venv .venv || goto fail

:install
echo Instaluje / aktualizuje biblioteki...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -e . || goto fail
echo ok> "%DEPS%"
echo.
echo Srodowisko gotowe.

:run
if "%~1"=="" (
    start "" ".venv\Scripts\szperacz-gui.exe"
    exit /b 0
)
".venv\Scripts\szperacz.exe" %*
exit /b %errorlevel%

:fail
echo.
echo Instalacja nie powiodla sie - sprawdz komunikat powyzej.
pause
exit /b 1
