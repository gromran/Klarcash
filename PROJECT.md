# Hauptbuch – Ausgabenverwaltung

Projekt-Zusammenfassung als Kontext für die Weiterentwicklung mit Claude Code.
Stand: siehe Git-Historie / Dateidatum. Diese Datei beschreibt Architektur,
Datenmodell und Konventionen, damit neue Sessions ohne erneutes Erkunden des
Codes produktiv weiterarbeiten können.

## Zweck & Stack

Lokale Webanwendung zur Verwaltung von Einnahmen, Ausgaben und
Kontobeständen (Konto / Bar / Anlage). Einzelnutzer, kein Login, läuft
komplett offline auf `localhost`.

- **Backend:** Python 3, Flask (`app.py`), kein ORM
- **Datenhaltung:** SQLite (`db.py`), rohes SQL über `sqlite3`
- **Frontend:** Jinja2-Templates, eigenes CSS (`static/style.css`,
  "Ledger"-Design mit Fraunces/Inter/IBM Plex Mono), kein Build-Step
- **Diagramme:** Chart.js per CDN (nur in `templates/statistics.html`)
- **Keine** JS-Frameworks, kein npm, keine zusätzlichen Python-Pakete außer
  Flask (siehe `requirements.txt`)

Start: `python app.py` → `http://127.0.0.1:5000`. Bei fehlender `ausgaben.db`
legt `db.init_db()` beim Start automatisch das Schema an.

## Dateistruktur

```
app.py                       Alle Flask-Routen (ein File, ~800 Zeilen)
db.py                        Schema, Migration, alle SQL-Queries
requirements.txt             Nur Flask
requirements-dev.txt         Flask + pytest (nur zum Testen)
pytest.ini                   pytest-Konfiguration (testpaths, pythonpath)
tests/                       pytest-Suite (siehe "Test-Vorgehen")
static/style.css             Design-System (CSS-Variablen, Ledger-Optik)
templates/
  base.html                  Layout, Sidebar-Navigation, Flash-Messages
  dashboard.html              Startseite: Kontoübersicht, Monats-KPIs, letzte Buchungen
  transactions_list.html      Buchungsliste: Filter, Suche, Paginierung
  transaction_form.html       Buchung anlegen/bearbeiten, inkl. Kassenzettel-Posten-UI
  accounts.html / account_form.html   Kontenverwaltung
  categories.html / category_form.html  Kategorienverwaltung (mit Unterkategorien)
  statistics.html             Dashboard-Charts (Chart.js): Monat, Vermögen, Kategorie-Donuts
  reports.html                 Frei gruppierbare Berichte + CSV-Export
  trash.html                   Papierkorb (Soft-Delete)
README.md                    Nutzerdokumentation (Setup, Feature-Liste)
PROJECT.md                   Diese Datei
```

## Datenmodell (SQLite, siehe `db.py::SCHEMA`)

```
accounts
  id, name, type ('Konto'|'Bar'|'Anlage'), initial_balance,
  archived, created_at

categories
  id, name (UNIQUE), kind ('Einnahme'|'Ausgabe'), parent_id → categories.id
  -- Unterkategorien: NUR 2 Ebenen, keine tiefere Verschachtelung erlaubt

transactions
  id, date, account_id → accounts, type ('Einnahme'|'Ausgabe'|'Umbuchung'),
  category_id → categories (NULL wenn Posten verwendet werden oder bei Umbuchung),
  description, amount (immer > 0, CHECK-Constraint),
  target_account_id → accounts (nur bei Umbuchung gesetzt),
  deleted, deleted_at   -- Soft-Delete / Papierkorb
  created_at

transaction_items
  id, transaction_id → transactions, description, amount (kann NEGATIV sein!),
  category_id → categories, position
  -- Kassenzettel-Posten: optional, mehrere pro Buchung.
  -- amount ist bei Posten NICHT auf > 0 begrenzt (Pfandrückgabe etc.)
```

