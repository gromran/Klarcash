"""Schema-Anlage und Migration alter Datenbanken (db.init_db / db._migrate).

Pflichtfall 1 aus PROJECT.md: eine bestehende ausgaben.db aus einer aelteren
Version muss beim Start automatisch mitwandern, ohne Daten zu verlieren.
"""

import db
from conftest import fetchall, fetchone, old_schema_db, scalar


def _columns(table):
    return [r["name"] for r in fetchall(f"PRAGMA table_info({table})")]


def _tables():
    return [r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")]


# ------------------------------------------------- frische Datenbank

def test_init_db_legt_alle_tabellen_an(initialized_db):
    for table in ("accounts", "categories", "transactions", "transaction_items"):
        assert table in _tables()


def test_init_db_legt_standardkategorien_an(initialized_db):
    namen = {r["name"] for r in fetchall("SELECT name FROM categories")}
    assert namen == {
        "Gehalt", "Sonstige Einnahmen", "Miete", "Lebensmittel",
        "Versicherung", "Freizeit", "Sonstige Ausgaben",
    }


def test_init_db_legt_keine_konten_an(initialized_db):
    # Bewusste Entscheidung (db.py Docstring): der erste Kontostand soll eine
    # explizite Nutzereingabe sein, kein geratener Platzhalter.
    assert scalar("SELECT COUNT(*) FROM accounts") == 0


def test_init_db_ist_idempotent(initialized_db):
    db.init_db()
    db.init_db()
    assert scalar("SELECT COUNT(*) FROM categories") == 7


def test_init_db_legt_indizes_an(initialized_db):
    indizes = {r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_tx_date", "idx_tx_account", "idx_tx_deleted",
            "idx_cat_parent", "idx_items_tx"} <= indizes


def test_init_db_ueberschreibt_vorhandene_kategorien_nicht(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        conn.execute("DELETE FROM categories WHERE name != 'Miete'")
    db.init_db()

    # Nur eine leere Tabelle wird neu befuellt - eine bereits genutzte nicht.
    assert scalar("SELECT COUNT(*) FROM categories") == 1


# ------------------------------------------------- Migration alter DB

def test_migration_alter_datenbank_crasht_nicht(isolated_db):
    old_schema_db(isolated_db)
    db.init_db()  # darf nicht werfen

    assert "deleted" in _columns("transactions")
    assert "deleted_at" in _columns("transactions")
    assert "parent_id" in _columns("categories")
    assert "transaction_items" in _tables()


def test_migration_erhaelt_altdaten(isolated_db):
    old_schema_db(isolated_db)
    db.init_db()

    tx = fetchone("SELECT * FROM transactions WHERE description = 'Alte Buchung'")
    assert tx["amount"] == 25.0
    assert tx["deleted"] == 0  # Default greift fuer Altzeilen
    assert tx["deleted_at"] is None

    kat = fetchone("SELECT * FROM categories WHERE name = 'Altkategorie'")
    assert kat["parent_id"] is None
    assert scalar("SELECT name FROM accounts WHERE id = 1") == "Altkonto"


def test_migration_legt_indizes_auf_neuen_spalten_an(isolated_db):
    # idx_tx_deleted setzt die erst von _migrate ergaenzte Spalte voraus.
    # Der Test sichert damit die Reihenfolge SCHEMA -> _migrate -> INDEXES ab.
    old_schema_db(isolated_db)
    db.init_db()

    indizes = {r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_tx_deleted" in indizes
    assert "idx_cat_parent" in indizes


def test_migration_fuegt_standardkategorien_nicht_hinzu(isolated_db):
    # Die alte DB hat bereits eine Kategorie -> init_db darf nicht nachlegen.
    old_schema_db(isolated_db)
    db.init_db()

    assert scalar("SELECT COUNT(*) FROM categories") == 1


def test_migration_ist_wiederholbar(isolated_db):
    old_schema_db(isolated_db)
    db.init_db()
    db.init_db()  # zweiter Start darf die bereits migrierte DB nicht beschaedigen

    assert _columns("transactions").count("deleted") == 1
    assert scalar("SELECT COUNT(*) FROM transactions") == 1


def test_migrierte_db_ist_voll_nutzbar(isolated_db):
    """Nach der Migration muessen Posten und Papierkorb sofort funktionieren."""
    old_schema_db(isolated_db)
    db.init_db()

    with db.db_session() as conn:
        db.replace_items(conn, 1, [("Brot", 3.0, None), ("Pfand", -0.25, None)])
        conn.execute("UPDATE transactions SET deleted = 1 WHERE id = 1")

    assert scalar("SELECT COUNT(*) FROM transaction_items WHERE transaction_id = 1") == 2
    with db.db_session() as conn:
        # Buchung ist im Papierkorb -> Saldo wieder nur der Anfangsbestand.
        assert db.account_balance(conn, 1) == 100.0
