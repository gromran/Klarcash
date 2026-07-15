"""Berichte: db.grouped_report, /berichte und der CSV-Export."""

import csv
import io

import pytest

import app as app_module
import db

ZEITRAUM = {"von": "2026-01-01", "bis": "2026-12-31"}


def report(conn, expr, joins="", where="WHERE t.deleted = 0", params=(), use_items=False):
    return db.grouped_report(conn, expr, joins, where, list(params), use_items=use_items)


def test_grouped_report_summiert_je_gruppe(make):
    acc = make.account()
    make.tx(acc, type="Einnahme", amount=2000.0)
    make.tx(acc, type="Ausgabe", amount=500.0)
    make.tx(acc, type="Ausgabe", amount=300.0)

    with db.db_session() as conn:
        rows = report(conn, "t.type")

    ergebnis = {r["grp"]: (r["einnahmen"], r["ausgaben"], r["anzahl"]) for r in rows}
    assert ergebnis["Einnahme"] == (2000.0, 0, 1)
    assert ergebnis["Ausgabe"] == (0, 800.0, 2)


def test_grouped_report_zaehlt_posten_als_eine_buchung(make):
    """use_items=True loest auf Posten-Ebene auf, muss "anzahl" aber weiterhin
    pro Buchung zaehlen (COUNT DISTINCT tx_id) - sonst zaehlt ein Kassenzettel
    mit drei Posten faelschlich als drei Buchungen."""
    acc = make.account()
    kat = make.category_id("Lebensmittel")
    tx = make.tx(acc, type="Ausgabe", amount=30.0, category=None)
    make.items(tx, [("Brot", 10.0, kat), ("Milch", 10.0, kat), ("Käse", 10.0, kat)])

    joins = "LEFT JOIN categories c ON c.id = t.category_id LEFT JOIN categories p ON p.id = c.parent_id"
    with db.db_session() as conn:
        rows = report(conn, "COALESCE(c.name, 'Ohne Kategorie')", joins, use_items=True)

    assert len(rows) == 1
    assert rows[0]["grp"] == "Lebensmittel"
    assert rows[0]["ausgaben"] == pytest.approx(30.0)
    assert rows[0]["anzahl"] == 1  # nicht 3


def test_grouped_report_ohne_items_sieht_posten_nicht(make):
    """Der use_items=False-Pfad geht direkt gegen transactions - eine
    Postenbuchung hat dort category_id NULL und landet in "Ohne Kategorie"."""
    acc = make.account()
    kat = make.category_id("Lebensmittel")
    tx = make.tx(acc, type="Ausgabe", amount=30.0, category=None)
    make.items(tx, [("Brot", 30.0, kat)])

    joins = "LEFT JOIN categories c ON c.id = t.category_id"
    with db.db_session() as conn:
        rows = report(conn, "COALESCE(c.name, 'Ohne Kategorie')", joins, use_items=False)

    assert rows[0]["grp"] == "Ohne Kategorie"
    assert rows[0]["anzahl"] == 1


def test_grouped_report_ignoriert_geloeschte(make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=500.0, deleted=1)

    with db.db_session() as conn:
        assert report(conn, "t.type") == []


# ------------------------------------------------------------- Route

@pytest.mark.parametrize("gruppierung", sorted(app_module.GROUP_OPTIONS))
def test_berichte_route_fuer_jede_gruppierung(client, make, gruppierung):
    acc = make.account(name="Giro")
    make.tx(acc, type="Ausgabe", amount=500.0, category=make.category_id("Miete"), date="2026-07-01")
    make.tx(acc, type="Einnahme", amount=2000.0, category=make.category_id("Gehalt"), date="2026-06-01")

    resp = client.get("/berichte", query_string={"gruppierung": gruppierung, **ZEITRAUM})

    assert resp.status_code == 200


@pytest.mark.parametrize("arten", sorted(app_module.ARTEN_OPTIONS))
def test_berichte_route_fuer_jede_art(client, make, arten):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=500.0, date="2026-07-01")

    resp = client.get("/berichte", query_string={"arten": arten, **ZEITRAUM})

    assert resp.status_code == 200


def test_berichte_filtert_nach_konto(client, make):
    giro = make.account(name="Girokonto")
    bar = make.account(name="Barkasse", type="Bar")
    make.tx(giro, type="Ausgabe", amount=500.0, date="2026-07-01")
    make.tx(bar, type="Ausgabe", amount=25.0, date="2026-07-01")

    resp = client.get("/berichte", query_string={"gruppierung": "konto", "konto": giro, **ZEITRAUM})
    text = resp.get_data(as_text=True)

    assert "Girokonto" in text
    assert "Barkasse" not in text.split("<table")[-1]  # nicht in der Ergebnistabelle


def test_berichte_unbekannte_gruppierung_faellt_auf_kategorie_zurueck(client, make):
    make.account()

    group_by, arten, _ = app_module._build_report(make.default_user_id, "quatsch", None, None, None, "quatsch")

    assert (group_by, arten) == ("kategorie", "ein_aus")


def test_berichte_umbuchungen_nur_bei_arten_alle(client, make):
    quelle = make.account(name="Giro")
    ziel = make.account(name="Bar", type="Bar")
    make.tx(quelle, type="Umbuchung", amount=100.0, target=ziel, date="2026-07-01")

    _, _, ohne = app_module._build_report(make.default_user_id, "art", "2026-01-01", "2026-12-31", None, "ein_aus")
    _, _, mit = app_module._build_report(make.default_user_id, "art", "2026-01-01", "2026-12-31", None, "alle")

    assert ohne == []
    assert [r["label"] for r in mit] == ["Umbuchung"]


# ---------------------------------------------------------- CSV-Export

def test_csv_export_header_und_mimetype(client, make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=500.0, category=make.category_id("Miete"), date="2026-07-01")

    resp = client.get("/berichte/export.csv", query_string=ZEITRAUM)

    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment; filename=bericht_kategorie_2026-01-01_2026-12-31.csv" in \
        resp.headers["Content-Disposition"]


def test_csv_export_inhalt(client, make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=1234.5, category=make.category_id("Miete"), date="2026-07-01")

    resp = client.get("/berichte/export.csv", query_string=ZEITRAUM)
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True)), delimiter=";"))

    assert rows[0] == ["Kategorie", "Einnahmen", "Ausgaben", "Saldo", "Anzahl Buchungen"]
    # Deutsche Zahlen: Komma als Dezimaltrennzeichen.
    assert rows[1] == ["Miete", "0,00", "1234,50", "-1234,50", "1"]


def test_csv_export_auf_leerer_db(client, initialized_db):
    resp = client.get("/berichte/export.csv", query_string=ZEITRAUM)

    assert resp.status_code == 200
    zeilen = [z for z in resp.get_data(as_text=True).splitlines() if z]
    assert len(zeilen) == 1  # nur die Kopfzeile
