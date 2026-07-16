"""Schema-Anlage (db.init_db) und explizite Migration (db.migrate) alter
Datenbanken.

Eine bestehende ausgaben.db aus einer aelteren Version wandert NICHT mehr
automatisch beim Start mit - init_db() ruehrt eine bestehende DB nicht an
(siehe db.py-Docstrings). Erst der bewusste Aufruf von db.migrate() (CLI
--migrate oder Route /migration) hebt das Schema an, ohne Daten zu verlieren.
"""

import db
from conftest import fetchall, fetchone, old_schema_db, scalar


def _columns(table):
    return [r["name"] for r in fetchall(f"PRAGMA table_info({table})")]


def _tables():
    return [r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")]


# ------------------------------------------------- frische Datenbank

def test_init_db_legt_alle_tabellen_an(initialized_db):
    for table in ("users", "accounts", "categories", "transactions", "transaction_items"):
        assert table in _tables()


def test_init_db_seedet_keine_standardkategorien(isolated_db):
    # Seit der Nutzerverwaltung (2.0.0) seedet init_db() selbst nichts mehr -
    # das ist erst sinnvoll, sobald ein Nutzer existiert (Ersteinrichtung ruft
    # dafuer seed_categories_for_user() auf). Die initialized_db-Fixture legt
    # fuer die Testsuite selbst einen Nutzer inkl. Kategorien an, siehe conftest.
    db.init_db()
    assert scalar("SELECT COUNT(*) FROM categories") == 0


def test_init_db_legt_keine_konten_an(initialized_db):
    # Bewusste Entscheidung (db.py Docstring): der erste Kontostand soll eine
    # explizite Nutzereingabe sein, kein geratener Platzhalter.
    assert scalar("SELECT COUNT(*) FROM accounts") == 0


def test_init_db_ist_idempotent(isolated_db):
    db.init_db()
    db.init_db()
    assert "transactions" in _tables()
    assert scalar("SELECT COUNT(*) FROM categories") == 0


def test_init_db_legt_indizes_an(initialized_db):
    indizes = {r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_tx_date", "idx_tx_account", "idx_tx_deleted", "idx_tx_user",
            "idx_cat_parent", "idx_cat_user", "idx_items_tx", "idx_accounts_user"} <= indizes


# ------------------------------------------------- Nutzer & Kategorien-Seeding

def test_seed_categories_for_user_ist_je_nutzer_getrennt(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        u1 = db.create_user(conn, "anna", "hash1")
        u2 = db.create_user(conn, "bert", "hash2")
        db.seed_categories_for_user(conn, u1)
        db.seed_categories_for_user(conn, u2)

    # Beide Nutzer duerfen je eine gleichnamige "Miete"-Kategorie haben.
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Miete'") == 2
    assert scalar("SELECT COUNT(*) FROM categories") == 14


def test_seed_categories_for_user_ist_idempotent(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash1")
        db.seed_categories_for_user(conn, uid)
        db.seed_categories_for_user(conn, uid)  # zweiter Aufruf darf nicht duplizieren

    assert scalar("SELECT COUNT(*) FROM categories") == 7


def test_claim_orphan_data_ordnet_verwaiste_zeilen_zu(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash1")
        conn.execute("INSERT INTO accounts (name, type) VALUES ('Altkonto', 'Konto')")
        conn.execute("INSERT INTO categories (name, kind) VALUES ('Altkategorie', 'Ausgabe')")
        db.claim_orphan_data(conn, uid)

    assert scalar("SELECT user_id FROM accounts WHERE name = 'Altkonto'") == 1
    assert scalar("SELECT user_id FROM categories WHERE name = 'Altkategorie'") == 1


def test_claim_orphan_data_vor_seeding_verhindert_duplikate(isolated_db):
    """Eine migrierte Alt-DB hatte schon eine 'Miete' - nach claim_orphan_data()
    darf seed_categories_for_user() keine zweite gleichnamige anlegen."""
    db.init_db()
    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash1")
        conn.execute("INSERT INTO categories (name, kind) VALUES ('Miete', 'Ausgabe')")
        db.claim_orphan_data(conn, uid)
        db.seed_categories_for_user(conn, uid)

    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Miete'") == 1


# --------------------------------------------- init_db() ruehrt Alt-DB nicht an

def test_init_db_migriert_alte_datenbank_nicht(isolated_db):
    # init_db() ist nur fuer FRISCHE Datenbanken zustaendig - eine bestehende
    # (alte) DB bleibt unangetastet, bis migrate() explizit laeuft.
    old_schema_db(isolated_db)
    db.init_db()  # darf nicht werfen, aendert aber nichts am Schema

    assert "deleted" not in _columns("transactions")
    assert "transaction_items" not in _tables()


# ------------------------------------------------- Migration alter DB (db.migrate)

def test_migration_alter_datenbank_crasht_nicht(isolated_db):
    old_schema_db(isolated_db)
    db.migrate()  # darf nicht werfen

    assert "deleted" in _columns("transactions")
    assert "deleted_at" in _columns("transactions")
    assert "user_id" in _columns("transactions")
    assert "user_id" in _columns("accounts")
    assert "parent_id" in _columns("categories")
    assert "user_id" in _columns("categories")
    assert "users" in _tables()
    assert "transaction_items" in _tables()
    assert "settings" in _tables()


def test_migration_legt_settings_tabelle_an(isolated_db):
    old_schema_db(isolated_db)  # kennt "settings" noch gar nicht
    db.migrate()

    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash1")
        assert db.get_settings(conn, uid) == {}
        db.set_setting(conn, uid, "bg_color", "#112233")
        assert db.get_settings(conn, uid) == {"bg_color": "#112233"}


def test_migration_erhaelt_altdaten(isolated_db):
    old_schema_db(isolated_db)
    db.migrate()

    tx = fetchone("SELECT * FROM transactions WHERE description = 'Alte Buchung'")
    assert tx["amount"] == 25.0
    assert tx["deleted"] == 0  # Default greift fuer Altzeilen
    assert tx["deleted_at"] is None
    assert tx["user_id"] is None  # verwaist, bis claim_orphan_data() laeuft

    kat = fetchone("SELECT * FROM categories WHERE name = 'Altkategorie'")
    assert kat["parent_id"] is None
    assert kat["user_id"] is None
    assert tx["category_id"] == kat["id"]  # FK bleibt nach dem Rebuild gueltig
    assert scalar("SELECT name FROM accounts WHERE id = 1") == "Altkonto"


def test_migration_categories_rebuild_erlaubt_gleichnamige_kategorien_je_nutzer(isolated_db):
    # Kern des Rebuilds: UNIQUE(name) -> UNIQUE(user_id, name).
    old_schema_db(isolated_db)
    db.migrate()

    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash1")
        db.claim_orphan_data(conn, uid)  # "Altkategorie" gehoert jetzt anna
        conn.execute(
            "INSERT INTO categories (name, kind, user_id) VALUES ('Altkategorie', 'Ausgabe', NULL)"
        )  # ein zweiter Nutzer (hier: keiner/NULL) darf denselben Namen haben

    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Altkategorie'") == 2
    assert "categories_old" not in _tables()


def test_migration_legt_indizes_auf_neuen_spalten_an(isolated_db):
    # idx_tx_deleted setzt die erst von _migrate ergaenzte Spalte voraus.
    # Der Test sichert damit die Reihenfolge SCHEMA -> _migrate -> INDEXES ab.
    old_schema_db(isolated_db)
    db.migrate()

    indizes = {r["name"] for r in fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_tx_deleted" in indizes
    assert "idx_tx_user" in indizes
    assert "idx_cat_parent" in indizes
    assert "idx_cat_user" in indizes
    assert "idx_accounts_user" in indizes


def test_migration_fuegt_standardkategorien_nicht_hinzu(isolated_db):
    # Die alte DB hat bereits eine Kategorie -> migrate() darf nicht nachlegen.
    old_schema_db(isolated_db)
    db.migrate()

    assert scalar("SELECT COUNT(*) FROM categories") == 1


def test_migration_ist_wiederholbar(isolated_db):
    old_schema_db(isolated_db)
    db.migrate()
    db.migrate()  # zweiter Aufruf darf die bereits migrierte DB nicht beschaedigen

    assert _columns("transactions").count("deleted") == 1
    assert scalar("SELECT COUNT(*) FROM transactions") == 1
    assert scalar("SELECT COUNT(*) FROM categories") == 1
    assert "categories_old" not in _tables()


def test_migration_stempelt_db_version(isolated_db):
    old_schema_db(isolated_db)
    db.migrate()

    with db.db_session() as conn:
        assert db.db_version(conn) == db.APP_VERSION
        assert db.is_compatible(conn)


def test_migrierte_db_ist_voll_nutzbar(isolated_db):
    """Nach der Migration und der Ersteinrichtung (die die verwaisten
    Alt-Daten uebernimmt) muessen Posten und Papierkorb sofort funktionieren."""
    old_schema_db(isolated_db)
    db.migrate()

    with db.db_session() as conn:
        uid = db.create_user(conn, "anna", "hash", role="admin")
        db.claim_orphan_data(conn, uid)
        db.replace_items(conn, 1, [("Brot", 3.0, None), ("Pfand", -0.25, None)])
        conn.execute("UPDATE transactions SET deleted = 1 WHERE id = 1")

    assert scalar("SELECT COUNT(*) FROM transaction_items WHERE transaction_id = 1") == 2
    with db.db_session() as conn:
        # Buchung ist im Papierkorb -> Saldo wieder nur der Anfangsbestand.
        assert db.account_balance(conn, 1, uid) == 100.0
