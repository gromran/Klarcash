"""Beweist, dass die Testsuite die echte ausgaben.db niemals anfasst.

Diese Datei testet die Schutzmechanik selbst - ohne die echte Datenbank je
zu beschreiben. Faellt einer dieser Tests aus, ist der Rest der Suite nicht
mehr vertrauenswuerdig.
"""

import db
from conftest import REAL_DB, _fingerprint, scalar


def test_db_path_zeigt_nie_auf_die_echte_datenbank(isolated_db, tmp_path):
    assert db.DB_PATH != REAL_DB
    assert db.DB_PATH.parent == tmp_path


def test_schreibzugriffe_landen_in_der_wegwerf_db(client, make):
    vorher = _fingerprint(REAL_DB)

    acc = make.account(name="Testkonto", initial=100.0)
    client.post("/buchungen/neu", data={
        "type": "Ausgabe", "account_id": acc, "amount": "10", "date": "2026-07-01",
    })

    assert scalar("SELECT COUNT(*) FROM transactions") == 1
    assert _fingerprint(REAL_DB) == vorher  # echte DB unveraendert


def test_fingerprint_erkennt_aenderungen(tmp_path):
    """Der Waechter taugt nur, wenn er Aenderungen wirklich bemerkt."""
    probe = tmp_path / "probe.db"

    assert _fingerprint(probe) is None  # existiert nicht

    probe.write_bytes(b"a")
    leer = _fingerprint(probe)
    probe.write_bytes(b"ab")

    assert _fingerprint(probe) != leer
