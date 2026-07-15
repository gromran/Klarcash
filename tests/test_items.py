"""Kassenzettel-Posten: Buchungen in einzelne Posten aufteilen.

Getestet ueber die echten Formularrouten, weil die parallelen Formularlisten
(item_description / item_category_id / item_amount) und das use_items-Flag
genau dort zusammenlaufen.
"""

import pytest

import db
from conftest import balance, fetchall, fetchone, scalar


def post_buchung(client, url="/buchungen/neu", **overrides):
    data = {
        "type": "Ausgabe",
        "date": "2026-07-01",
        "description": "Supermarkt",
        "amount": "0",
        "category_id": "",
        "target_account_id": "",
    }
    data.update(overrides)
    return client.post(url, data=data)


def test_posten_bilden_den_buchungsbetrag(client, make):
    acc = make.account(initial=100.0)

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Brot", "Milch"],
        item_amount=["3,50", "1,25"],
        item_category_id=["", ""],
        amount="999",  # muss vom Server ignoriert werden
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    tx = fetchone("SELECT * FROM transactions")
    assert tx["amount"] == pytest.approx(4.75)  # nicht 999
    assert balance(acc) == pytest.approx(95.25)


def test_postenbuchung_hat_keine_eigene_kategorie(client, make):
    acc = make.account()
    kat = make.category_id("Lebensmittel")

    post_buchung(
        client,
        account_id=acc,
        category_id=kat,  # wird bei Posten bewusst verworfen
        use_items="on",
        item_description=["Brot"],
        item_amount=["3,50"],
        item_category_id=[kat],
    )

    tx = fetchone("SELECT * FROM transactions")
    assert tx["category_id"] is None
    # Die Kategorie lebt jetzt auf Posten-Ebene.
    assert scalar("SELECT category_id FROM transaction_items") == kat


def test_posten_behalten_ihre_reihenfolge(client, make):
    acc = make.account()

    post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Zuerst", "Danach", "Zuletzt"],
        item_amount=["1", "2", "3"],
        item_category_id=["", "", ""],
    )

    rows = fetchall("SELECT description, position FROM transaction_items ORDER BY position")
    assert [r["description"] for r in rows] == ["Zuerst", "Danach", "Zuletzt"]
    assert [r["position"] for r in rows] == [0, 1, 2]


def test_negativer_posten_ist_erlaubt(client, make):
    """Pfandrueckgabe: Posten duerfen negativ sein, die Summe bleibt positiv."""
    acc = make.account(initial=100.0)

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Getränke", "Pfandrückgabe"],
        item_amount=["12,00", "-3,00"],
        item_category_id=["", ""],
    )

    assert resp.status_code == 302
    assert fetchone("SELECT amount FROM transactions")["amount"] == pytest.approx(9.0)
    assert balance(acc) == pytest.approx(91.0)


def test_postensumme_muss_groesser_null_sein(client, make):
    acc = make.account()

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Ware", "Rückgabe"],
        item_amount=["5,00", "-5,00"],
        item_category_id=["", ""],
    )

    assert resp.status_code == 200  # Re-Render statt Redirect
    assert "Die Summe der Posten muss größer als 0 sein." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM transactions") == 0


def test_posten_mit_betrag_null_wird_abgelehnt(client, make):
    acc = make.account()

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Brot", "Nullposten"],
        item_amount=["3,00", "0"],
        item_category_id=["", ""],
    )

    assert resp.status_code == 200
    # Fehlermeldungen sind 1-indiziert, nicht 0-indiziert.
    assert "Posten 2: Der Betrag darf nicht 0 sein." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM transactions") == 0


def test_leere_posten_zeilen_werden_ignoriert(client, make):
    acc = make.account()

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Brot", "", ""],
        item_amount=["3,50", "", ""],
        item_category_id=["", "", ""],
    )

    assert resp.status_code == 302
    assert scalar("SELECT COUNT(*) FROM transaction_items") == 1


def test_use_items_ohne_posten_wird_abgelehnt(client, make):
    acc = make.account()

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=[""],
        item_amount=[""],
        item_category_id=[""],
    )

    assert resp.status_code == 200
    assert "Bitte mindestens einen Posten angeben" in resp.get_data(as_text=True)