**Wichtige Invarianten:**

- `transactions.amount` ist immer die Summe aller zugehörigen
  `transaction_items` (falls vorhanden) oder ein manuell eingegebener Wert
  (falls keine Posten). Kontostände rechnen **ausschließlich** mit
  `transactions.amount` — Posten sind nur für Kategorie-Auswertungen relevant,
  nie für Salden.
- Hat eine Buchung Posten, ist `transactions.category_id = NULL` — die
  Kategorie-Zuordnung lebt dann auf Posten-Ebene.
- Soft-Delete: Gelöschte Buchungen bekommen `deleted = 1`, bleiben aber in
  der DB (Papierkorb). Alle Salden-/Statistik-/Report-Queries filtern
  konsequent `deleted = 0`. Endgültiges Löschen (`transaction_purge`) entfernt
  auch zugehörige `transaction_items`.
- Umbuchungen zählen nicht als Einnahme/Ausgabe in Statistiken und wirken
  sich nicht auf die Gesamt-Vermögens-Zeitreihe (`net_worth_series`) aus.

## Migration (`db.py::_migrate`)

Bestehende `ausgaben.db`-Dateien werden bei jedem Start automatisch auf das
aktuelle Schema gehoben (`PRAGMA table_info` prüft auf fehlende Spalten,
`CREATE TABLE IF NOT EXISTS` legt fehlende Tabellen nach). Bisher migriert:
`deleted`/`deleted_at` (Papierkorb), `categories.parent_id`
(Unterkategorien), `transaction_items`-Tabelle (Kassenzettel-Posten).

**Bei jeder künftigen Schema-Änderung:** `_migrate()` entsprechend erweitern
und mit einer simulierten "alten" DB (ohne die neue Spalte/Tabelle) testen,
bevor etwas an `init_db()` geändert wird — siehe Testmuster unten.

## Routen-Übersicht (`app.py`)

| Bereich | Routen |
|---|---|
| Dashboard | `GET /` |
| Buchungen | `GET /buchungen` (Filter: `konto`, `typ`, `von`, `bis`, `suche`, `seite`), `GET/POST /buchungen/neu`, `GET/POST /buchungen/<id>/bearbeiten`, `POST /buchungen/<id>/loeschen` |
| Papierkorb | `GET /papierkorb`, `POST /papierkorb/<id>/wiederherstellen`, `POST /papierkorb/<id>/endgueltig-loeschen` |
| Konten | `GET /konten`, `GET/POST /konten/neu`, `POST /konten/<id>/archivieren` |
| Kategorien | `GET/POST /kategorien`, `GET/POST /kategorien/<id>/bearbeiten`, `POST /kategorien/<id>/loeschen` |
| Statistiken | `GET /statistiken` (Query: `zeitraum` = 6/12/24/alle) |
| Berichte | `GET /berichte` (Query: `gruppierung`, `arten`, `von`, `bis`, `konto`), `GET /berichte/export.csv` |

**Wichtige Helper-Funktionen in `app.py`:**
- `_validate_transaction_form(form, items_total=None)` — zentrale Validierung
  für Neuanlage UND Bearbeitung von Buchungen. `items_total` überschreibt den
  Betrag, wenn Kassenzettel-Posten verwendet werden.
- `_parse_items(form)` — liest die parallelen Formular-Listen
  (`item_description[]`, `item_category_id[]`, `item_amount[]`) zu einer
  Liste von Posten-Dicts, ignoriert leere Zeilen, validiert Beträge.
- `_validate_category_form(conn, name, kind, parent_id, current_id=None)` —
  zentrale Validierung für Kategorie-Anlage/-Bearbeitung (Ebenen-Limit,
  Art-Konsistenz, Selbstreferenz-Schutz).
- `_build_report(...)` / `GROUP_OPTIONS` in der Berichte-Route — generisches
  Gruppierungssystem (Kategorie, Hauptkategorie, Konto, Monat, Quartal, Jahr,
  Wochentag, Art), teils mit `use_items=True` für Posten-bewusste Auswertung.

