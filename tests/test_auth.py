"""Anmeldung, Ersteinrichtung und die beiden zugehoerigen before_request-Gates
(_require_setup, _require_login in app.py).

Nutzt bewusst eigene, UNANGEMELDETE Clients statt der client-Fixture (die ist
bereits als Test-Admin eingeloggt) - genau dieser Zustand vor dem Login soll
hier geprueft werden.
"""

import app as app_module
import db
from conftest import scalar


def _raw_client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ------------------------------------------------------- Setup-Gate

def test_ohne_nutzer_leitet_jede_seite_zur_ersteinrichtung(isolated_db):
    db.init_db()
    with _raw_client() as c:
        resp = c.get("/")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/ersteinrichtung"


def test_ersteinrichtung_ist_ohne_nutzer_ohne_login_erreichbar(isolated_db):
    db.init_db()
    with _raw_client() as c:
        assert c.get("/ersteinrichtung").status_code == 200


def test_ersteinrichtung_legt_admin_an_und_loggt_ein(isolated_db):
    db.init_db()
    with _raw_client() as c:
        resp = c.post("/ersteinrichtung", data={
            "username": "anna", "password": "sicheres-passwort",
            "password_confirm": "sicheres-passwort",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

        # Session ist gesetzt -> Dashboard sofort erreichbar, kein Redirect mehr.
        assert c.get("/").status_code == 200

    assert scalar("SELECT role FROM users WHERE username = 'anna'") == "admin"
    assert scalar("SELECT COUNT(*) FROM categories WHERE user_id = "
                   "(SELECT id FROM users WHERE username = 'anna')") == 7


def test_ersteinrichtung_lehnt_kurzes_passwort_ab(isolated_db):
    db.init_db()
    with _raw_client() as c:
        resp = c.post("/ersteinrichtung", data={
            "username": "anna", "password": "kurz", "password_confirm": "kurz",
        }, follow_redirects=True)

        assert "mindestens 8 Zeichen" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users") == 0


def test_ersteinrichtung_lehnt_abweichende_passwort_bestaetigung_ab(isolated_db):
    db.init_db()
    with _raw_client() as c:
        resp = c.post("/ersteinrichtung", data={
            "username": "anna", "password": "sicheres-passwort",
            "password_confirm": "anderes-passwort",
        }, follow_redirects=True)

        assert "stimmen nicht überein" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users") == 0


def test_ersteinrichtung_ordnet_verwaiste_altdaten_dem_admin_zu(isolated_db):
    """Simuliert eine gerade migrierte Vor-2.0.0-Datenbank: Konto/Kategorie/
    Buchung existieren bereits, aber ohne user_id."""
    db.init_db()
    with db.db_session() as conn:
        conn.execute("INSERT INTO accounts (name, type) VALUES ('Altkonto', 'Konto')")
        conn.execute("INSERT INTO categories (name, kind) VALUES ('Altkategorie', 'Ausgabe')")

    with _raw_client() as c:
        c.post("/ersteinrichtung", data={
            "username": "anna", "password": "sicheres-passwort",
            "password_confirm": "sicheres-passwort",
        })

    admin_id = scalar("SELECT id FROM users WHERE username = 'anna'")
    assert scalar("SELECT user_id FROM accounts WHERE name = 'Altkonto'") == admin_id
    assert scalar("SELECT user_id FROM categories WHERE name = 'Altkategorie'") == admin_id
    # Keine Dublette durch das anschliessende Seeding der Standardkategorien.
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Altkategorie'") == 1


def test_ersteinrichtung_ist_nach_abschluss_gesperrt(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", "irgendein-hash", role="admin")

    with _raw_client() as c:
        resp = c.get("/ersteinrichtung")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login"


# --------------------------------------------------------- Login-Gate

def test_ohne_login_leitet_jede_seite_zum_login(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", "irgendein-hash", role="admin")

    with _raw_client() as c:
        resp = c.get("/buchungen")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/login"


def test_login_seite_ist_ohne_login_erreichbar(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", "irgendein-hash", role="admin")

    with _raw_client() as c:
        assert c.get("/login").status_code == 200


# ------------------------------------------------------------- Login

def test_login_mit_korrekten_daten(isolated_db):
    from werkzeug.security import generate_password_hash

    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", generate_password_hash("sicheres-passwort"), role="admin")
        db.seed_categories_for_user(conn, scalar("SELECT id FROM users WHERE username = 'anna'"))

    with _raw_client() as c:
        resp = c.post("/login", data={"username": "anna", "password": "sicheres-passwort"})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        assert c.get("/").status_code == 200


def test_login_mit_falschem_passwort(isolated_db):
    from werkzeug.security import generate_password_hash

    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", generate_password_hash("sicheres-passwort"), role="admin")

    with _raw_client() as c:
        resp = c.post("/login", data={"username": "anna", "password": "falsch"})
        assert resp.status_code == 200
        assert "falsch" in resp.get_data(as_text=True)
        # Kein Login -> naechster Seitenaufruf wird weiterhin umgeleitet.
        assert c.get("/").status_code == 302


def test_login_mit_unbekanntem_benutzernamen(isolated_db):
    db.init_db()
    with db.db_session() as conn:
        db.create_user(conn, "anna", "irgendein-hash", role="admin")

    with _raw_client() as c:
        resp = c.post("/login", data={"username": "unbekannt", "password": "egal"})
        assert resp.status_code == 200
        assert "falsch" in resp.get_data(as_text=True)


# ------------------------------------------------------------ Logout

def test_logout_beendet_die_session(client):
    assert client.get("/").status_code == 200

    resp = client.post("/logout")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"

    assert client.get("/").status_code == 302


# ------------------------------------------------------------- Profil

def _client_with_user(username, password):
    from werkzeug.security import generate_password_hash

    db.init_db()
    with db.db_session() as conn:
        uid = db.create_user(conn, username, generate_password_hash(password), role="admin")
        db.seed_categories_for_user(conn, uid)

    c = _raw_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return c, uid


def test_profil_passwort_aendern(isolated_db):
    from werkzeug.security import check_password_hash

    c, uid = _client_with_user("anna", "altes-passwort")
    resp = c.post("/profil", data={
        "current_password": "altes-passwort",
        "new_password": "neues-passwort",
        "new_password_confirm": "neues-passwort",
    })

    assert resp.status_code == 302
    with db.db_session() as conn:
        user = db.get_user_by_id(conn, uid)
    assert check_password_hash(user["password_hash"], "neues-passwort")


def test_profil_lehnt_falsches_altes_passwort_ab(isolated_db):
    c, uid = _client_with_user("anna", "altes-passwort")
    resp = c.post("/profil", data={
        "current_password": "falsch",
        "new_password": "neues-passwort",
        "new_password_confirm": "neues-passwort",
    })

    assert "aktuelle Passwort ist falsch" in resp.get_data(as_text=True)


def test_profil_validiert_neues_passwort(isolated_db):
    c, uid = _client_with_user("anna", "altes-passwort")
    resp = c.post("/profil", data={
        "current_password": "altes-passwort",
        "new_password": "kurz",
        "new_password_confirm": "kurz",
    })

    assert "mindestens 8 Zeichen" in resp.get_data(as_text=True)


def test_profil_lehnt_abweichende_bestaetigung_ab(isolated_db):
    c, uid = _client_with_user("anna", "altes-passwort")
    resp = c.post("/profil", data={
        "current_password": "altes-passwort",
        "new_password": "neues-passwort",
        "new_password_confirm": "anderes-passwort",
    })

    assert "stimmen nicht überein" in resp.get_data(as_text=True)
