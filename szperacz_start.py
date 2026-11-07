"""Punkt startowy dla SzperaczCLI.exe (PyInstaller --console)."""
import os
import sys
from pathlib import Path

from szperacz.cli import main

if __name__ == "__main__":
    if getattr(sys, "frozen", False) and not Path("config.toml").exists():
        os.chdir(Path(sys.executable).parent)
    raise SystemExit(main())
