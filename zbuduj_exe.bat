@echo off
rem Buduje dwa pliki:
rem   Szperacz.exe     - okienko (dwuklik)
rem   SzperaczCLI.exe  - wersja konsolowa (polecenia: sync, szukaj, pytaj...)
rem Najpierw uruchom raz Szperacz.bat, zeby powstalo srodowisko .venv.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Najpierw uruchom Szperacz.bat - przygotuje srodowisko.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install pyinstaller || goto fail

echo.
echo [1/2] Szperacz.exe (okienko)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Szperacz --collect-submodules telethon --hidden-import qrcode ^
    --collect-all webview --collect-all clr_loader --hidden-import clr --hidden-import PIL._tkinter_finder ^
    szperacz_gui_start.py || goto fail

echo.
echo [2/2] SzperaczCLI.exe (konsola)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --console ^
    --name SzperaczCLI --collect-submodules telethon --hidden-import qrcode ^
    szperacz_start.py || goto fail

copy /y "dist\Szperacz.exe" "Szperacz.exe" >nul
copy /y "dist\SzperaczCLI.exe" "SzperaczCLI.exe" >nul
echo.
echo Gotowe:
echo   %~dp0Szperacz.exe      - okienko
echo   %~dp0SzperaczCLI.exe   - konsola, np. do Harmonogramu zadan: SzperaczCLI.exe sync
echo Oba uzywaja config.toml i folderu "dane" z folderu, w ktorym leza.
pause
exit /b 0

:fail
echo.
echo Budowanie nie powiodlo sie - sprawdz komunikat powyzej.
pause
exit /b 1
