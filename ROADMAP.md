# Roadmap

Hauptbuch startete als bewusst einfache Single-User-App mit flachem
`app.py` + `db.py`, rohem `sqlite3` (kein ORM), ohne Anmeldung und ohne
Konfigurationsschicht. Seit Version 2.0.0 ist die **Nutzerverwaltung**
umgesetzt (siehe Punkt 2 unten) - diese Roadmap beschreibt die verbleibenden
Ausbaustufen. Details zur bestehenden Architektur stehen in `PROJECT.md`,
inklusive des Abschnitts "bewusst nicht umgesetzte Punkte", aus dem einige
der hier genannten Ideen stammen.

Ursprünglich vorgesehene Reihenfolge war Settings-Tab → Nutzerverwaltung →
Remote DB / Sync → Ersteinrichtung. Tatsächlich wurden zuerst **Nutzerver-
waltung** und eine **einfache Ersteinrichtung** umgesetzt (ohne die
Remote-DB/Sync-Wahl aus Punkt 4, da Punkt 3 noch nicht existiert) - der
Settings-Tab bleibt offen und ist weiterhin ein guter naechster Schritt,
bevor Remote DB / Sync sinnvoll angegangen werden kann.

## 1. Settings-Tab

**Ziel:** Eine zentrale Einstellungsseite, die die aktuell fehlende
Persistenz-/Konfigurationsschicht der App schafft.

**Betroffen:**
- Neuer Nav-Eintrag in `templates/base.html`
- Neue Route in `app.py`
- Neues Template `templates/einstellungen.html`
- Neue Key/Value-Tabelle `settings` in `db.py` (`SCHEMA` + `_migrate()`,
  Major-Bump auf 3.0.0), je Nutzer scopen (`user_id`-Spalte, wie die
  Nutzerverwaltung es für `accounts`/`categories`/`transactions` vorgemacht
  hat)
- Getter/Setter-Helfer in `db.py`

**Erste Inhalte:** `PAGE_SIZE` (heute hart in `db.py`), Standard-Zeitraum
für die Statistik, evtl. der `--online`-Warnhinweis (heute noch als reiner
CLI-Hinweis in `app.py`, siehe `--online`-Flag). Später Andockpunkt für
Backup/Export.

**Tests:** neue Route in `GET_ROUTEN` (`tests/test_routes_smoke.py`),
Migrationstest (`tests/test_migration.py`).

## 2. Nutzerverwaltung ✅ umgesetzt (Version 2.0.0)

**Ziel:** Mehrbenutzerfähigkeit + Login. Hebt die frühere "keine
Anmeldung"-Warnung auf und macht den `--online`-Betrieb (LAN-Zugriff)
absicherbar.

**Umgesetzt:**
- `users`-Tabelle (`username`, `password_hash`, `role`), Passwort-Hash via
  Werkzeug (`generate_password_hash`/`check_password_hash` - keine neue
  Abhängigkeit)
- Echter, persistenter `app.secret_key` (`.secret_key`-Datei neben der DB,
  gitignored)
- Session-basierter Login über Flask-Bordmittel (kein flask-login) -
  `_require_setup`/`_require_login` als `before_request`-Gates in `app.py`
- Routen `/login`, `/logout`, `/ersteinrichtung` (siehe Punkt 4),
  `/nutzer`, `/nutzer/neu`, `/nutzer/<id>/loeschen` (Admin-only via
  `admin_required`-Decorator), `/profil` (Passwort ändern)
- Datenmodell: `user_id`-FK an `accounts`, `categories`, `transactions`
  (`transaction_items` erbt implizit über `transaction_id`);
  `categories.name` ist seither nur je Nutzer eindeutig
  (`UNIQUE(user_id, name)` statt `UNIQUE(name)`) - der dafür nötige
  SQLite-Tabellen-Rebuild steckt in `db.py:_migrate()`
- Alle bestehenden Routen und Query-Helfer in `db.py` sind auf `user_id`
  gescoped, inkl. IDOR-Schutz bei `<int:id>`-Routen und Formularfeldern
  (`_validate_transaction_ownership`, `_validate_items_ownership`)
- `APP_VERSION` auf `2.0.0` gehoben (Major-Bump)

**Nicht umgesetzt / bewusst ausgelassen:** kein separater Settings-Tab
(Punkt 1) als Unterbau - die Nutzerverwaltung lebt als eigenständige
Routengruppe; kann bei Bedarf später dorthin verschoben werden.

## 3. Remote DB / Sync

**Ziel:** Daten geräteübergreifend verfügbar machen (Desktop ↔ die
Android-WebView-App in `android/`, deren `app.py`/`db.py` per Gradle-Task
`copyPythonSources` automatisch aus dem Projekt-Root übernommen werden).

Zwei mögliche Richtungen:

- **A – Remote-DB:** Abstraktion über `db.get_connection()` einführen und die
  Verbindungsdaten aus dem Settings-Tab konfigurierbar machen; Wechsel von
  lokalem SQLite auf z. B. Postgres oder gehostetes SQLite. Steht im
  Konflikt mit der bewussten "kein ORM"-Entscheidung — hoher Aufwand.
- **B – Sync:** lokales SQLite bleibt bestehen, zusätzlich Push/Pull bzw.
  Backup-Sync der DB-Datei oder ein Änderungs-Sync. Näher am bestehenden
  Design. Die Nutzerverwaltung (Punkt 2, jetzt vorhanden) liefert dafür
  bereits die Eigentümer-Basis (`user_id`) für Konfliktauflösung.

**Abhängigkeit:** setzt den Settings-Tab (für Konfiguration) voraus —
deshalb weiterhin nach Punkt 1 eingeplant.

## 4. Ersteinrichtung ⚠️ teilweise umgesetzt (Version 2.0.0)

**Ziel:** Ein geführter Onboarding-Flow beim allerersten Start der App, der
die Lücke schließt, dass nach der Installation direkt das leere Dashboard
erscheint.

**Umgesetzt:** Route `/ersteinrichtung` (`app.py`) + Template
`templates/ersteinrichtung.html`. Erkennung "erster Start" über
`db.user_count() == 0` (Gate `_require_setup`), Anlage des ersten
Admin-Benutzers, automatische Übernahme verwaister Alt-Daten aus einer
migrierten Vor-2.0.0-Datenbank (`db.claim_orphan_data()`) sowie Seeding der
Standardkategorien (`db.seed_categories_for_user()`), danach Redirect ins
Dashboard.

**Nicht umgesetzt:** der ursprünglich vorgesehene Schritt "Wahl der
Datenhaltung: lokal oder Remote-DB/Sync verbinden" - setzt Punkt 3 voraus,
das noch nicht existiert. Sobald Remote DB/Sync (Punkt 3) umgesetzt ist,
kann dieser Schritt in `/ersteinrichtung` ergänzt werden.

---

Konventionen für alle Punkte: jede Schema-Änderung muss `_migrate()` in
`db.py` erweitern UND die Major-Zahl in `db.APP_VERSION` erhöhen (siehe
Abschnitt "Versionierung & Migration" in `PROJECT.md`) — Bestandsinstallationen
werden dadurch bis zum expliziten `db.migrate()`-Aufruf gesperrt, statt
unbemerkt am neuen Schema vorbeizulaufen. Jede neue Route bzw. neue Analyse
bekommt zusätzlich einen Test (siehe Testkonventionen in `PROJECT.md`).