## Feature-Liste (chronologisch entstanden)

1. Grundgerüst: Konten (Konto/Bar/Anlage), Buchungen (Einnahme/Ausgabe/
   Umbuchung), Dashboard, Buchungsliste mit Filtern
2. Statistiken-Seite: Monatsverlauf (Balken), Vermögensentwicklung (Linie),
   Kategorie-Verteilung (Donut) — alles Chart.js
3. Buchungen bearbeiten, Volltextsuche, Paginierung (25/Seite), Papierkorb
   (Soft-Delete statt Hard-Delete)
4. Erststart legt **keine** Konten mehr automatisch an (nur Kategorien) —
   `/buchungen/neu` leitet ohne vorhandenes Konto zu `/konten/neu` um.
   Unterkategorien (2 Ebenen, optional, z. B. Lebensmittel → Brot)
5. Berichte-Seite: freie Gruppierung + Filter + CSV-Export
6. Kategorien bearbeiten (Name, Art, übergeordnete Kategorie), inkl.
   Kaskadierung der Art auf Unterkategorien beim Ändern der Hauptkategorie
7. Kassenzettel-Posten: Buchungen optional in einzelne Posten aufteilbar
   (Beschreibung, Betrag, Kategorie je Posten), Beträge dürfen negativ sein
   (z. B. Pfandrückgabe). Summe der Posten wird automatisch zum
   Buchungsbetrag. Statistiken/Berichte lösen Posten-Kategorien korrekt auf.

## Bekannte, bewusst nicht umgesetzte Punkte (Ideen für Weiterentwicklung)

Aus einer früheren Ideensammlung im Chat, noch nicht umgesetzt:

- Wiederkehrende Buchungen (Miete, Gehalt, Abos) mit automatischer Anlage
- Budgets pro Kategorie mit Fortschrittsbalken
- Beleg-Anhänge (Foto/PDF je Buchung)
- Backup-Funktion im UI (DB-Datei als Download anbieten)
- Jahresvergleich in den Statistiken
- Vermögensentwicklung je Konto (statt nur Gesamtsumme)
- Sparquote als KPI
- CSV-Import von Bank-Kontoauszügen mit Regel-basierter Kategorie-Zuordnung
- Login-Schutz (falls Mehrbenutzer-/Netzwerkbetrieb gewünscht ist);
  aktuell ist `app.secret_key` in `app.py` bewusst simpel gehalten, da nur
  für Flash-Messages auf `localhost` gedacht

## Konventionen für Weiterarbeit

- **Sprache:** Alle UI-Texte, Flash-Messages, Kommentare und Commit-relevanten
  Inhalte auf Deutsch. Variablen-/Funktionsnamen in Code auf Englisch/Deutsch
  gemischt wie im Bestand (z. B. `transaction_new`, aber `tx_type`,
  `date_from`).
- **Geldbeträge:** Im UI immer deutsches Format (Komma als Dezimaltrennzeichen,
  Punkt als Tausendertrennzeichen) über den Jinja-Filter `eur` in `app.py`.
  Formulareingaben akzeptieren sowohl `,` als auch `.` (`.replace(",", ".")`
  vor `float()`).
- **Datumsformat:** Intern immer ISO (`YYYY-MM-DD`), Anzeige über den
  Jinja-Filter `de_date` (`DD.MM.YYYY`).
- **Neue Datenbankspalten/-tabellen:** Immer in `SCHEMA`/`SCHEMA_INDEXES`
  UND in `_migrate()` ergänzen, sonst brechen bestehende Installationen.
- **Neue Buchungsfilter/-felder:** Wenn sie Kontostände oder Kategorie-Summen
  betreffen, daran denken, dass es zwei Auswertungspfade gibt:
  `transactions` direkt (für Kontostände, einfache Listen) und den
  `FLATTENED_CTE`-Pfad in `db.py` (für Kategorie-Auswertungen, die Posten
  berücksichtigen müssen).
