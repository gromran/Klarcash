"""Windows-Desktop-Launcher fuer Klarcash.

Startet den bestehenden Flask-Server in einem Hintergrund-Thread und zeigt
ihn in einem nativen Fenster (pywebview, nutzt Windows WebView2/Edge) an -
analog zur Android-App, die denselben Server per Chaquopy-WebView anzeigt.

Start aus dem Quellcode: python desktop.py
Als gepacktes .exe: siehe klarcash.spec (PyInstaller).
"""

import os
import socket
import threading
from pathlib import Path

import webview

import db

# Muss VOR "import app" gesetzt werden: app.py leitet SECRET_KEY_PATH beim
# Import aus db.DB_PATH ab (app.py:26). Bei einer Ueberschreibung erst nach
# dem Import wuerde der Secret-Key weiterhin neben dem alten Pfad landen.
_DATA_DIR = Path(os.environ["APPDATA"]) / "Klarcash"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
db.DB_PATH = _DATA_DIR / "ausgaben.db"

import app  # noqa: E402  (Import bewusst erst nach dem DB_PATH-Override)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, attempts: int = 50, delay: float = 0.1) -> None:
    for _ in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=delay):
                return
        except OSError:
            threading.Event().wait(delay)
    raise RuntimeError("Klarcash-Server ist nicht rechtzeitig gestartet.")


def main() -> None:
    port = _free_port()
    server_thread = threading.Thread(
        target=app.start, args=("127.0.0.1", port), daemon=True
    )
    server_thread.start()
    _wait_for_server(port)

    webview.create_window("Klarcash", f"http://127.0.0.1:{port}/", width=1200, height=800)
    # Primaerquelle des .exe-/Taskleisten-Icons ist icon= im EXE-Block von
    # klarcash.spec; dieser Parameter deckt zusaetzlich den Start aus dem
    # Quellcode ab (python desktop.py) und wirkt fuer Source- UND
    # PyInstaller-Builds gleichermassen, da app._RESOURCE_DIR beide Faelle
    # unterscheidet (siehe app.py).
    _icon_path = app._RESOURCE_DIR / "static" / "icons" / "klarcash.ico"
    webview.start(icon=str(_icon_path) if _icon_path.exists() else None)


if __name__ == "__main__":
    main()
