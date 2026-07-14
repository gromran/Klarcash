"""Kategorien: Anlegen, Bearbeiten, Loeschsperren (Pflichtfall 4 aus PROJECT.md).

Deckt die Regeln ab, die _validate_category_form (app.py) durchsetzt:
zwei Ebenen, Art-Konsistenz, Selbstreferenz - sowie die Loeschsperren in
db.category_in_use / db.category_has_children.
"""

import db
from conftest import fetchone, scalar


def anlegen(client, name, kind="Ausgabe", parent=""):
    return client.post(
        "/kategorien",
        data={"name": name, "kind": kind, "parent_id": parent},
        follow_redirects=True,
    )


def bearbeiten(client, cat_id, name, kind="Ausgabe", parent=""):
    return client.post(
        f"/kategorien/{cat_id}/bearbeiten",
        data={"name": name, "kind": kind, "parent_id": parent},
        follow_redirects=True,
    )


# ------------------------------------------------------------ Anlegen

def test_kategorie_anlegen(client, make):
    resp = anlegen(client, "Tanken")

    assert "Kategorie angelegt." in resp.get_data(as_text=True)
    kat = fetchone("SELECT * FROM categories WHERE name = 'Tanken'")
    assert kat["kind"] == "Ausgabe"
    assert kat["parent_id"] is None


def test_unterkategorie_anlegen(client, make):
    parent = make.category_id("Lebensmittel")

    resp = anlegen(client, "Brot", kind="Ausgabe", parent=parent)

    assert "Kategorie angelegt." in resp.get_data(as_text=True)
    assert scalar("SELECT parent_id FROM categories WHERE name = 'Brot'") == parent


def test_doppelter_name_wird_abgelehnt(client, make):
    resp = anlegen(client, "Lebensmittel")  # existiert als Standardkategorie

    assert "Diese Kategorie existiert bereits." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Lebensmittel'") == 1


def test_doppelter_name_liefert_trotzdem_redirect(client, make):
    # Die POST-Route redirectet immer - Fehler werden nur als Flash sichtbar.
    resp = client.post("/kategorien", data={"name": "Miete", "kind": "Ausgabe"})
    assert resp.status_code == 302


def test_leerer_name_wird_abgelehnt(client, make):
    resp = anlegen(client, "")

    assert "Bitte Name und Art der Kategorie angeben." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories") == 7


def test_ungueltige_art_wird_abgelehnt(client, make):
    resp = anlegen(client, "Quatsch", kind="Vielleicht")

    assert "Bitte Name und Art der Kategorie angeben." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Quatsch'") == 0


# ------------------------------------------------- Regeln der Hierarchie

def test_dritte_ebene_ist_verboten(client, make):
    haupt = make.category_id("Lebensmittel")
    unter = make.category("Brot", kind="Ausgabe", parent=haupt)

    resp = anlegen(client, "Brötchen", kind="Ausgabe", parent=unter)

    assert "nur zwei Ebenen" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Brötchen'") == 0


def test_art_muss_zur_elternkategorie_passen(client, make):
    haupt = make.category_id("Lebensmittel")  # Ausgabe

    resp = anlegen(client, "Bonus", kind="Einnahme", parent=haupt)

    assert "Die Art muss mit der übergeordneten Kategorie übereinstimmen." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Bonus'") == 0


def test_unbekannte_elternkategorie_wird_abgelehnt(client, make):
    resp = anlegen(client, "Waise", parent=9999)

    assert "Übergeordnete Kategorie wurde nicht gefunden." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE name = 'Waise'") == 0


def test_kategorie_kann_nicht_ihr_eigenes_elternteil_sein(client, make):
    kat = make.category_id("Freizeit")

    resp = bearbeiten(client, kat, "Freizeit", parent=kat)

    assert "nicht ihre eigene übergeordnete Kategorie" in resp.get_data(as_text=True)
    assert scalar("SELECT parent_id FROM categories WHERE id = ?", (kat,)) is None


def test_kategorie_mit_kindern_wird_nicht_selbst_unterkategorie(client, make):
    haupt = make.category_id("Lebensmittel")
    make.category("Brot", kind="Ausgabe", parent=haupt)
    anderes = make.category_id("Freizeit")

    resp = bearbeiten(client, haupt, "Lebensmittel", parent=anderes)

    assert "eigene Unterkategorien" in resp.get_data(as_text=True)
    assert scalar("SELECT parent_id FROM categories WHERE id = ?", (haupt,)) is None


