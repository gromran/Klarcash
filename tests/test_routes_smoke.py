"""Smoke-Tests aller Routen, plus Filter, Paginierung und Kontenverwaltung.

Der Lauf gegen die LEERE Datenbank ist der eigentliche Wert dieser Datei:
leere Aggregate (SUM ueber 0 Zeilen -> NULL, max() ohne default) sind die
haeufigste Ursache fuer 500er auf frisch installierten Instanzen.
"""

import pytest

from conftest import fetchall, scalar

GET_ROUTEN = [
    "/",
    "/buchungen",
    "/papierkorb",
    "/konten",
    "/konten/neu",
    "/kategorien",
    "/statistiken",
    "/berichte",
    "/berichte/export.csv",
]


@pytest.fixture
def befuellt(make):
    """Eine Datenbank mit je einer Buchung jeder Art, inkl. Postenbuchung."""
    giro = make.account(name="Girokonto", initial=1000.0)
    bar = make.account(name="Barkasse", type="Bar", initial=50.0)
    make.account(name="Depot", type="Anlage", initial=5000.0, archived=1)

    make.tx(giro, type="Einnahme", amount=2000.0, category=make.category_id("Gehalt"),
            date="2026-07-01", description="Gehalt Juli")
    make.tx(giro, type="Ausgabe", amount=800.0, category=make.category_id("Miete"),
            date="2026-07-02", description="Miete")
    make.tx(giro, type="Umbuchung", amount=100.0, target=bar, date="2026-07-03",
            description="Abhebung")
    make.tx(giro, type="Ausgabe", amount=25.0, date="2026-07-04", description="Papierkorb",
            deleted=1)

    posten_tx = make.tx(bar, type="Ausgabe", amount=13.75, category=None, date="2026-07-05",
                        description="Supermarkt")
    make.items(posten_tx, [
        ("Brot", 3.50, make.category_id("Lebensmittel")),
        ("Kino", 10.50, make.category_id("Freizeit")),
        ("Pfand", -0.25, None),
    ])
    return {"giro": giro, "bar": bar, "posten_tx": posten_tx}


@pytest.mark.parametrize("route", GET_ROUTEN)
def test_get_route_auf_leerer_db(client, route):
    assert client.get(route).status_code == 200


@pytest.mark.parametrize("route", GET_ROUTEN)
def test_get_route_auf_befuellter_db(client, befuellt, route):
    assert client.get(route).status_code == 200


def test_formularseiten_auf_befuellter_db(client, befuellt):
    assert client.get("/buchungen/neu").status_code == 200
    assert client.get(f"/buchungen/{befuellt['posten_tx']}/bearbeiten").status_code == 200

    kat = scalar("SELECT id FROM categories WHERE name = 'Miete'")
    assert client.get(f"/kategorien/{kat}/bearbeiten").status_code == 200


def test_buchung_neu_ohne_konto_leitet_zur_kontoanlage(client, initialized_db):
    resp = client.get("/buchungen/neu")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/konten/neu"


def test_dashboard_zeigt_gesamtbestand(client, befuellt):
    text = client.get("/").get_data(as_text=True)

    # 1000 + 2000 - 800 - 100 = 2100 (Giro), 50 + 100 - 13,75 = 136,25 (Bar).
    # Das archivierte Depot zaehlt nicht mit.
    assert "2.100,00 €" in text
    assert "136,25 €" in text


# ------------------------------------------------- Filter der Buchungsliste

def test_filter_konto(client, befuellt):
    text = client.get("/buchungen", query_string={"konto": befuellt["bar"]}).get_data(as_text=True)

    assert "Supermarkt" in text
    assert "Abhebung" in text  # Umbuchung ZUM Barkonto zaehlt auch
    assert "Gehalt Juli" not in text


def test_filter_typ(client, befuellt):
    text = client.get("/buchungen", query_string={"typ": "Einnahme"}).get_data(as_text=True)

    assert "Gehalt Juli" in text
    assert "Miete" not in text


def test_filter_datum(client, befuellt):
    text = client.get(
        "/buchungen", query_string={"von": "2026-07-03", "bis": "2026-07-05"}
    ).get_data(as_text=True)

    assert "Abhebung" in text
    assert "Supermarkt" in text
    assert "Gehalt Juli" not in text


def test_filter_suche(client, befuellt):
    text = client.get("/buchungen", query_string={"suche": "markt"}).get_data(as_text=True)

    assert "Supermarkt" in text
    assert "Gehalt Juli" not in text


def test_filter_zeigt_geloeschte_nie(client, befuellt):
    text = client.get("/buchungen", query_string={"suche": "Papierkorb"}).get_data(as_text=True)

    assert "Papierkorb</td>" not in text  # nur der Navigationslink heisst so


