"""DB-Tab im Settings-Bereich (Version 3.4.0, Phase 1 von Roadmap-Punkt 3):
waehlbarer Speicherort (klarcash_config.json) sowie Backup-Download/Restore-
Upload der Datenbank-Datei.

Siehe db.py (load_config/save_config/resolve_db_path/backup_to/is_valid_db)
und app.py (settings()-Zweig section == "db", /einstellungen/db/backup,
/einstellungen/db/restore). Alle drei sind bewusst Admin-only, da die DB ein
einziges, app-weit geteiltes SQLite-File ist (kein Pro-Nutzer-Setting wie
Account/Appearance).
"""

import io
import sqlite3

import app as app_module
import db
from conftest import scalar


def _client_as(uid):
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return c


# ------------------------------------------------- resolve_db_path/Config

def test_resolve_db_path_ohne_config_liefert_default(isolated_db, tmp_path):
    assert db.resolve_db_path() == tmp_path / db.DEFAULT_DB_NAME


def test_resolve_db_path_nutzt_konfigurierten_pfad(isolated_db, tmp_path):
    custom = tmp_path / "custom" / "meine.db"
    custom.parent.mkdir()
    db.save_config({"db_path": str(custom)})

    assert db.resolve_db_path() == custom


def test_resolve_db_path_bei_kaputter_config_faellt_auf_default_zurueck(isolated_db, tmp_path):
    db.config_file().write_text("{das ist kein json", encoding="utf-8")

    assert db.resolve_db_path() == tmp_path / db.DEFAULT_DB_NAME


# ------------------------------------------------------------ backup_to / is_valid_db

def test_backup_to_liefert_intakte_kopie(initialized_db, tmp_path):
    dest = tmp_path / "backup.db"
    db.backup_to(dest)

    assert db.is_valid_db(dest)


def test_is_valid_db_lehnt_keine_sqlite_datei_ab(tmp_path):
    fake = tmp_path / "fake.db"
    fake.write_text("das ist keine sqlite-datenbank", encoding="utf-8")

    assert not db.is_valid_db(fake)


def test_is_valid_db_lehnt_falsche_major_version_ab(initialized_db, tmp_path):
    dest = tmp_path / "backup.db"
    db.backup_to(dest)
    conn = sqlite3.connect(dest)
    db._set_version(conn, "99.0.0")
    conn.commit()
    conn.close()

    assert not db.is_valid_db(dest)


# ------------------------------------------------------------- DB-Tab (UI)

def test_db_tab_fuer_admin_erreichbar(client):
    resp = client.get("/einstellungen", query_string={"tab": "db"})

    assert resp.status_code == 200
    assert 'id="panel-db"' in resp.get_data(as_text=True)


def test_db_tab_fuer_normale_nutzer_nicht_sichtbar(make):
    bert = make.user("bert", role="user")
    c = _client_as(bert)

    resp = c.get("/einstellungen", query_string={"tab": "db"})

    assert resp.status_code == 200
    assert 'id="panel-db"' not in resp.get_data(as_text=True)


# -------------------------------------------------------- Speicherort wechseln

def test_speicherort_wechseln_kopiert_db_und_schreibt_config(client, tmp_path):
    ziel = tmp_path / "neuer_ordner"
    ziel.mkdir()

    resp = client.post("/einstellungen", data={"section": "db", "db_dir": str(ziel)})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/einstellungen?tab=db"
    cfg = db.load_config()
    assert cfg["db_path"] == str(ziel / db.DEFAULT_DB_NAME)
    assert (ziel / db.DEFAULT_DB_NAME).exists()
    assert db.is_valid_db(ziel / db.DEFAULT_DB_NAME)


def test_speicherort_wechseln_mit_fehlendem_ordner_zeigt_fehler(client, tmp_path):
    resp = client.post(
        "/einstellungen",
        data={"section": "db", "db_dir": str(tmp_path / "gibt-es-nicht")},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert "existiert nicht" in resp.get_data(as_text=True)
    assert not db.config_file().exists()


def test_normale_nutzer_koennen_speicherort_nicht_aendern(make, tmp_path):
    bert = make.user("bert", role="user")
    c = _client_as(bert)
    ziel = tmp_path / "ziel"
    ziel.mkdir()

    c.post("/einstellungen", data={"section": "db", "db_dir": str(ziel)})

    assert not db.config_file().exists()


# ---------------------------------------------------------------- Backup

def test_backup_download_liefert_valide_datenbank(client, tmp_path):
    resp = client.get("/einstellungen/db/backup")

    assert resp.status_code == 200
    dest = tmp_path / "downloaded.db"
    dest.write_bytes(resp.data)
    assert db.is_valid_db(dest)


def test_backup_download_nur_fuer_admin(make):
    bert = make.user("bert", role="user")
    c = _client_as(bert)

    resp = c.get("/einstellungen/db/backup")

    assert resp.status_code == 302  # admin_required leitet aufs Dashboard um
    assert resp.headers["Location"] == "/"


# ---------------------------------------------------------------- Restore

def test_restore_akzeptiert_gueltiges_backup(client, tmp_path):
    backup_path = tmp_path / "backup.db"
    db.backup_to(backup_path)

    with open(backup_path, "rb") as f:
        resp = client.post(
            "/einstellungen/db/restore",
            data={"backup_file": (f, "backup.db")},
            content_type="multipart/form-data",
        )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/einstellungen?tab=db"


def test_restore_lehnt_ungueltige_datei_ab(client):
    resp = client.post(
        "/einstellungen/db/restore",
        data={"backup_file": (io.BytesIO(b"keine sqlite-datei"), "fake.db")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert "keine gültige oder kompatible" in resp.get_data(as_text=True)
    # Der urspruengliche Test-Admin muss weiterhin existieren - die
    # ungueltige Datei darf die produktive DB nie ueberschrieben haben.
    assert scalar("SELECT username FROM users WHERE username = 'test'") == "test"


def test_restore_ohne_datei_zeigt_fehler(client):
    resp = client.post("/einstellungen/db/restore", data={}, follow_redirects=True)

    assert resp.status_code == 200
    assert "Bitte eine Backup-Datei auswählen." in resp.get_data(as_text=True)


def test_restore_nur_fuer_admin(make, tmp_path):
    bert = make.user("bert", role="user")
    c = _client_as(bert)
    backup_path = tmp_path / "backup.db"
    db.backup_to(backup_path)

    with open(backup_path, "rb") as f:
        resp = c.post(
            "/einstellungen/db/restore",
            data={"backup_file": (f, "backup.db")},
            content_type="multipart/form-data",
        )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
