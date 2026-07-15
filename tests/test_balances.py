"""Kontostaende (db.account_balance / accounts_with_balances / total_balance).

Pflichtfall 2 aus PROJECT.md. Die zentrale Invariante lautet: Salden rechnen
ausschliesslich mit transactions.amount - Posten sind dafuer irrelevant.
"""

import sqlite3

import pytest

import db
from conftest import balance, fetchone, total


def test_leeres_konto_hat_anfangsbestand(make):
    acc = make.account(initial=250.0)
    assert balance(acc) == 250.0


def test_einnahme_und_ausgabe(make):
    acc = make.account(initial=100.0)
    make.tx(acc, type="Einnahme", amount=50.0)
    make.tx(acc, type="Ausgabe", amount=20.0)

    assert balance(acc) == pytest.approx(130.0)


def test_umbuchung_verschiebt_zwischen_konten(make):
    quelle = make.account(name="Giro", initial=500.0)
    ziel = make.account(name="Bar", type="Bar", initial=0.0)
    make.tx(quelle, type="Umbuchung", amount=200.0, target=ziel)

    assert balance(quelle) == pytest.approx(300.0)
    assert balance(ziel) == pytest.approx(200.0)


def test_umbuchung_laesst_gesamtsumme_unveraendert(make):
    quelle = make.account(name="Giro", initial=500.0)
    ziel = make.account(name="Bar", type="Bar", initial=100.0)
    vorher = total(make.default_user_id)

    make.tx(quelle, type="Umbuchung", amount=200.0, target=ziel)

    assert total(make.default_user_id) == pytest.approx(vorher)
    assert total(make.default_user_id) == pytest.approx(600.0)


def test_saldo_folgt_transactions_amount_nicht_der_postensumme(make):
    """Kern-Invariante aus PROJECT.md: Posten sind nur fuer Kategorie-
    Auswertungen da, nie fuer Salden. Hier wird die Postensumme bewusst
    abweichend gesetzt - der Saldo darf sich davon nicht beirren lassen."""
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=30.0)
    make.items(tx, [("Brot", 999.0, None)])  # absichtlich inkonsistent

    assert balance(acc) == pytest.approx(70.0)


def test_postenbuchung_zaehlt_mit_ihrem_buchungsbetrag(make):
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=12.75)
    make.items(tx, [("Brot", 3.0, None), ("Milch", 10.0, None), ("Pfand", -0.25, None)])

    assert balance(acc) == pytest.approx(87.25)


def test_geloeschte_buchung_faellt_aus_dem_saldo(make):
    acc = make.account(initial=100.0)
    make.tx(acc, type="Ausgabe", amount=40.0, deleted=1)

    assert balance(acc) == 100.0


def test_geloeschte_umbuchung_wirkt_auf_keiner_seite(make):
    quelle = make.account(name="Giro", initial=500.0)
    ziel = make.account(name="Bar", type="Bar", initial=0.0)
    make.tx(quelle, type="Umbuchung", amount=200.0, target=ziel, deleted=1)

    assert balance(quelle) == 500.0
    assert balance(ziel) == 0.0


def test_accounts_with_balances_blendet_archivierte_aus(make):
    make.account(name="Aktiv", initial=100.0)
    make.account(name="Archiviert", initial=999.0, archived=1)

    with db.db_session() as conn:
        aktiv = db.accounts_with_balances(conn, make.default_user_id)
        alle = db.accounts_with_balances(conn, make.default_user_id, include_archived=True)

    assert [a["name"] for a in aktiv] == ["Aktiv"]
    assert {a["name"] for a in alle} == {"Aktiv", "Archiviert"}


def test_total_balance_ignoriert_archivierte_konten(make):
    make.account(name="Aktiv", initial=100.0)
    make.account(name="Archiviert", initial=999.0, archived=1)

    assert total(make.default_user_id) == pytest.approx(100.0)


def test_accounts_with_balances_liefert_balance_je_konto(make):
    acc = make.account(name="Giro", initial=100.0)
    make.tx(acc, type="Einnahme", amount=25.0)

    with db.db_session() as conn:
        konten = db.accounts_with_balances(conn, make.default_user_id)

    assert konten[0]["balance"] == pytest.approx(125.0)
    assert konten[0]["name"] == "Giro"  # dict enthaelt weiterhin alle Spalten


def test_unbekanntes_konto_hat_saldo_null(initialized_db):
    assert balance(999) == 0.0


def test_total_balance_ohne_konten_ist_null(test_user_id):
    assert total(test_user_id) == 0


def test_betrag_muss_groesser_null_sein(make):
    acc = make.account()
    with pytest.raises(sqlite3.IntegrityError):
        make.tx(acc, type="Ausgabe", amount=0.0)
    with pytest.raises(sqlite3.IntegrityError):
        make.tx(acc, type="Ausgabe", amount=-5.0)


def test_posten_duerfen_negativ_sein(make):
    """Gegenstueck zum CHECK auf transactions.amount: Posten kennen die
    Einschraenkung bewusst nicht (Pfandrueckgabe, Rabatt)."""
    acc = make.account()
    tx = make.tx(acc, type="Ausgabe", amount=5.0)
    make.items(tx, [("Pfand", -0.25, None)])

    assert fetchone("SELECT amount FROM transaction_items")["amount"] == -0.25