- **Validierungslogik** gehört in die zentralen `_validate_*`-Funktionen in
  `app.py`, nicht dupliziert in einzelne Routen — Neuanlage und Bearbeitung
  teilen sich diese Funktionen bewusst.

## Test-Vorgehen (pytest-Suite in `tests/`)

```
venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
```

190 Tests, Laufzeit ~30 s. pytest ist die **einzige** zusätzliche
Abhängigkeit und steht bewusst nur in `requirements-dev.txt` — die
Produktiv-`requirements.txt` bleibt bei `Flask>=3.0`.

**DB-Isolation (wichtigste Regel):** Die echte `ausgaben.db` darf von Tests
niemals berührt werden. `tests/conftest.py` biegt `db.DB_PATH` deshalb per
**autouse**-Fixture für *jeden* Test auf eine Wegwerf-DB in `tmp_path` um —
auch für Tests, die die DB-Fixtures gar nicht anfordern. Das funktioniert,
weil `db.get_connection()` die Konstante bei jedem Aufruf frisch aus dem
Modulnamensraum liest. Eine session-weite Fixture prüft am Ende zusätzlich,
dass Größe und mtime der echten DB unverändert sind. `tests/test_isolation.py`
testet diese Schutzmechanik selbst.

`:memory:` funktioniert **nicht** — jede `db_session()` öffnet eine neue
Connection und bekäme damit eine eigene, leere In-Memory-DB.

| Datei | Deckt ab |
|---|---|
| `conftest.py` | Fixtures (`client`, `make`-Factory, Query-Helfer), DB-Isolation |
| `test_isolation.py` | Beweist, dass die echte DB unberührt bleibt |
| `test_migration.py` | `init_db`/`_migrate` inkl. nachgebauter "alter" DB |
| `test_balances.py` | Kontostände, Umbuchungen, Salden-vs-Posten-Invariante |
| `test_trash.py` | Soft-Delete, Wiederherstellen, endgültiges Löschen |
| `test_categories.py` | Ebenen-Limit, Art-Konsistenz, Löschsperren, Kaskadierung |
| `test_items.py` | Kassenzettel-Posten (Anlegen, Bearbeiten, Auswertung) |
| `test_statistics.py` | `monthly_summary`, `net_worth_series`, `category_breakdown` |
| `test_reports.py` | `grouped_report` (beide Pfade), `/berichte`, CSV-Export |
| `test_routes_smoke.py` | Alle GET-Routen (leere **und** befüllte DB), Filter, Paginierung, Konten |

**Testdaten** werden über die `make`-Factory per direktem SQL angelegt
(`make.account()`, `make.category()`, `make.tx()`, `make.items()`).
Routen-POSTs nur dort, wo die Route selbst unter Test steht.

**Bei jeder Änderung mitziehen:**
- Neue Schema-Spalte/-Tabelle → Test in `test_migration.py`, der die Migration
  einer DB *ohne* diese Spalte prüft (`conftest.old_schema_db()` als Vorlage).
- Neue Route → Eintrag in `GET_ROUTEN` in `test_routes_smoke.py`.
- Neue Auswertung → daran denken, dass es zwei Pfade gibt (`transactions`
  direkt vs. `FLATTENED_CTE`); beide brauchen einen Test.

## Offene Kleinigkeiten / Nice-to-haves aus dem Code selbst

- `app.secret_key` ist ein Platzhalter-String — für Betrieb außerhalb von
  `localhost` durch echten Zufallswert ersetzen (README weist darauf hin).
- CSS ist ein einzelnes File ohne Präprozessor — bei wachsendem Umfang ggf.
  in Abschnitte aufteilen (ist aktuell schon mit Kommentar-Überschriften
  strukturiert).
- Chart.js wird per CDN geladen (`templates/statistics.html`) — für
  echten Offline-Betrieb müsste das lokal vendored werden.