# ---------------------------------------------------------- Bearbeiten

def test_kategorie_umbenennen(client, make):
    kat = make.category_id("Freizeit")

    bearbeiten(client, kat, "Hobby")

    assert scalar("SELECT name FROM categories WHERE id = ?", (kat,)) == "Hobby"


def test_artwechsel_kaskadiert_auf_unterkategorien(client, make):
    haupt = make.category("Nebenjob", kind="Ausgabe")
    unter = make.category("Trinkgeld", kind="Ausgabe", parent=haupt)

    bearbeiten(client, haupt, "Nebenjob", kind="Einnahme")

    assert scalar("SELECT kind FROM categories WHERE id = ?", (haupt,)) == "Einnahme"
    assert scalar("SELECT kind FROM categories WHERE id = ?", (unter,)) == "Einnahme"


def test_bearbeiten_auf_bestehenden_namen_wird_abgelehnt(client, make):
    kat = make.category_id("Freizeit")

    resp = bearbeiten(client, kat, "Miete")

    assert "existiert bereits" in resp.get_data(as_text=True)
    assert scalar("SELECT name FROM categories WHERE id = ?", (kat,)) == "Freizeit"


def test_bearbeiten_unbekannter_kategorie_leitet_um(client):
    resp = client.get("/kategorien/9999/bearbeiten")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/kategorien"


# ------------------------------------------------------------ Loeschen

def test_kategorie_loeschen(client, make):
    kat = make.category("Ungenutzt", kind="Ausgabe")

    resp = client.post(f"/kategorien/{kat}/loeschen", follow_redirects=True)

    assert "Kategorie gelöscht." in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE id = ?", (kat,)) == 0


def test_loeschen_gesperrt_wenn_von_buchung_verwendet(client, make):
    acc = make.account()
    kat = make.category_id("Miete")
    make.tx(acc, type="Ausgabe", amount=500.0, category=kat)

    resp = client.post(f"/kategorien/{kat}/loeschen", follow_redirects=True)

    assert "wird noch von Buchungen verwendet" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE id = ?", (kat,)) == 1


def test_loeschen_gesperrt_wenn_von_posten_verwendet(client, make):
    """Zweiter Pfad von db.category_in_use: die Kategorie haengt nicht an der
    Buchung, sondern an einem einzelnen Kassenzettel-Posten."""
    acc = make.account()
    kat = make.category_id("Lebensmittel")
    tx = make.tx(acc, type="Ausgabe", amount=3.0, category=None)
    make.items(tx, [("Brot", 3.0, kat)])

    resp = client.post(f"/kategorien/{kat}/loeschen", follow_redirects=True)

    assert "wird noch von Buchungen verwendet" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE id = ?", (kat,)) == 1


def test_loeschen_gesperrt_wenn_unterkategorien_existieren(client, make):
    haupt = make.category_id("Lebensmittel")
    make.category("Brot", kind="Ausgabe", parent=haupt)

    resp = client.post(f"/kategorien/{haupt}/loeschen", follow_redirects=True)

    assert "hat noch Unterkategorien" in resp.get_data(as_text=True)
    assert scalar("SELECT COUNT(*) FROM categories WHERE id = ?", (haupt,)) == 1


# -------------------------------------------------------- category_tree

def test_category_tree_baut_zwei_ebenen(make):
    haupt = make.category_id("Lebensmittel")
    make.category("Brot", kind="Ausgabe", parent=haupt)
    make.category("Milch", kind="Ausgabe", parent=haupt)

    with db.db_session() as conn:
        baum = db.category_tree(conn, kind="Ausgabe")

    lebensmittel = next(k for k in baum if k["name"] == "Lebensmittel")
    assert [c["name"] for c in lebensmittel["children"]] == ["Brot", "Milch"]  # alphabetisch
    assert all(k["parent_id"] is None for k in baum)


def test_category_tree_filtert_nach_art(make):
    with db.db_session() as conn:
        einnahmen = db.category_tree(conn, kind="Einnahme")

    assert {k["name"] for k in einnahmen} == {"Gehalt", "Sonstige Einnahmen"}
