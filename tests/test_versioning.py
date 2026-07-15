"""Versionierung (Major.Minor.Patch): gespeicherte DB-Version, Major-Vergleich
mit dem Code, Sperre bei Mismatch und die explizite migrate()-Route/-CLI.

Siehe db.py (APP_VERSION, db_version, is_compatible, migrate) und
app.py (_require_matching_schema, /migration).
"""

import app as app_module
import db
from conftest import old_schema_db, scalar


# --------------------------------------------------------- Version parsen

def test_major_extrahiert_erste_zahl():
    assert db.major("2.3.4") == 2
    assert db.major("10.0.0") == 10


def test_major_ist_none_bei_fehlender_oder_kaputter_version():
    assert db.major(None) is None
    assert db.major("") is None
    assert db.major("keine-zahl.0.0") is None


# ------------------------------------------------------- init_db / Version

def test_init_db_stempelt_frische_db_auf_app_version(initialized_db):
    with db.db_session() as conn:
        assert db.db_version(conn) == db.APP_VERSION
        assert db.is_compatible(conn)


def test_alte_db_ohne_schema_meta_ist_inkompatibel(isolated_db):
    old_schema_db(isolated_db)  # keine schema_meta-Tabelle, kein init_db()

    with db.db_session() as conn:
        assert db.db_version(conn) is None
        assert not db.is_compatible(conn)


def test_migrate_macht_alte_db_wieder_kompatibel(isolated_db):
    old_schema_db(isolated_db)
    db.migrate()

    with db.db_session() as conn:
        assert db.db_version(conn) == db.APP_VERSION
        assert db.is_compatible(conn)


def test_falsche_major_version_wird_als_inkompatibel_erkannt(initialized_db):
    with db.db_session() as conn:
        db._set_version(conn, "99.0.0")

    with db.db_session() as conn:
        assert not db.is_compatible(conn)


# -------------------------------------------------- Sperre ueber before_request

def test_kompatible_db_laesst_seiten_normal_durch(client):
    assert client.get("/").status_code == 200


def test_falsche_major_version_sperrt_alle_seiten(client):
    with db.db_session() as conn:
        db._set_version(conn, "99.0.0")

    resp = client.get("/")
    assert resp.status_code == 409
    assert "Migration erforderlich" in resp.get_data(as_text=True)


def test_migrations_route_bleibt_bei_falscher_version_erreichbar(client):
    with db.db_session() as conn:
        db._set_version(conn, "99.0.0")

    assert client.get("/migration").status_code == 200


def test_migration_ueber_die_route_hebt_die_sperre_wieder_auf(client):
    with db.db_session() as conn:
        db._set_version(conn, "99.0.0")
    assert client.get("/").status_code == 409

    resp = client.post("/migration")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"

    assert client.get("/").status_code == 200
    with db.db_session() as conn:
        assert db.db_version(conn) == db.APP_VERSION


def test_migration_ueber_alte_db_end_to_end(isolated_db):
    """Deckt den vollen Ablauf ab: alte DB -> 409 auf jeder Seite -> nur
    /migration erreichbar -> POST migriert -> Ersteinrichtung noetig (die
    migrierte Alt-DB hat noch keine Nutzer) -> danach App wieder frei, mit
    den Alt-Buchungen dem neuen Admin zugeordnet."""
    old_schema_db(isolated_db)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/buchungen")
        assert resp.status_code == 409

        assert c.get("/migration").status_code == 200

        resp = c.post("/migration")
        assert resp.status_code == 302

        # Nach der Schema-Migration greift das Setup-Gate, da die alte DB
        # keine users-Tabelle mit Eintraegen hatte.
        resp = c.get("/buchungen")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/ersteinrichtung"

        resp = c.post("/ersteinrichtung", data={
            "username": "admin", "password": "sicheres-passwort",
            "password_confirm": "sicheres-passwort",
        })
        assert resp.status_code == 302

        assert c.get("/buchungen").status_code == 200
        assert scalar("SELECT name FROM accounts WHERE id = 1") == "Altkonto"
        # Die verwaisten Alt-Daten wurden dem neuen Admin zugeordnet.
        assert scalar("SELECT user_id FROM accounts WHERE id = 1") == scalar(
            "SELECT id FROM users WHERE username = 'admin'"
        )
