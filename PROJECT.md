# Hauptbuch – Ausgabenverwaltung

Projekt-Zusammenfassung als Kontext für die Weiterentwicklung mit Claude Code.
Stand: siehe Git-Historie / Dateidatum. Diese Datei beschreibt Architektur,
Datenmodell und Konventionen, damit neue Sessions ohne erneutes Erkunden des
Codes produktiv weiterarbeiten können.

## Zweck & Stack

Lokale Webanwendung zur Verwaltung von Einnahmen, Ausgaben und
Kontobeständen (Konto / Bar / Anlage). Seit Version 2.0.0
**mehrbenutzerfähig mit Login** (siehe Abschnitt "Nutzerverwaltung"); läuft
weiterhin primär offline auf `localhost`, kann per `--online` aber auch im
LAN erreichbar gemacht werden, ohne dass fremde Geräte ungeschützt auf die
Daten zugreifen können.

- **Backend:** Python 3, Flask (`app.py`), kein ORM
- **Datenhaltung:** SQLite (`db.py`), rohes SQL über `sqlite3`
- **Frontend:** Jinja2-Templates, eigenes CSS (`static/style.css`,
  "Ledger"-Design mit Fraunces/Inter/IBM Plex Mono), kein Build-Step
- **Diagramme:** Chart.js per CDN (nur in `templates/statistics.html`)
- **Keine** JS-Frameworks, kein npm, keine zusätzlichen Python-Pakete außer
  Flask (siehe `requirements.txt`) - Passwort-Hashing nutzt `werkzeug.security`,
  das als Flask-Abhängigkeit ohnehin vorhanden ist

Start: `python app.py` → `http://127.0.0.1:5000`. Bei fehlender `ausgaben.db`
legt `db.init_db()` beim Start automatisch das Schema an. Ohne vorhandenen
Nutzer leitet die App automatisch zur Ersteinrichtung (`/ersteinrichtung`).

## Dateistruktur

```
app.py                       Alle Flask-Routen (ein File, ~1370 Zeilen)
db.py                        Schema, Migration, alle SQL-Queries (~650 Zeilen)
requirements.txt             Nur Flask
requirements-dev.txt         Flask + pytest (nur zum Testen)
pytest.ini                   pytest-Konfiguration (testpaths, pythonpath)
tests/                       pytest-Suite (siehe "Test-Vorgehen")
static/style.css             Design-System (CSS-Variablen, Ledger-Optik)
.secret_key                  Generierter Session-Signierschlüssel (gitignored, siehe "Nutzerverwaltung")
templates/
  base.html                  Layout, Sidebar-Navigation (nur eingeloggt), Flash-Messages
  dashboard.html              Startseite: Kontoübersicht, Monats-KPIs, letzte Buchungen
  transactions_list.html      Buchungsliste: Filter, Suche, Paginierung
  transaction_form.html       Buchung anlegen/bearbeiten, inkl. Kassenzettel-Posten-UI
  accounts.html / account_form.html   Kontenverwaltung
  categories.html / category_form.html  Kategorienverwaltung (mit Unterkategorien)
  statistics.html             Dashboard-Charts (Chart.js): Monat, Vermögen, Kategorie-Donuts
  reports.html                 Frei gruppierbare Berichte + CSV-Export
  trash.html                   Papierkorb (Soft-Delete)
  login.html / ersteinrichtung.html   Anmeldung / Ersteinrichtung des ersten Admins
  nutzer.html / nutzer_form.html      Nutzerverwaltung (Admin-only)
  profil.html                  Eigenes Passwort ändern
  migration.html / version_error.html Schema-Versionsabgleich (siehe "Versionierung & Migration")
README.md                    Nutzerdokumentation (Setup, Feature-Liste)
PROJECT.md                   Diese Datei
ROADMAP.md                   Geplante/umgesetzte Ausbaustufen
android/app/src/main/python/ Automatisch generierter 1:1-Spiegel von app.py/db.py/
                              templates/static für die Android-WebView-App (Chaquopy) -
                              der Gradle-Task `copyPythonSources` kopiert vor jedem
                              Android-Build frisch aus dem Projekt-Root, nicht manuell pflegen
```

## Datenmodell (SQLite, siehe `db.py::SCHEMA`)

