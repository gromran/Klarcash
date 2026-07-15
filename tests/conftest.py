"""Gemeinsame Fixtures fuer die Testsuite.

Wichtigster Punkt: kein Test darf jemals die echte ausgaben.db anfassen.
db.DB_PATH wird deshalb per autouse-Fixture fuer JEDEN Test auf eine
Wegwerf-Datei in tmp_path umgebogen - auch fuer Tests, die die DB-Fixtures
gar nicht anfordern. Das funktioniert zuverlaessig, weil db.get_connection()
die Konstante bei jedem Aufruf frisch aus dem Modulnamensraum liest und
app.py das Modul als "db" importiert (nie "from db import get_connection").

Zusaetzlich prueft eine session-weite Fixture am Ende, dass Groesse und
Aenderungsdatum der echten DB unveraendert geblieben sind.
"""

import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

import app as app_module
import db

REAL_DB = Path(db.__file__).parent / "ausgaben.db"


def _fingerprint(path):
    """(Groesse, mtime) einer Datei - oder None, wenn sie nicht existiert."""
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@pytest.fixture(scope="session", autouse=True)
def _real_db_untouched():
    """Beweist ueber den gesamten Lauf, dass die echte DB unberuehrt bleibt."""
    before = _fingerprint(REAL_DB)
    yield
    assert _fingerprint(REAL_DB) == before, (
        f"Die echte Datenbank {REAL_DB} wurde von der Testsuite veraendert!"
    )


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Leitet alle DB-Zugriffe dieses Tests auf eine Wegwerf-Datei um."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    assert db.DB_PATH != REAL_DB
    return path


@pytest.fixture
def initialized_db(isolated_db):
    """Wegwerf-DB mit angelegtem Schema, einem Standard-Testnutzer (Admin)
    und dessen 7 Standardkategorien.

    Seit der Nutzerverwaltung (Version 2.0.0) seedet init_db() selbst keine
    Kategorien mehr (das ist Aufgabe der Ersteinrichtung fuer den ersten
    Nutzer) - diese Fixture uebernimmt das hier fuer die Testsuite, damit
    bestehende Tests (Factory.category_id() etc.) unveraendert funktionieren."""
    db.init_db()
    with db.db_session() as conn:
        uid = db.create_user(conn, "test", "not-a-real-hash", role="admin")
        db.seed_categories_for_user(conn, uid)
    return isolated_db


@pytest.fixture
def test_user_id(initialized_db):
    """ID des von initialized_db angelegten Test-Admins."""
    return scalar("SELECT id FROM users WHERE username = 'test'")


