"""Daten-Isolation zwischen Nutzern (Nutzerverwaltung, Routen-Scoping).

Testet, dass ein eingeloggter Nutzer ausschliesslich seine eigenen Daten
sieht und veraendern kann. Fremde <int:id>-Routen werden wie "nicht
gefunden" behandelt (IDOR-Schutz) statt einen Serverfehler oder gar Zugriff
zu erlauben. client (aus conftest.py) ist als der Test-Admin eingeloggt;
make.user(...) legt weitere Nutzer fuer die Gegenprobe an.
"""

import app as app_module
import db
from conftest import scalar


def _client_as(uid):
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return c


# --------------------------------------------------------- Kategorien

def test_zwei_nutzer_duerfen_gleichnamige_kategorie_haben(client, make):
    bert = make.user("bert")

    resp_test = client.post("/kategorien", data={"name": "Urlaub", "kind": "Ausgabe"})
    resp_bert = _client_as(bert).post("/kategorien", data={"name": "Urlaub", "kind": "Ausgabe"})

    assert resp_test.status_code == 302
    assert resp_bert.status_code == 302
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Urlaub'") == 2


def test_fremde_kategorie_bearbeiten_wird_wie_nicht_gefunden_behandelt(client, make):
    bert = make.user("bert")
    fremde_kat = make.category("Bert-Kategorie", user=bert)

    resp = client.get(f"/kategorien/{fremde_kat}/bearbeiten")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/kategorien"


def test_fremde_kategorie_loeschen_aendert_sie_nicht(client, make):
    bert = make.user("bert")
    fremde_kat = make.category("Bert-Kategorie", user=bert)

    client.post(f"/kategorien/{fremde_kat}/loeschen")

    assert scalar("SELECT COUNT(*) FROM categories WHERE id = ?", (fremde_kat,)) == 1


def test_kategorienliste_zeigt_nur_eigene(client, make):
    bert = make.user("bert")
    make.category("Bert-Only", user=bert)

    text = client.get("/kategorien").get_data(as_text=True)
    assert "Bert-Only" not in text


# ------------------------------------------------------------- Konten

def test_dashboard_zeigt_nur_eigene_konten(client, make):
    bert = make.user("bert")
    make.account(name="Bert-Konto", initial=500.0, user=bert)
    make.account(name="Mein-Konto", initial=100.0)

    text = client.get("/").get_data(as_text=True)
    assert "Mein-Konto" in text
    assert "Bert-Konto" not in text


def test_kontenuebersicht_zeigt_nur_eigene(client, make):
    bert = make.user("bert")
    make.account(name="Bert-Konto", user=bert)

    text = client.get("/konten").get_data(as_text=True)
    assert "Bert-Konto" not in text


def test_fremdes_konto_archivieren_aendert_es_nicht(client, make):
    bert = make.user("bert")
    fremdes_konto = make.account(user=bert, archived=0)

    client.post(f"/konten/{fremdes_konto}/archivieren")

    assert scalar("SELECT archived FROM accounts WHERE id = ?", (fremdes_konto,)) == 0


# --------------------------------------------------------- Buchungen

def test_buchungsliste_zeigt_nur_eigene(client, make):
    bert = make.user("bert")
    bert_acc = make.account(name="Bert-Konto", user=bert)
    make.tx(bert_acc, description="Fremdes Geheimnis", user=bert)

    text = client.get("/buchungen").get_data(as_text=True)
    assert "Fremdes Geheimnis" not in text


