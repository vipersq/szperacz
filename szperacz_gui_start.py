"""Punkt startowy dla Szperacz.exe z okienkiem (PyInstaller --windowed).

Ten sam plik exe uruchamia też okno wbudowanej przeglądarki (parametr --przegladarka).
"""
import sys

if __name__ == "__main__":
    if "--przegladarka" in sys.argv:
        from szperacz.browser import main as browser_main
        raise SystemExit(browser_main())
    from szperacz.gui import main
    main()