# ------------------------------------------------------------ Paginierung

def test_paginierung(client, make):
    import re

    import db as db_module

    acc = make.account()
    for i in range(30):
        make.tx(acc, type="Ausgabe", amount=1.0, description=f"Buchung {i:02d}",
                date=f"2026-07-{(i % 28) + 1:02d}")

    assert db_module.PAGE_SIZE == 25

    def buchungen_auf(seite):
        html = client.get("/buchungen", query_string={"seite": seite}).get_data(as_text=True)
        return set(re.findall(r"Buchung \d\d", html))

    seite1, seite2 = buchungen_auf(1), buchungen_auf(2)

    assert len(seite1) == 25
    assert len(seite2) == 5
    assert seite1.isdisjoint(seite2)  # keine Buchung taucht doppelt auf


def test_paginierung_klemmt_unsinnige_seiten(client, make):
    acc = make.account()
    make.tx(acc, description="Einzige")

    assert client.get("/buchungen", query_string={"seite": 0}).status_code == 200
    assert b"Einzige" in client.get("/buchungen", query_string={"seite": 99}).data


# ------------------------------------------------------------- Konten

def test_konto_anlegen(client, initialized_db):
    resp = client.post(
        "/konten/neu",
        data={"name": "Girokonto", "type": "Konto", "initial_balance": "1234,56"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/konten"
    konto = fetchall("SELECT * FROM accounts")[0]
    assert (konto["name"], konto["type"], konto["initial_balance"]) == ("Girokonto", "Konto", 1234.56)


def test_konto_anlegen_mit_tausenderpunkt_scheitert(client, initialized_db):
    """Dokumentiert das Ist-Verhalten: die Eingabe wird stumpf per
    replace(",", ".") normalisiert. "1.234,56" wird damit zu "1.234.56"
    und ist keine gueltige Zahl mehr - obwohl das UI Betraege genau so
    anzeigt (eur-Filter). Siehe Befundliste."""
    resp = client.post(
        "/konten/neu",
        data={"name": "Girokonto", "type": "Konto", "initial_balance": "1.234,56"},
    )

    assert resp.status_code == 200
    assert "Der Anfangsbestand muss eine Zahl sein." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM accounts") == 0


@pytest.mark.parametrize("daten,fehler", [
    ({"name": "", "type": "Konto"}, "Bitte einen Kontonamen angeben."),
    ({"name": "X", "type": "Sparstrumpf"}, "Bitte einen gültigen Kontotyp wählen."),
    ({"name": "X", "type": "Konto", "initial_balance": "viel"}, "Der Anfangsbestand muss eine Zahl sein."),
])
def test_konto_anlegen_validierung(client, initialized_db, daten, fehler):
    resp = client.post("/konten/neu", data=daten, follow_redirects=True)

    assert resp.status_code == 200  # Re-Render statt Redirect
    assert fehler in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM accounts") == 0


def test_konto_archivieren_toggelt(client, make):
    acc = make.account(name="Altkonto")

    client.post(f"/konten/{acc}/archivieren")
    assert scalar("SELECT archived FROM accounts WHERE id = ?", (acc,)) == 1

    client.post(f"/konten/{acc}/archivieren")
    assert scalar("SELECT archived FROM accounts WHERE id = ?", (acc,)) == 0


def test_kontenuebersicht_zeigt_auch_archivierte(client, make):
    make.account(name="Aktivkonto")
    make.account(name="Altlast", archived=1)

    text = client.get("/konten").get_data(as_text=True)

    assert "Aktivkonto" in text
    assert "Altlast" in text


# --------------------------------------------------------- Buchung anlegen

def test_buchung_anlegen_ueber_die_route(client, make):
    acc = make.account(initial=100.0)
    kat = make.category_id("Miete")

    resp = client.post("/buchungen/neu", data={
        "type": "Ausgabe", "account_id": acc, "date": "2026-07-01",
        "description": "Juli-Miete", "category_id": kat, "amount": "800,00",
    })

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    row = fetchall("SELECT * FROM transactions")[0]
    assert (row["amount"], row["description"], row["category_id"]) == (800.0, "Juli-Miete", kat)


def test_buchung_anlegen_mit_fehler_rendert_neu(client, make):
    acc = make.account()

    resp = client.post("/buchungen/neu", data={
        "type": "Ausgabe", "account_id": acc, "amount": "keine Zahl",
    })

    assert resp.status_code == 200
    assert "Bitte einen gültigen Betrag angeben." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM transactions") == 0