def test_fremde_buchung_bearbeiten_wird_wie_nicht_gefunden_behandelt(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    fremde_tx = make.tx(bert_acc, description="Fremd", user=bert)

    resp = client.get(f"/buchungen/{fremde_tx}/bearbeiten")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/buchungen"


def test_fremde_buchung_loeschen_aendert_sie_nicht(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    fremde_tx = make.tx(bert_acc, user=bert)

    client.post(f"/buchungen/{fremde_tx}/loeschen")

    assert scalar("SELECT deleted FROM transactions WHERE id = ?", (fremde_tx,)) == 0


def test_fremde_buchung_wiederherstellen_aendert_sie_nicht(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    fremde_tx = make.tx(bert_acc, user=bert, deleted=1)

    client.post(f"/papierkorb/{fremde_tx}/wiederherstellen")

    assert scalar("SELECT deleted FROM transactions WHERE id = ?", (fremde_tx,)) == 1


def test_fremde_buchung_endgueltig_loeschen_entfernt_sie_nicht(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    fremde_tx = make.tx(bert_acc, user=bert, deleted=1)

    client.post(f"/papierkorb/{fremde_tx}/endgueltig-loeschen")

    assert scalar("SELECT COUNT(*) FROM transactions WHERE id = ?", (fremde_tx,)) == 1


def test_papierkorb_zeigt_nur_eigene(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    make.tx(bert_acc, description="Fremd geloescht", user=bert, deleted=1)

    text = client.get("/papierkorb").get_data(as_text=True)
    assert "Fremd geloescht" not in text


# ------------------------------------------------------ IDOR ueber Formulare

def test_buchung_anlegen_mit_fremdem_konto_wird_abgelehnt(client, make):
    make.account()  # eigenes Konto, sonst leitet die Route zur Kontoanlage um
    bert = make.user("bert")
    fremdes_konto = make.account(user=bert)

    resp = client.post("/buchungen/neu", data={
        "type": "Ausgabe", "account_id": fremdes_konto, "amount": "10,00",
    })

    assert resp.status_code == 200
    assert "Ungültiges Konto." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (fremdes_konto,)) == 0


def test_buchung_anlegen_mit_fremder_kategorie_wird_abgelehnt(client, make):
    eigenes_konto = make.account()
    bert = make.user("bert")
    fremde_kat = make.category("Bert-Kategorie", user=bert)

    resp = client.post("/buchungen/neu", data={
        "type": "Ausgabe", "account_id": eigenes_konto, "amount": "10,00",
        "category_id": fremde_kat,
    })

    assert resp.status_code == 200
    assert "Ungültige Kategorie." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM transactions WHERE category_id = ?", (fremde_kat,)) == 0


def test_kategorie_anlegen_mit_fremder_elternkategorie_wird_abgelehnt(client, make):
    bert = make.user("bert")
    fremde_haupt = make.category("Bert-Haupt", user=bert)

    resp = client.post("/kategorien", data={
        "name": "Unterkategorie", "kind": "Ausgabe", "parent_id": fremde_haupt,
    }, follow_redirects=True)

    assert "Übergeordnete Kategorie wurde nicht gefunden." in resp.get_data(as_text=True)


# --------------------------------------------------------- Statistiken

def test_statistiken_beruecksichtigen_nur_eigene_buchungen(client, make):
    bert = make.user("bert")
    bert_acc = make.account(user=bert)
    make.tx(bert_acc, type="Ausgabe", amount=99999.0, user=bert, date="2026-07-01")

    text = client.get("/statistiken").get_data(as_text=True)
    assert "99.999,00" not in text


# --------------------------------------------------- Nutzerverwaltung (Admin)

def test_nutzerseite_fuer_nicht_admin_gesperrt(make):
    bert = make.user("bert", role="user")

    resp = _client_as(bert).get("/nutzer")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_nutzer_neu_fuer_nicht_admin_gesperrt(make):
    bert = make.user("bert", role="user")

    resp = _client_as(bert).post("/nutzer/neu", data={
        "username": "dritter", "password": "sicheres-passwort",
        "password_confirm": "sicheres-passwort",
    })

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    assert scalar("SELECT COUNT(*) FROM users WHERE username = 'dritter'") == 0


def test_admin_kann_nutzer_anlegen(client):
    resp = client.post("/nutzer/neu", data={
        "username": "bert", "role": "user", "password": "sicheres-passwort",
        "password_confirm": "sicheres-passwort",
    })

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/nutzer"
    assert scalar("SELECT role FROM users WHERE username = 'bert'") == "user"
    assert scalar(
        "SELECT COUNT(*) FROM categories WHERE user_id = (SELECT id FROM users WHERE username = 'bert')"
    ) == 7


def test_nutzer_anlegen_mit_doppeltem_namen_wird_abgelehnt(client, make):
    make.user("bert")

    resp = client.post("/nutzer/neu", data={
        "username": "bert", "password": "sicheres-passwort",
        "password_confirm": "sicheres-passwort",
    })

    assert "bereits vergeben" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users WHERE username = 'bert'") == 1


def test_nutzer_anlegen_validiert_passwort(client):
    resp = client.post("/nutzer/neu", data={
        "username": "bert", "password": "kurz", "password_confirm": "kurz",
    })

    assert "mindestens 8 Zeichen" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users WHERE username = 'bert'") == 0


def test_admin_kann_nutzer_ohne_daten_loeschen(client, make):
    bert = make.user("bert")

    resp = client.post(f"/nutzer/{bert}/loeschen")

    assert resp.status_code == 302
    assert scalar("SELECT COUNT(*) FROM users WHERE id = ?", (bert,)) == 0


def test_nutzer_mit_daten_kann_nicht_geloescht_werden(client, make):
    bert = make.user("bert")
    make.account(user=bert)

    resp = client.post(f"/nutzer/{bert}/loeschen", follow_redirects=True)

    assert "kann nicht gelöscht werden" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users WHERE id = ?", (bert,)) == 1


def test_eigener_account_kann_nicht_geloescht_werden(client, make):
    resp = client.post(f"/nutzer/{make.default_user_id}/loeschen", follow_redirects=True)

    assert "eigene Account kann nicht gelöscht werden" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM users WHERE id = ?", (make.default_user_id,)) == 1


def test_admin_kann_anderen_admin_loeschen(client, make):
    """Die Selbstloeschungssperre ist die einzige Huerde - ein Admin darf
    einen ANDEREN (auch admin-berechtigten) Nutzer jederzeit entfernen,
    solange dieser keine eigenen Daten mehr hat."""
    with db.db_session() as conn:
        zweiter_admin = db.create_user(conn, "carla", "hash", role="admin")

    resp = client.post(f"/nutzer/{zweiter_admin}/loeschen")

    assert resp.status_code == 302
    assert scalar("SELECT COUNT(*) FROM users WHERE id = ?", (zweiter_admin,)) == 0
    # Der ausfuehrende Admin bleibt unangetastet.
    assert scalar("SELECT COUNT(*) FROM users WHERE id = ?", (make.default_user_id,)) == 1