```
users
  id, username (UNIQUE), password_hash, role ('admin'|'user'), created_at

accounts
  id, name, type ('Konto'|'Bar'|'Anlage'), initial_balance,
  archived, user_id → users.id, created_at

categories
  id, name, kind ('Einnahme'|'Ausgabe'), parent_id → categories.id,
  user_id → users.id, UNIQUE(user_id, name)
  -- name ist NUR je Nutzer eindeutig (nicht mehr global) - zwei Nutzer
  -- duerfen je eine gleichnamige Kategorie haben
  -- Unterkategorien: NUR 2 Ebenen, keine tiefere Verschachtelung erlaubt

transactions
  id, date, account_id → accounts, type ('Einnahme'|'Ausgabe'|'Umbuchung'),
  category_id → categories (NULL wenn Posten verwendet werden oder bei Umbuchung),
  description, amount (immer > 0, CHECK-Constraint),
  target_account_id → accounts (nur bei Umbuchung gesetzt),
  deleted, deleted_at,   -- Soft-Delete / Papierkorb
  user_id → users.id, created_at

transaction_items
  id, transaction_id → transactions, description, amount (kann NEGATIV sein!),
  category_id → categories, position
  -- Kassenzettel-Posten: optional, mehrere pro Buchung.
  -- amount ist bei Posten NICHT auf > 0 begrenzt (Pfandrückgabe etc.)
  -- KEIN eigenes user_id - Eigentümerschaft erbt sich über transaction_id
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
- **Alle** Queries auf `accounts`/`categories`/`transactions` filtern auf den
  eingeloggten Nutzer (`user_id`). Neue Query-Helfer in `db.py` und neue
  Routen in `app.py` müssen das konsequent fortführen — siehe Abschnitt
  "Nutzerverwaltung".

## Versionierung & Migration (`db.py`)

Die App trägt eine Version `APP_VERSION` (`db.py`, Format `Major.Minor.Patch`).
Die **Major-Zahl ist die Schema-Version** — sie muss bei jeder Schema-Änderung
erhöht werden. Die DB speichert ihre eigene Version in der Tabelle
`schema_meta` (`db.db_version()`); `db.is_compatible()` vergleicht nur die
Major von Code und DB.

**Minor** wird bei neuen nutzersichtbaren Funktionen ohne Schema-Änderung
erhöht, **Patch** bei Bugfixes/Refactorings/Tests ohne neue Funktion. Beide
haben keine Laufzeitwirkung (nur Anzeige, siehe `app_version` im
Context-Processor in `app.py`), werden aber bei jeder inhaltlichen Änderung
mitgeführt — siehe `CLAUDE.md` für die genaue Regel. `APP_VERSION` muss dabei
in `db.py` **und** der gespiegelten `android/app/src/main/python/db.py`
identisch gehalten werden.

`init_db()` legt ausschließlich eine **frische** Datenbank an (leere Datei) und
stempelt sie sofort auf `APP_VERSION`. Eine **bestehende** Datenbank rührt
`init_db()` bewusst nicht an — Schema-Änderungen werden **nicht automatisch**
übernommen. Stattdessen gibt es die explizite Funktion `db.migrate()`,
erreichbar über:
- `python app.py --migrate` (CLI, beendet den Prozess danach statt den Server
  zu starten),
- die Route `GET/POST /migration` (auch aus der Android-WebView erreichbar,
  da diese denselben Flask-Code einbettet).

Weicht beim Start die Major-Version der DB von der des Codes ab (oder existiert
gar kein Versionseintrag — Alt-Installation von vor der Versionierung), sperrt
ein `before_request`-Hook (`app._require_matching_schema`) **jede** Seite mit
HTTP 409 und `templates/version_error.html`; einzig `/migration` bleibt
erreichbar. Bisher migriert (in `_migrate()`, aufgerufen von `migrate()`):
`deleted`/`deleted_at` (Papierkorb), `categories.parent_id`
(Unterkategorien), `transaction_items`-Tabelle (Kassenzettel-Posten),
`users`-Tabelle + `user_id`-Spalten an `accounts`/`transactions` sowie ein
vollständiger Tabellen-Rebuild von `categories` (Version 2.0.0, siehe
"Nutzerverwaltung" — SQLite kann ein spaltengebundenes `UNIQUE` nicht per
`ALTER TABLE` ändern).

**Bei jeder künftigen Schema-Änderung:**
1. `APP_VERSION` in `db.py` in der Major-Zahl erhöhen.
2. `_migrate()` entsprechend erweitern und mit einer simulierten "alten" DB
   (ohne die neue Spalte/Tabelle) testen, bevor etwas an `SCHEMA` geändert
   wird — siehe Testmuster unten (`tests/test_migration.py`).
3. Erfordert die Änderung einen Tabellen-Rebuild (z. B. ein geändertes
   `UNIQUE`/`CHECK`), unbedingt `PRAGMA foreign_keys = OFF` UND
   `PRAGMA legacy_alter_table = ON` **beide** vor dem `RENAME TO` setzen —
   sonst schreibt SQLite die `REFERENCES`-Klauseln anderer Tabellen
   automatisch auf den Zwischennamen um (siehe Kommentar in `db.py::_migrate()`).

## Nutzerverwaltung (`app.py`, seit Version 2.0.0)

Login ist Pflicht. Zwei `before_request`-Gates laufen nach dem Schema-Gate:
`_require_setup` (leitet auf `/ersteinrichtung` um, solange `db.user_count()
== 0`) und `_require_login` (leitet auf `/login` um, solange
`current_user()` `None` ist — liest `session["user_id"]`).

- **Session:** Flask-Bordmittel, kein `flask-login`. `app.secret_key` wird
  einmalig generiert und in `.secret_key` neben der DB persistiert
  (`_load_or_create_secret_key()`), damit Logins einen Neustart überleben.
- **Passwörter:** `werkzeug.security.generate_password_hash` /
  `check_password_hash`.
- **Rollen:** `admin` / `user`. `admin_required`-Decorator schützt
  `/nutzer*`-Routen serverseitig zusätzlich zur Nav-Sichtbarkeit
  (`base.html` zeigt den Nav-Eintrag nur für Admins).
- **Ersteinrichtung** (`/ersteinrichtung`): legt den ersten Nutzer als Admin
  an, übernimmt verwaiste Alt-Daten aus einer migrierten Vor-2.0.0-Datenbank
  (`db.claim_orphan_data()`, `user_id IS NULL` → neuer Admin) und seedet
  danach dessen Standardkategorien (`db.seed_categories_for_user()`,
  `INSERT OR IGNORE`, damit übernommene Alt-Kategorien nicht dupliziert
  werden — **Reihenfolge wichtig**: erst `claim_orphan_data()`, dann seeden).
- **IDOR-Schutz:** Jede `<int:id>`-Route prüft Eigentümerschaft
  (`WHERE id = ? AND user_id = ?`); eine fremde ID wird identisch zu einer
  nicht existierenden behandelt (kein Leck, ob eine Ressource existiert).
  Formularfelder mit Fremdschlüsseln (`account_id`, `category_id`,
  `target_account_id`, Posten-`category_id`) werden zusätzlich per
  `_validate_transaction_ownership()`/`_validate_items_ownership()` gegen
  den eingeloggten Nutzer geprüft — sonst liesse sich per manipuliertem
  Formularfeld auf fremde Konten/Kategorien zugreifen.
- **Nutzer löschen** (`/nutzer/<id>/loeschen`): blockiert Selbstlöschung
  sowie das Löschen von Nutzern mit noch vorhandenen Konten/Buchungen
  (`db.user_has_data()` — zählt bewusst **keine** Kategorien, da jeder
  Nutzer sofort Standardkategorien bekommt und ein "datenloser" Nutzer sonst
  nie löschbar wäre; `db.delete_user()` räumt dessen Kategorien automatisch
  mit ab).

## Routen-Übersicht (`app.py`)

| Bereich | Routen |
|---|---|
| Dashboard | `GET /` |
| Buchungen | `GET /buchungen` (Filter: `konto`, `typ`, `von`, `bis`, `suche`, `seite`), `GET/POST /buchungen/neu`, `GET/POST /buchungen/<id>/bearbeiten`, `POST /buchungen/<id>/loeschen` |
| Papierkorb | `GET /papierkorb`, `POST /papierkorb/<id>/wiederherstellen`, `POST /papierkorb/<id>/endgueltig-loeschen` |
| Konten | `GET /konten`, `GET/POST /konten/neu`, `POST /konten/<id>/archivieren` |
| Kategorien | `GET/POST /kategorien`, `POST /kategorien/neu.json` (AJAX-Inline-Anlage aus dem Buchungsformular), `GET/POST /kategorien/<id>/bearbeiten`, `POST /kategorien/<id>/loeschen` |
| Statistiken | `GET /statistiken` (Query: `zeitraum` = 6/12/24/alle) |
| Berichte | `GET /berichte` (Query: `gruppierung`, `arten`, `von`, `bis`, `konto`), `GET /berichte/export.csv` |
| Migration | `GET/POST /migration` (Versionsanzeige + expliziter Migrations-Trigger, siehe Abschnitt "Versionierung & Migration") |
| Anmeldung | `GET/POST /login`, `POST /logout`, `GET/POST /ersteinrichtung` |
| Nutzerverwaltung | `GET /nutzer`, `GET/POST /nutzer/neu`, `POST /nutzer/<id>/loeschen` (alle Admin-only), `GET/POST /profil` (Passwort ändern, alle Nutzer) |

**Wichtige Helper-Funktionen in `app.py`:**
- `_validate_transaction_form(form, items_total=None)` — zentrale Validierung
  für Neuanlage UND Bearbeitung von Buchungen. `items_total` überschreibt den
  Betrag, wenn Kassenzettel-Posten verwendet werden.
- `_parse_items(form)` — liest die parallelen Formular-Listen
  (`item_description[]`, `item_category_id[]`, `item_amount[]`) zu einer
  Liste von Posten-Dicts, ignoriert leere Zeilen, validiert Beträge.
- `_validate_category_form(conn, user_id, name, kind, parent_id, current_id=None)`
  — zentrale Validierung für Kategorie-Anlage/-Bearbeitung (Ebenen-Limit,
  Art-Konsistenz, Selbstreferenz-Schutz, Eltern-Kategorie auf den Nutzer
  gescoped).
- `_validate_transaction_ownership(conn, uid, data)` /
  `_validate_items_ownership(conn, uid, items)` — IDOR-Schutz für per
  Formular übergebene Fremdschlüssel (siehe "Nutzerverwaltung"), bewusst
  getrennt von `_validate_transaction_form`, damit dessen reine
  Formvalidierung ohne DB-Verbindung unit-testbar bleibt.
- `_build_report(...)` / `GROUP_OPTIONS` in der Berichte-Route — generisches
  Gruppierungssystem (Kategorie, Hauptkategorie, Konto, Monat, Quartal, Jahr,
  Wochentag, Art), teils mit `use_items=True` für Posten-bewusste Auswertung.
- `current_user()` / `admin_required` — liefert den eingeloggten Nutzer
  (`sqlite3.Row` oder `None`) bzw. sperrt eine Route für Nicht-Admins.

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
8. Nutzerverwaltung (Version 2.0.0): Login/Logout, Ersteinrichtung des
   ersten Admins, Mehrbenutzerfähigkeit mit vollständiger Daten-Isolation
   je Nutzer (Konten/Kategorien/Buchungen), Admin-Nutzerverwaltung,
   eigenes Passwort ändern. Siehe Abschnitt "Nutzerverwaltung".

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
- Settings-Tab und Remote DB/Sync (siehe `ROADMAP.md`, Punkte 1 und 3)

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
  UND in `_migrate()` ergänzen, UND die Major-Zahl in `db.APP_VERSION`
  erhöhen — sonst wird die Schema-Änderung nie via `db.migrate()`
  eingespielt und bestehende Installationen bleiben (korrekterweise) an der
  Versionssperre hängen.
- **Neue Buchungsfilter/-felder:** Wenn sie Kontostände oder Kategorie-Summen
  betreffen, daran denken, dass es zwei Auswertungspfade gibt:
  `transactions` direkt (für Kontostände, einfache Listen) und den
  `FLATTENED_CTE`-Pfad in `db.py` (für Kategorie-Auswertungen, die Posten
  berücksichtigen müssen).
- **Validierungslogik** gehört in die zentralen `_validate_*`-Funktionen in
  `app.py`, nicht dupliziert in einzelne Routen — Neuanlage und Bearbeitung
  teilen sich diese Funktionen bewusst.
- **Neue Tabellen/Query-Helfer mit Nutzerbezug:** immer einen `user_id`-
  Parameter vorsehen und konsequent filtern (siehe "Nutzerverwaltung") -
  sonst sehen Nutzer fremde Daten. Neue `<int:id>`-Routen brauchen einen
  Ownership-Check (`WHERE id = ? AND user_id = ?`, fremde ID = "nicht
  gefunden"); neue Formularfelder mit Fremdschlüsseln brauchen eine
  IDOR-Prüfung nach dem Muster von `_validate_transaction_ownership()`.
- **Android-Spiegel:** `android/app/src/main/python/` wird automatisch vom
  Gradle-Task `copyPythonSources` aus `app.py`, `db.py`, `templates/` und
  `static/` im Projekt-Root befüllt (vor jedem Android-Build) - nicht von
  Hand kopieren. Der eingecheckte Stand dort kann zwischen Builds veraltet
  sein; das ist unkritisch, solange kein Android-Build läuft. Dort gibt es
  keine automatisierten Tests, nur manuelle Kontrolle nach dem Bauen.

## Test-Vorgehen (pytest-Suite in `tests/`)

```
venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
```

258 Tests, Laufzeit ~60 s. pytest ist die **einzige** zusätzliche
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
| `conftest.py` | Fixtures (`client` - eingeloggt als Test-Admin, `make`-Factory, `test_user_id`, Query-Helfer), DB-Isolation |
| `test_isolation.py` | Beweist, dass die echte DB unberührt bleibt |
| `test_migration.py` | `init_db` (rührt Alt-DB nicht an) / `migrate` inkl. nachgebauter "alter" DB |
| `test_versioning.py` | `APP_VERSION`/`schema_meta`, `is_compatible`, 409-Sperre + `/migration`-Route |
| `test_balances.py` | Kontostände, Umbuchungen, Salden-vs-Posten-Invariante |
| `test_trash.py` | Soft-Delete, Wiederherstellen, endgültiges Löschen |
| `test_categories.py` | Ebenen-Limit, Art-Konsistenz, Löschsperren, Kaskadierung |
| `test_items.py` | Kassenzettel-Posten (Anlegen, Bearbeiten, Auswertung) |
| `test_statistics.py` | `monthly_summary`, `net_worth_series`, `category_breakdown` |
| `test_reports.py` | `grouped_report` (beide Pfade), `/berichte`, CSV-Export |
| `test_routes_smoke.py` | Alle GET-Routen (leere **und** befüllte DB), Filter, Paginierung, Konten |
| `test_auth.py` | Setup-/Login-Gate, `/login`, `/logout`, `/ersteinrichtung`, `claim_orphan_data`, `/profil` |
| `test_users.py` | Daten-Isolation zwischen Nutzern, IDOR-Schutz, Admin-Nutzerverwaltung (`/nutzer*`) |

**Testdaten** werden über die `make`-Factory per direktem SQL angelegt
(`make.account()`, `make.category()`, `make.tx()`, `make.items()`,
`make.user()` für einen zweiten Nutzer in Isolations-Tests). Alle Methoden
setzen standardmäßig `user_id = make.default_user_id` (der von
`initialized_db` angelegte Test-Admin), außer ein `user=`-Parameter
überschreibt das explizit. Routen-POSTs nur dort, wo die Route selbst unter
Test steht.

**Bei jeder Änderung mitziehen:**
- Neue Schema-Spalte/-Tabelle → `APP_VERSION`-Major erhöhen, Test in
  `test_migration.py`, der `db.migrate()` auf einer DB *ohne* diese Spalte
  prüft (`conftest.old_schema_db()` als Vorlage).
- Neue Route → Eintrag in `GET_ROUTEN` in `test_routes_smoke.py`.
- Neue Auswertung → daran denken, dass es zwei Pfade gibt (`transactions`
  direkt vs. `FLATTENED_CTE`); beide brauchen einen Test.

## Offene Kleinigkeiten / Nice-to-haves aus dem Code selbst

- CSS ist ein einzelnes File ohne Präprozessor — bei wachsendem Umfang ggf.
  in Abschnitte aufteilen (ist aktuell schon mit Kommentar-Überschriften
  strukturiert).
- Chart.js wird per CDN geladen (`templates/statistics.html`) — für
  echten Offline-Betrieb müsste das lokal vendored werden.
- Kein Passwort-Reset-Flow (z. B. per E-Mail) — ein vergessenes Passwort
  kann aktuell nur ein Admin über `/nutzer` beheben (Nutzer löschen + neu
  anlegen; ein direktes "Passwort zurücksetzen" für andere Nutzer gibt es
  noch nicht).
