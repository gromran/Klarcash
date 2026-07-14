"""Papierkorb / Soft-Delete (Pflichtfall 3 aus PROJECT.md).

Getestet auf Routen-Ebene, weil der Soft-Delete-Zyklus genau dort lebt.
"""

import pytest

import db
from conftest import balance, fetchone, scalar


def test_loeschen_setzt_soft_delete_flags(client, make):
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=40.0)

    resp = client.post(f"/buchungen/{tx}/loeschen")

    assert resp.status_code == 302
    row = fetchone("SELECT deleted, deleted_at FROM transactions WHERE id = ?", (tx,))
    assert row["deleted"] == 1
    assert row["deleted_at"] is not None
    # Zeile bleibt in der DB - nur eben als geloescht markiert.
    assert scalar("SELECT COUNT(*) FROM transactions") == 1


def test_loeschen_dreht_den_saldo_zurueck(client, make):
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=40.0)
    assert balance(acc) == pytest.approx(60.0)

    client.post(f"/buchungen/{tx}/loeschen")

    assert balance(acc) == pytest.approx(100.0)


def test_loeschen_folgt_dem_referer(client, make):
    acc = make.account()
    tx = make.tx(acc)

    resp = client.post(f"/buchungen/{tx}/loeschen", headers={"Referer": "/papierkorb"})
    assert resp.headers["Location"] == "/papierkorb"

    tx2 = make.tx(acc)
    resp = client.post(f"/buchungen/{tx2}/loeschen")
    assert resp.headers["Location"] == "/buchungen"  # Fallback ohne Referer


def test_geloeschte_buchung_verschwindet_aus_der_liste(client, make):
    acc = make.account()
    tx = make.tx(acc, description="Geheimkauf", amount=40.0)

    assert b"Geheimkauf" in client.get("/buchungen").data
    client.post(f"/buchungen/{tx}/loeschen")

    assert b"Geheimkauf" not in client.get("/buchungen").data
    assert b"Geheimkauf" in client.get("/papierkorb").data


def test_geloeschte_buchung_faellt_aus_den_auswertungen(client, make):
    acc = make.account(initial=100.0)
    kat = make.category_id("Lebensmittel")
    tx = make.tx(acc, type="Ausgabe", amount=40.0, category=kat, date="2026-07-01")

    client.post(f"/buchungen/{tx}/loeschen")

    with db.db_session() as conn:
        assert db.monthly_summary(conn, "2026-01-01") == {}
        assert db.category_breakdown(conn, "Ausgabe", "2026-01-01") == []
        start_total, series = db.net_worth_series(conn)
        assert start_total == 100.0
        assert series == []
        assert db.earliest_transaction_date(conn) is None


def test_wiederherstellen_macht_den_soft_delete_rueckgaengig(client, make):
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=40.0, deleted=1)

    resp = client.post(f"/papierkorb/{tx}/wiederherstellen")

    assert resp.status_code == 302
    row = fetchone("SELECT deleted, deleted_at FROM transactions WHERE id = ?", (tx,))
    assert row["deleted"] == 0
    assert row["deleted_at"] is None
    assert balance(acc) == pytest.approx(60.0)


def test_endgueltig_loeschen_entfernt_buchung_und_posten(client, make):
    """Der explizit in PROJECT.md genannte Fall: transaction_items duerfen
    nicht als Waisen zurueckbleiben."""
    acc = make.account()
    tx = make.tx(acc, type="Ausgabe", amount=13.0, deleted=1)
    make.items(tx, [("Brot", 3.0, None), ("Milch", 10.0, None)])
    assert scalar("SELECT COUNT(*) FROM transaction_items WHERE transaction_id = ?", (tx,)) == 2

    resp = client.post(f"/papierkorb/{tx}/endgueltig-loeschen")

    assert resp.status_code == 302
    assert scalar("SELECT COUNT(*) FROM transactions WHERE id = ?", (tx,)) == 0
    assert scalar("SELECT COUNT(*) FROM transaction_items WHERE transaction_id = ?", (tx,)) == 0


def test_endgueltig_loeschen_verschont_aktive_buchungen(client, make):
    """Die Route filtert bewusst auf deleted = 1 - eine aktive Buchung darf
    ueber den Papierkorb-Endpunkt nicht verschwinden."""
    acc = make.account()
    tx = make.tx(acc, type="Ausgabe", amount=13.0, deleted=0)

    client.post(f"/papierkorb/{tx}/endgueltig-loeschen")

    assert scalar("SELECT COUNT(*) FROM transactions WHERE id = ?", (tx,)) == 1


def test_papierkorb_zeigt_nur_geloeschte(client, make):
    acc = make.account()
    make.tx(acc, description="Aktiv", deleted=0)
    make.tx(acc, description="Geloescht", deleted=1)

    data = client.get("/papierkorb").data
    assert b"Geloescht" in data
    assert b"Aktiv" not in data


def test_bearbeiten_einer_geloeschten_buchung_leitet_um(client, make):
    acc = make.account()
    tx = make.tx(acc, deleted=1)

    resp = client.get(f"/buchungen/{tx}/bearbeiten")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/buchungen"
