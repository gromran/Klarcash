# Roadmap

Klarcash startete als bewusst einfache Single-User-App mit flachem
`app.py` + `db.py`, rohem `sqlite3` (kein ORM), ohne Anmeldung und ohne
Konfigurationsschicht. Seit Version 2.0.0 ist die **Nutzerverwaltung**
umgesetzt (siehe Punkt 2 unten) - diese Roadmap beschreibt die verbleibenden
Ausbaustufen. Details zur bestehenden Architektur stehen in `PROJECT.md`,
inklusive des Abschnitts "bewusst nicht umgesetzte Punkte", aus dem einige
der hier genannten Ideen stammen.

Ursprünglich vorgesehene Reihenfolge war Settings-Tab → Nutzerverwaltung →
Remote DB / Sync → Ersteinrichtung. Tatsächlich wurden zuerst **Nutzerver-
waltung** und eine **einfache Ersteinrichtung** umgesetzt (ohne die
Remote-DB/Sync-Wahl aus Punkt 4, da Punkt 3 noch nicht existiert), danach
der **Settings-Tab** (Punkt 1, Version 3.0.0) - Remote DB / Sync (Punkt 3)
und **Geteilte Konten** (Punkt 5, neu) sind damit die verbleibenden offenen
Ausbaustufen, unabhängig voneinander umsetzbar.

## 1. Settings-Tab ✅ umgesetzt (Version 3.0.0)

**Ziel:** Eine zentrale Einstellungsseite, die die zuvor fehlende
Persistenz-/Konfigurationsschicht der App schafft.

**Umgesetzt:**
- Route `GET/POST /einstellungen` (Endpoint `settings`) mit zwei Tabs:
  **Account** (Benutzername ändern, Passwort ändern - hier ist `/profil`
  aufgegangen, der alte Endpoint bleibt als reiner Redirect bestehen) und
  **Appearance** (Hintergrund-/Akzentfarbe per freiem Farbwähler,
  Schriftgröße als vierstufige Auswahl)
- Neue Key/Value-Tabelle `settings` in `db.py` (`user_id, key, value`,
  Primary Key `(user_id, key)`), Getter/Setter `db.get_settings()` /
  `db.set_setting()`, Major-Bump auf 3.0.0
- Appearance-Werte werden serverseitig validiert (`^#[0-9A-Fa-f]{6}$` für
  Farben, feste Allowlist für die Schriftgröße) und über einen
  Context-Prozessor (`app.py::_inject_appearance`) als `<style>`-Override
  der CSS-Design-Tokens (`--paper`, `--brand`, `--brand-ink`,
  `--brand-tint`, `--font-scale`) in `templates/base.html` eingebettet -
  siehe dessen Kommentar zur CSS-Injection-Absicherung
- Live-Vorschau der Farb-/Größenwahl per Inline-JS, noch vor dem Speichern
- CSS-only Tab-UI (`static/style.css`, Abschnitt "Tabs (Einstellungen)")
  nach dem Muster der bestehenden `type-toggle`-Idiome

**Nachtraeglich ergaenzt:** Seitenleiste/mobile Menueleiste folgen ebenfalls
der gewaehlten Hintergrundfarbe (zuvor blieben sie auf festem Weiss, siehe
`.sidebar`/`.topbar` in `static/style.css`); automatischer Text-/Button-
Kontrast (`app.py::_auto_text_color`, Tokens `--paper-ink*`/`--accent-text`)
waehlt hell oder dunkel je nach Helligkeit der gewaehlten Hintergrund- bzw.
Akzentfarbe - dafuer musste `--paper` von einem zweiten, informell
mitgenutzten Zweck ("generische helle Flaeche innerhalb weisser Karten":
Formularfelder, Tabellenkopf, Badges, Fortschrittsbalken) entkoppelt werden
(neues, festes Token `--surface-tint`), sonst waeren diese bei dunkler
Hintergrundwahl dunkel-auf-dunkel geworden.

**Bewusst nicht umgesetzt:** `PAGE_SIZE`/Statistik-Standardzeitraum/
`--online`-Warnhinweis als konfigurierbare Settings - die `settings`-Tabelle
ist dafür vorbereitet (freies Key/Value-Schema), aber ungenutzte Keys wurden
nicht vorab angelegt. Kartenflächen (`--surface`: Formulare, Tabellen, KPI-/
Konto-Karten) bleiben bewusst IMMER hell, unabhängig von der gewählten
Hintergrundfarbe - u. a. weil die Einnahme-/Ausgabe-Farbcodierung
(`--positive`/`--negative`) auf hellem Untergrund ausgelegt ist.

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

## 3. Remote DB / Sync ⚠️ teilweise umgesetzt (Version 3.4.0)

**Ziel:** Daten geräteübergreifend verfügbar machen (Desktop ↔ die
Android-WebView-App in `android/`, deren `app.py`/`db.py` per Gradle-Task
`copyPythonSources` automatisch aus dem Projekt-Root übernommen werden).