@pytest.fixture
def client(initialized_db):
    """Test-Client, bereits als der von initialized_db angelegte Test-Admin
    eingeloggt - sonst wuerde jede Route seit der Nutzerverwaltung (2.0.0)
    ueber _require_login auf /login umgeleitet."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with db.db_session() as conn:
            user = db.get_user_by_username(conn, "test")
        with c.session_transaction() as s:
            s["user_id"] = user["id"]
        yield c


class Factory:
    """Legt Testdaten per direktem SQL an - schnell und praezise.

    Routen-POSTs werden nur dort verwendet, wo die Route selbst unter Test
    steht; zum blossen Herstellen eines Ausgangszustands waeren sie unnoetig
    umstaendlich und wuerden fremde Validierungslogik mittesten.

    default_user_id ist der von initialized_db angelegte Test-Admin - alle
    Methoden setzen ihn als user_id, sofern nicht explizit ein anderer
    Nutzer (Parameter user) uebergeben wird (fuer Isolations-Tests mit
    mehreren Nutzern, siehe test_users.py).
    """

    def __init__(self, default_user_id):
        self.default_user_id = default_user_id

    def account(self, name="Girokonto", type="Konto", initial=0.0, archived=0, user=None):
        with db.db_session() as conn:
            cur = conn.execute(
                "INSERT INTO accounts (name, type, initial_balance, archived, user_id) VALUES (?, ?, ?, ?, ?)",
                (name, type, initial, archived, user or self.default_user_id),
            )
            return cur.lastrowid

    def category(self, name, kind="Ausgabe", parent=None, user=None):
        with db.db_session() as conn:
            cur = conn.execute(
                "INSERT INTO categories (name, kind, parent_id, user_id) VALUES (?, ?, ?, ?)",
                (name, kind, parent, user or self.default_user_id),
            )
            return cur.lastrowid

    def tx(self, account, type="Ausgabe", amount=10.0, date="2026-07-01",
           category=None, target=None, description="", deleted=0, user=None):
        with db.db_session() as conn:
            cur = conn.execute(
                """
                INSERT INTO transactions
                    (date, account_id, type, category_id, description, amount,
                     target_account_id, deleted, deleted_at, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (date, account, type, category, description, amount, target,
                 deleted, "2026-07-01 12:00:00" if deleted else None, user or self.default_user_id),
            )
            return cur.lastrowid

    def items(self, tx_id, items):
        """items: Liste von (description, amount, category_id)-Tupeln."""
        with db.db_session() as conn:
            db.replace_items(conn, tx_id, items)

    def category_id(self, name):
        """ID einer (Standard-)Kategorie anhand ihres Namens (Test-Admin)."""
        return scalar(
            "SELECT id FROM categories WHERE name = ? AND user_id = ?",
            (name, self.default_user_id),
        )

    def user(self, username, role="user"):
        """Legt einen weiteren Nutzer an (Passwort irrelevant fuer Tests, die
        nicht ueber /login gehen) und seedet dessen Standardkategorien."""
        with db.db_session() as conn:
            uid = db.create_user(conn, username, "not-a-real-hash", role=role)
            db.seed_categories_for_user(conn, uid)
            return uid


@pytest.fixture
def make(initialized_db):
    uid = scalar("SELECT id FROM users WHERE username = 'test'")
    return Factory(uid)


# ------------------------------------------------------- Query-Helfer


def fetchall(sql, params=()):
    with db.db_session() as conn:
        return conn.execute(sql, params).fetchall()


def fetchone(sql, params=()):
    with db.db_session() as conn:
        return conn.execute(sql, params).fetchone()


def scalar(sql, params=()):
    row = fetchone(sql, params)
    return row[0] if row else None


def balance(account_id, user_id=None):
    with db.db_session() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT user_id FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            user_id = row["user_id"] if row else None
        return db.account_balance(conn, account_id, user_id)


def total(user_id):
    with db.db_session() as conn:
        return db.total_balance(conn, user_id)


def form(**kwargs):
    """Baut einen MultiDict fuer die Unit-Tests der _validate_*-Helper.

    Die Helper erwarten ein Werkzeug-Formularobjekt (.get(key, type=int),
    .getlist(key)), kein plain dict. Listenwerte werden zu mehrfachen
    Eintraegen desselben Schluessels expandiert (item_amount etc.).
    """
    md = MultiDict()
    for key, value in kwargs.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                md.add(key, str(v))
        else:
            md.add(key, str(value))
    return md


def old_schema_db(path):
    """Baut eine "alte" Datenbank nach: transactions ohne deleted/deleted_at,
    categories ohne parent_id, transaction_items existiert noch gar nicht.
    Entspricht dem Stand vor Papierkorb, Unterkategorien und Kassenzettel."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('Konto', 'Bar', 'Anlage')),
            initial_balance REAL NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('Einnahme', 'Ausgabe'))
        );
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            type TEXT NOT NULL CHECK(type IN ('Einnahme', 'Ausgabe', 'Umbuchung')),
            category_id INTEGER REFERENCES categories(id),
            description TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL CHECK(amount > 0),
            target_account_id INTEGER REFERENCES accounts(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        "INSERT INTO accounts (name, type, initial_balance) VALUES ('Altkonto', 'Konto', 100.0)"
    )
    conn.execute("INSERT INTO categories (name, kind) VALUES ('Altkategorie', 'Ausgabe')")
    conn.execute(
        """
        INSERT INTO transactions (date, account_id, type, category_id, description, amount)
        VALUES ('2025-01-15', 1, 'Ausgabe', 1, 'Alte Buchung', 25.0)
        """
    )
    conn.commit()
    conn.close()
