"""Statistiken: monthly_summary, net_worth_series, category_breakdown."""

import pytest

import db


def test_monthly_summary_gruppiert_nach_monat(make):
    acc = make.account()
    make.tx(acc, type="Einnahme", amount=2000.0, date="2026-06-01")
    make.tx(acc, type="Ausgabe", amount=500.0, date="2026-06-15")
    make.tx(acc, type="Ausgabe", amount=300.0, date="2026-07-02")

    with db.db_session() as conn:
        summary = db.monthly_summary(conn, "2026-01-01", make.default_user_id)

    assert summary["2026-06"] == {"einnahmen": 2000.0, "ausgaben": 500.0}
    assert summary["2026-07"] == {"einnahmen": 0, "ausgaben": 300.0}


def test_monthly_summary_respektiert_startdatum(make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=100.0, date="2025-01-01")
    make.tx(acc, type="Ausgabe", amount=200.0, date="2026-07-01")

    with db.db_session() as conn:
        summary = db.monthly_summary(conn, "2026-01-01", make.default_user_id)

    assert list(summary) == ["2026-07"]


def test_monthly_summary_ignoriert_geloeschte(make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=100.0, date="2026-07-01", deleted=1)

    with db.db_session() as conn:
        assert db.monthly_summary(conn, "2026-01-01", make.default_user_id) == {}


def test_monthly_summary_ignoriert_umbuchungen(make):
    quelle = make.account(name="Giro")
    ziel = make.account(name="Bar", type="Bar")
    make.tx(quelle, type="Umbuchung", amount=100.0, target=ziel, date="2026-07-01")

    with db.db_session() as conn:
        summary = db.monthly_summary(conn, "2026-01-01", make.default_user_id)

    assert summary["2026-07"] == {"einnahmen": 0, "ausgaben": 0}


# --------------------------------------------------- net_worth_series

def test_net_worth_series_kumuliert(make):
    acc = make.account(initial=1000.0)
    make.tx(acc, type="Einnahme", amount=500.0, date="2026-06-01")
    make.tx(acc, type="Ausgabe", amount=200.0, date="2026-06-15")

    with db.db_session() as conn:
        start, series = db.net_worth_series(conn, make.default_user_id)

    assert start == 1000.0
    assert series == [
        {"date": "2026-06-01", "total": 1500.0},
        {"date": "2026-06-15", "total": 1300.0},
    ]


def test_net_worth_series_ignoriert_umbuchungen(make):
    quelle = make.account(name="Giro", initial=1000.0)
    ziel = make.account(name="Bar", type="Bar", initial=0.0)
    make.tx(quelle, type="Umbuchung", amount=300.0, target=ziel, date="2026-06-01")

    with db.db_session() as conn:
        start, series = db.net_worth_series(conn, make.default_user_id)

    assert start == 1000.0
    assert series == [{"date": "2026-06-01", "total": 1000.0}]  # Summe unveraendert


def test_net_worth_series_ignoriert_archivierte_konten(make):
    make.account(name="Aktiv", initial=1000.0)
    archiviert = make.account(name="Alt", initial=5000.0, archived=1)
    make.tx(archiviert, type="Einnahme", amount=999.0, date="2026-06-01")

    with db.db_session() as conn:
        start, series = db.net_worth_series(conn, make.default_user_id)

    assert start == 1000.0
    assert series == []


def test_net_worth_series_ignoriert_geloeschte(make):
    acc = make.account(initial=1000.0)
    make.tx(acc, type="Ausgabe", amount=100.0, date="2026-06-01", deleted=1)

    with db.db_session() as conn:
        start, series = db.net_worth_series(conn, make.default_user_id)

    assert (start, series) == (1000.0, [])


def test_net_worth_series_auf_leerer_db(test_user_id):
    with db.db_session() as conn:
        assert db.net_worth_series(conn, test_user_id) == (0, [])


# ------------------------------------------------- category_breakdown

def test_category_breakdown_sortiert_absteigend(make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=100.0, category=make.category_id("Freizeit"), date="2026-07-01")
    make.tx(acc, type="Ausgabe", amount=900.0, category=make.category_id("Miete"), date="2026-07-01")

    with db.db_session() as conn:
        rows = db.category_breakdown(conn, "Ausgabe", "2026-01-01", make.default_user_id)

    assert [r["category"] for r in rows] == ["Miete", "Freizeit"]
    assert rows[0]["summe"] == 900.0


def test_category_breakdown_ohne_kategorie(make):
    acc = make.account()
    make.tx(acc, type="Ausgabe", amount=50.0, category=None, date="2026-07-01")

    with db.db_session() as conn:
        rows = db.category_breakdown(conn, "Ausgabe", "2026-01-01", make.default_user_id)

    assert rows[0]["category"] == "Ohne Kategorie"


def test_category_breakdown_trennt_nach_art(make):
    acc = make.account()
    make.tx(acc, type="Einnahme", amount=2000.0, category=make.category_id("Gehalt"), date="2026-07-01")
    make.tx(acc, type="Ausgabe", amount=900.0, category=make.category_id("Miete"), date="2026-07-01")

    with db.db_session() as conn:
        einnahmen = db.category_breakdown(conn, "Einnahme", "2026-01-01", make.default_user_id)
        ausgaben = db.category_breakdown(conn, "Ausgabe", "2026-01-01", make.default_user_id)

    assert [r["category"] for r in einnahmen] == ["Gehalt"]
    assert [r["category"] for r in ausgaben] == ["Miete"]


def test_earliest_transaction_date(make):
    acc = make.account()
    make.tx(acc, date="2026-07-01")
    make.tx(acc, date="2025-03-15")
    make.tx(acc, date="2024-01-01", deleted=1)  # zaehlt nicht

    with db.db_session() as conn:
        assert db.earliest_transaction_date(conn, make.default_user_id) == "2025-03-15"


# ----------------------------------------------------------- Route

@pytest.mark.parametrize("zeitraum", ["6", "12", "24", "alle"])
def test_statistiken_route(client, make, zeitraum):
    acc = make.account(initial=1000.0)
    make.tx(acc, type="Ausgabe", amount=100.0, category=make.category_id("Miete"), date="2026-07-01")

    assert client.get(f"/statistiken?zeitraum={zeitraum}").status_code == 200


def test_statistiken_faellt_auf_12_monate_zurueck(client, make):
    resp = client.get("/statistiken?zeitraum=quatsch")

    assert resp.status_code == 200
    assert "Letzte 12 Monate" in resp.get_data(as_text=True)