**Umgesetzt (Phase 1 - lokaler Speicherort + Backup/Restore):**
- Dritter Tab **Datenbank** unter `/einstellungen` (Admin-only, `admin_required`
  - die DB ist ein einziges, app-weit geteiltes SQLite-File, kein
  Pro-Nutzer-Setting wie Account/Appearance)
- Externe Konfigurationsdatei `klarcash_config.json` neben der DB
  (`db.load_config()`/`db.save_config()`/`db.resolve_db_path()`) - bewusst
  **nicht** die `settings`-Tabelle, da die den Speicherort selbst nicht
  kennen kann, solange die DB an diesem Ort noch nicht geöffnet ist
  (Henne-Ei-Problem)
- Speicherort wechselbar über ein Zielordner-Textfeld; Wechsel schreibt
  einen konsistenten Snapshot über die SQLite-Backup-API (`db.backup_to()`)
  an den neuen Ort und aktualisiert `klarcash_config.json` - wirksam erst
  nach einem Neustart, da `db.DB_PATH`/`SECRET_KEY_PATH` beim Programmstart
  gebunden werden (siehe `desktop.py`-Kommentar zur Import-Reihenfolge)
- Backup-Download (`GET /einstellungen/db/backup`, konsistenter Snapshot)
  und Restore-Upload (`POST /einstellungen/db/restore`, validiert über
  `db.is_valid_db()`: Integritätscheck + Major-Versions-Kompatibilität,
  bevor die produktive DB überschrieben wird)
- Bewusst **keine neue Python-Abhängigkeit** (Policy "nur Flask", siehe
  `PROJECT.md`) - wichtig auch für die Android/Chaquopy-arm64-Wheels

**Offen (Phase 2 - echter Remote-Sync):** Google Drive / NextCloud / SFTP
als Ziel für Push/Pull der DB-Datei (ROADMAP-Variante B unten), an das
Backup/Restore aus Phase 1 andockt. Jeder dieser Provider erfordert eine
neue Abhängigkeit (z. B. WebDAV-Client oder `requests` für NextCloud,
`paramiko` für SFTP, Google-API-Client + OAuth für Drive) - vor der Wahl
muss geprüft werden, ob dafür arm64-Wheels existieren, die auch unter
Chaquopy (Android) laufen. Variante A (echtes Remote-DB-Backend statt
Datei-Sync) bleibt unten als Alternative dokumentiert, wurde aber nicht
gewählt.

Zwei mögliche Richtungen (Analyse von vor Phase 1, weiterhin gültig für
Phase 2):

- **A – Remote-DB:** Abstraktion über `db.get_connection()` einführen und die
  Verbindungsdaten aus dem Settings-Tab konfigurierbar machen; Wechsel von
  lokalem SQLite auf z. B. Postgres oder gehostetes SQLite. Steht im
  Konflikt mit der bewussten "kein ORM"-Entscheidung — hoher Aufwand.
- **B – Sync:** lokales SQLite bleibt bestehen, zusätzlich Push/Pull bzw.
  Backup-Sync der DB-Datei oder ein Änderungs-Sync. Näher am bestehenden
  Design (Phase 1 oben nutzt bereits `db.backup_to()` dafür). Die
  Nutzerverwaltung (Punkt 2) liefert dafür bereits die Eigentümer-Basis
  (`user_id`) für Konfliktauflösung.

**Abhängigkeit:** setzte den Settings-Tab (für Konfiguration) voraus — der
existiert seit Version 3.0.0 (Punkt 1). Phase 1 verwendet dafür bewusst
`klarcash_config.json` statt der `settings`-Tabelle (siehe oben); für
Phase-2-Verbindungsdaten (Variante A bzw. Zugangsdaten für Variante B) käme
je nach Sensitivität wieder die `settings`-Tabelle oder eine weitere externe
Config-Datei infrage.

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

## 5. Geteilte Konten (gemeinsames Girokonto)

**Ziel:** Ein einzelnes Konto (`accounts`) für mehrere Nutzer freigeben,
z. B. ein gemeinsames Girokonto von Partnern — beide sehen denselben
Kontostand, dieselben Buchungen und können (je nach Rolle) buchen. Hebt die
bisher strikte Pro-Nutzer-Isolation für *ausgewählte* Konten gezielt auf,
ohne die Isolation der übrigen (privaten) Konten anzutasten.

**Datenmodell (geplant):**
- Neue Verknüpfungstabelle `account_members` (`account_id → accounts.id`,
  `user_id → users.id`, `role TEXT CHECK(role IN ('read','write'))`,
  `PRIMARY KEY (account_id, user_id)`). `accounts.user_id` bleibt als
  **Eigentümer** (Ersteller, implizit `write` + darf teilen/entziehen).
- Zugriff auf ein Konto = Eigentümer **oder** Eintrag in `account_members`.
  Der bisherige Filter `WHERE accounts.user_id = ?` wird zu einer
  Sichtbarkeit "eigene **plus** geteilte Konten" (LEFT JOIN/`UNION` gegen
  `account_members`) — betrifft `db.accounts_with_balances`,
  `db.account_balance` und alle Konto-Auswahllisten.