def test_use_items_wird_bei_umbuchung_ignoriert(client, make):
    quelle = make.account(name="Giro", initial=500.0)
    ziel = make.account(name="Bar", type="Bar")

    resp = post_buchung(
        client,
        type="Umbuchung",
        account_id=quelle,
        target_account_id=ziel,
        amount="200",
        use_items="on",
        item_description=["Unsinn"],
        item_amount=["999"],
        item_category_id=[""],
    )

    assert resp.status_code == 302
    tx = fetchone("SELECT * FROM transactions")
    assert tx["amount"] == pytest.approx(200.0)  # amount-Feld gewinnt
    assert scalar("SELECT COUNT(*) FROM transaction_items") == 0


def test_ungueltiger_postenbetrag_wird_gemeldet(client, make):
    acc = make.account()

    resp = post_buchung(
        client,
        account_id=acc,
        use_items="on",
        item_description=["Brot"],
        item_amount=["keine Zahl"],
        item_category_id=[""],
    )

    assert resp.status_code == 200
    assert "Posten 1: Bitte einen gültigen Betrag angeben." in resp.get_data(as_text=True)


# ---------------------------------------------------------- Bearbeiten

def test_posten_beim_bearbeiten_ersetzen(client, make):
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=4.75)
    make.items(tx, [("Brot", 3.50, None), ("Milch", 1.25, None)])

    resp = post_buchung(
        client,
        url=f"/buchungen/{tx}/bearbeiten",
        account_id=acc,
        use_items="on",
        item_description=["Käse"],
        item_amount=["8,00"],
        item_category_id=[""],
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/buchungen"
    rows = fetchall("SELECT description FROM transaction_items")
    assert [r["description"] for r in rows] == ["Käse"]
    assert fetchone("SELECT amount FROM transactions")["amount"] == pytest.approx(8.0)


def test_posten_beim_bearbeiten_entfernen(client, make):
    """use_items abwaehlen macht die Buchung wieder zur Einzelbuchung."""
    acc = make.account(initial=100.0)
    tx = make.tx(acc, type="Ausgabe", amount=4.75)
    make.items(tx, [("Brot", 3.50, None), ("Milch", 1.25, None)])
    kat = make.category_id("Lebensmittel")

    resp = post_buchung(
        client,
        url=f"/buchungen/{tx}/bearbeiten",
        account_id=acc,
        amount="20,00",
        category_id=kat,
        # kein use_items -> Aufteilung deaktiviert
    )

    assert resp.status_code == 302
    assert scalar("SELECT COUNT(*) FROM transaction_items WHERE transaction_id = ?", (tx,)) == 0
    tx_row = fetchone("SELECT * FROM transactions WHERE id = ?", (tx,))
    assert tx_row["amount"] == pytest.approx(20.0)
    assert tx_row["category_id"] == kat
    assert balance(acc) == pytest.approx(80.0)


# ------------------------------------------------- Auswertung der Posten

def test_category_breakdown_loest_posten_kategorien_auf(client, make):
    acc = make.account()
    lebensmittel = make.category_id("Lebensmittel")
    freizeit = make.category_id("Freizeit")
    tx = make.tx(acc, type="Ausgabe", amount=30.0, category=None, date="2026-07-01")
    make.items(tx, [("Brot", 10.0, lebensmittel), ("Kino", 20.0, freizeit)])

    with db.db_session() as conn:
        rows = db.category_breakdown(conn, "Ausgabe", "2026-01-01", make.default_user_id)

    ergebnis = {r["category"]: r["summe"] for r in rows}
    assert ergebnis == {"Freizeit": 20.0, "Lebensmittel": 10.0}


def test_category_breakdown_zeigt_unterkategorie_mit_pfad(client, make):
    acc = make.account()
    haupt = make.category_id("Lebensmittel")
    unter = make.category("Brot", kind="Ausgabe", parent=haupt)
    tx = make.tx(acc, type="Ausgabe", amount=3.0, category=None, date="2026-07-01")
    make.items(tx, [("Vollkorn", 3.0, unter)])

    with db.db_session() as conn:
        rows = db.category_breakdown(conn, "Ausgabe", "2026-01-01", make.default_user_id)

    assert rows[0]["category"] == "Lebensmittel › Brot"
