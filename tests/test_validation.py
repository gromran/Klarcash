"""Unit-Tests der zentralen Helper aus app.py - ohne HTTP.

_validate_transaction_form und _validate_category_form werden von Neuanlage
UND Bearbeitung geteilt; sie sind damit der wichtigste Ort fuer Regeln.
"""

from datetime import date

import pytest

import db
from app import (
    _parse_items,
    _validate_category_form,
    _validate_transaction_form,
    format_de_date,
    format_eur,
)
from conftest import form


def valid(**overrides):
    data = {"type": "Ausgabe", "account_id": 1, "amount": "10,00", "date": "2026-07-01"}
    data.update(overrides)
    return form(**data)


# ------------------------------------------ _validate_transaction_form

def test_gueltiges_formular():
    data, errors = _validate_transaction_form(valid(description="  Brot  "))

    assert errors == []
    assert data["amount"] == pytest.approx(10.0)
    assert data["description"] == "Brot"  # getrimmt


def test_komma_und_punkt_werden_akzeptiert():
    komma, _ = _validate_transaction_form(valid(amount="12,50"))
    punkt, _ = _validate_transaction_form(valid(amount="12.50"))

    assert komma["amount"] == punkt["amount"] == pytest.approx(12.5)


def test_ungueltiger_betrag():
    data, errors = _validate_transaction_form(valid(amount="viel"))

    assert "Bitte einen gültigen Betrag angeben." in errors
    assert data["amount"] is None  # data bleibt trotzdem befuellt


@pytest.mark.parametrize("betrag", ["0", "-5"])
def test_betrag_muss_positiv_sein(betrag):
    _, errors = _validate_transaction_form(valid(amount=betrag))

    assert "Der Betrag muss größer als 0 sein." in errors


def test_fehlendes_datum_wird_heute():
    data, errors = _validate_transaction_form(form(type="Ausgabe", account_id=1, amount="10"))

    assert data["date"] == date.today().isoformat()
    assert errors == []


def test_ungueltige_buchungsart():
    _, errors = _validate_transaction_form(valid(type="Schenkung"))

    assert "Bitte eine gültige Buchungsart wählen." in errors


def test_fehlendes_konto():
    _, errors = _validate_transaction_form(form(type="Ausgabe", amount="10"))

    assert "Bitte ein Konto auswählen." in errors


def test_umbuchung_braucht_zielkonto():
    _, errors = _validate_transaction_form(valid(type="Umbuchung"))

    assert "Bitte ein Zielkonto für die Umbuchung auswählen." in errors


def test_umbuchung_auf_sich_selbst():
    _, errors = _validate_transaction_form(valid(type="Umbuchung", account_id=1, target_account_id=1))

    assert "Quell- und Zielkonto dürfen nicht identisch sein." in errors


def test_umbuchung_verwirft_kategorie():
    data, errors = _validate_transaction_form(
        valid(type="Umbuchung", account_id=1, target_account_id=2, category_id=5)
    )

    assert errors == []
    assert data["category_id"] is None
    assert data["target_account_id"] == 2


def test_normale_buchung_verwirft_zielkonto():
    data, _ = _validate_transaction_form(valid(type="Ausgabe", target_account_id=2, category_id=5))

    assert data["target_account_id"] is None
    assert data["category_id"] == 5


def test_items_total_ueberschreibt_betragsfeld():
    data, errors = _validate_transaction_form(valid(amount="999"), items_total=4.75)

    assert errors == []
    assert data["amount"] == pytest.approx(4.75)


def test_items_total_wird_gerundet():
    data, _ = _validate_transaction_form(valid(), items_total=0.1 + 0.2)

    assert data["amount"] == 0.3  # nicht 0.30000000000000004


def test_items_total_muss_positiv_sein():
    _, errors = _validate_transaction_form(valid(), items_total=0.0)

    assert "Die Summe der Posten muss größer als 0 sein." in errors


# ------------------------------------------------------- _parse_items

def test_parse_items_liest_parallele_listen():
    items, errors = _parse_items(form(
        item_description=["Brot", "Milch"],
        item_amount=["3,50", "1.25"],
        item_category_id=["4", ""],
    ))

    assert errors == []
    assert items == [
        {"description": "Brot", "amount": 3.5, "category_id": 4},
        {"description": "Milch", "amount": 1.25, "category_id": None},
    ]