- Buchungen: `transactions.user_id` bleibt der **buchende** Nutzer (Autor),
  aber Sichtbarkeit/IDOR wird auf **Konto-Mitgliedschaft** umgestellt statt
  auf `transactions.user_id`. Die Salden-Query darf dann **nicht** mehr
  Buchungs-Summen an `user_id = a.user_id` koppeln (heute in
  `db.account_balance`) — sonst zählen Buchungen des Partners nicht mit.

**Berechtigung (Besitzer + Rollen):** Der Eigentümer teilt das Konto mit
einem anderen Nutzer (per Nutzername) und vergibt die Rolle `read`
(nur sehen) oder `write` (sehen + buchen/bearbeiten/löschen).
`write`-Mitglieder dürfen Buchungen anlegen/ändern, aber **nicht** das
Konto archivieren, umbenennen oder Freigaben verwalten — das bleibt beim
Eigentümer. Alle bestehenden `<int:id>`-Ownership-Prüfungen
(`WHERE id = ? AND user_id = ?`) und die Formular-IDOR-Prüfungen
(`_validate_transaction_ownership`, `_validate_items_ownership` in
`app.py`) müssen von "ist Eigentümer" auf "ist Mitglied mit passender
Rolle" umgestellt werden.

**Kategorien (geteilter Kategoriensatz):** Buchungen auf einem geteilten
Konto brauchen Kategorien, die **beide** Partner nutzen können — heute sind
Kategorien pro Nutzer (`categories.user_id`, `UNIQUE(user_id, name)`).
Geplant: geteilte Konten bekommen einen **gemeinsamen Kategoriensatz**,
z. B. über ein optionales `categories.account_id` (Kategorie gehört dann
dem Konto statt einem Nutzer) und eine erweiterte Eindeutigkeit
(`UNIQUE(user_id, name)` **und** `UNIQUE(account_id, name)`). Buchungen auf
dem geteilten Konto (und ihre `transaction_items`) referenzieren die
Konto-Kategorien; private Konten bleiben bei den Nutzer-Kategorien. Der
`FLATTENED_CTE`-Auswertungspfad (Berichte/Statistik mit Posten) muss die
Konto-Kategorien einbeziehen.

**UI/Routen (geplant):** Neuer Bereich in der Kontenverwaltung
(`templates/accounts.html`/`account_form.html`), z. B. `GET/POST
/konten/<id>/teilen` (Mitglied per Nutzername + Rolle hinzufügen),
`POST /konten/<id>/teilen/<user_id>/entfernen`, Rollen-Wechsel. Nur der
Eigentümer sieht/nutzt diese Aktionen. Geteilte Konten im Dashboard/den
Listen kennzeichnen (Badge "geteilt", ggf. mit Eigentümer/Mitglieder).

**Auswirkung auf die Kern-Invariante:** Dies ist der erste bewusste Bruch
der Regel "**Alle** Queries auf `accounts`/`categories`/`transactions`
filtern auf `user_id`" (PROJECT.md). Sauber umsetzen heißt: einen zentralen
Helfer einführen (z. B. `db.visible_account_ids(user_id)` bzw.
`db.user_can_access_account(user_id, account_id, need_write=…)`) und
konsequent überall statt des rohen `user_id`-Filters verwenden — sonst
entstehen Sicht-Lücken oder IDOR-Regressionen. Die Isolation privater
Konten muss dabei unverändert bleiben.

**Abhängigkeit / Aufwand:** Unabhängig von Punkt 3 (Remote DB / Sync).
Schema-Änderung → **Major-Bump auf `4.0.0`** (aktuell `3.2.0`), inkl.
`_migrate()`-Erweiterung (neue `account_members`-Tabelle, `categories`-
Rebuild für das zusätzliche `UNIQUE`/`account_id` — analog dem 2.0.0-
Rebuild, mit `PRAGMA foreign_keys=OFF` + `legacy_alter_table=ON`). Höherer
Aufwand als die bisherigen Punkte, weil die zentrale Isolations-Invariante
angefasst wird; entsprechend viele Tests (Isolation privat vs. geteilt,
Rollen `read`/`write`, IDOR gegen Nicht-Mitglieder, Salden mit Buchungen
beider Partner). Bump vorab explizit ankündigen (Major).

---

Konventionen für alle Punkte: jede Schema-Änderung muss `_migrate()` in
`db.py` erweitern UND die Major-Zahl in `db.APP_VERSION` erhöhen (siehe
Abschnitt "Versionierung & Migration" in `PROJECT.md`) — Bestandsinstallationen
werden dadurch bis zum expliziten `db.migrate()`-Aufruf gesperrt, statt
unbemerkt am neuen Schema vorbeizulaufen. Jede neue Route bzw. neue Analyse
bekommt zusätzlich einen Test (siehe Testkonventionen in `PROJECT.md`).