def test_parse_items_ignoriert_leere_zeilen():
    items, errors = _parse_items(form(
        item_description=["Brot", "", ""],
        item_amount=["3,50", "", ""],
        item_category_id=["", "", ""],
    ))

    assert errors == []
    assert len(items) == 1


def test_parse_items_erlaubt_negative_betraege():
    items, errors = _parse_items(form(
        item_description=["Pfand"], item_amount=["-0,25"], item_category_id=[""],
    ))

    assert errors == []
    assert items[0]["amount"] == pytest.approx(-0.25)


def test_parse_items_verbietet_null():
    items, errors = _parse_items(form(
        item_description=["Gratis"], item_amount=["0"], item_category_id=[""],
    ))

    assert items == []
    assert errors == ["Posten 1: Der Betrag darf nicht 0 sein."]


def test_parse_items_meldet_ungueltigen_betrag_mit_zeilennummer():
    _, errors = _parse_items(form(
        item_description=["Brot", "Milch"],
        item_amount=["3,50", "keine Zahl"],
        item_category_id=["", ""],
    ))

    assert errors == ["Posten 2: Bitte einen gültigen Betrag angeben."]


def test_parse_items_vertraegt_ungleich_lange_listen():
    # item_amount ist fuehrend; fehlende Beschreibungen gelten als leer.
    items, errors = _parse_items(form(item_amount=["3,50", "1,00"], item_description=["Brot"]))

    assert errors == []
    assert [i["description"] for i in items] == ["Brot", ""]


def test_parse_items_ohne_posten():
    assert _parse_items(form()) == ([], [])


# --------------------------------------------- _validate_category_form

def test_category_form_gueltig(test_user_id):
    with db.db_session() as conn:
        assert _validate_category_form(conn, test_user_id, "Tanken", "Ausgabe", None) == []


@pytest.mark.parametrize("name,kind", [("", "Ausgabe"), ("Tanken", "Quatsch"), ("", "")])
def test_category_form_braucht_name_und_art(test_user_id, name, kind):
    with db.db_session() as conn:
        errors = _validate_category_form(conn, test_user_id, name, kind, None)

    assert errors == ["Bitte Name und Art der Kategorie angeben."]


def test_category_form_selbstreferenz(make):
    kat = make.category_id("Freizeit")

    with db.db_session() as conn:
        errors = _validate_category_form(conn, make.default_user_id, "Freizeit", "Ausgabe", kat, current_id=kat)

    assert "eigene übergeordnete Kategorie" in errors[0]


def test_category_form_unbekanntes_elternteil(test_user_id):
    with db.db_session() as conn:
        errors = _validate_category_form(conn, test_user_id, "Waise", "Ausgabe", 9999)

    assert errors == ["Übergeordnete Kategorie wurde nicht gefunden."]


def test_category_form_dritte_ebene(make):
    haupt = make.category_id("Lebensmittel")
    unter = make.category("Brot", kind="Ausgabe", parent=haupt)

    with db.db_session() as conn:
        errors = _validate_category_form(conn, make.default_user_id, "Vollkorn", "Ausgabe", unter)

    assert "nur zwei Ebenen" in errors[0]


def test_category_form_art_muss_passen(make):
    haupt = make.category_id("Lebensmittel")  # Ausgabe

    with db.db_session() as conn:
        errors = _validate_category_form(conn, make.default_user_id, "Bonus", "Einnahme", haupt)

    assert errors == ["Die Art muss mit der übergeordneten Kategorie übereinstimmen."]


# ---------------------------------------------------------- Filter

@pytest.mark.parametrize("wert,erwartet", [
    (1234.5, "1.234,50 €"),
    (0, "0,00 €"),
    (-12.5, "-12,50 €"),
    (1234567.891, "1.234.567,89 €"),
    ("42", "42,00 €"),
])
def test_eur_filter(wert, erwartet):
    assert format_eur(wert) == erwartet


def test_eur_filter_gibt_unbrauchbaren_wert_zurueck():
    assert format_eur(None) is None
    assert format_eur("keine Zahl") == "keine Zahl"


def test_de_date_filter():
    assert format_de_date("2026-07-13") == "13.07.2026"


def test_de_date_filter_gibt_unbrauchbaren_wert_zurueck():
    assert format_de_date("13.07.2026") == "13.07.2026"
    assert format_de_date(None) is None
