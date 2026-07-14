# Roadmap

Hauptbuch ist heute bewusst einfach gehalten: eine Single-User-App mit
flachem `app.py` + `db.py`, rohem `sqlite3` (kein ORM), ohne Anmeldung und
ohne Konfigurationsschicht. Diese Roadmap beschreibt die vier nächsten
Ausbaustufen. Details zur bestehenden Architektur stehen in `PROJECT.md`,
inklusive des Abschnitts "bewusst nicht umgesetzte Punkte", aus dem einige
der hier genannten Ideen stammen.

Empfohlene Reihenfolge: **Settings-Tab → Nutzerverwaltung → Remote DB / Sync →
Ersteinrichtung**, weil der Settings-Tab die aktuell fehlende Konfigurations-/
Persistenzschicht schafft, auf der die anderen Features aufbauen, Remote
DB/Sync von einer funktionierenden Nutzerverwaltung profitiert
(Eigentümer-/Konfliktbasis), und die Ersteinrichtung als abschließender
Onboarding-Flow beide vorherigen Features (Nutzerverwaltung, Remote DB/Sync)
voraussetzt.

## 1. Settings-Tab

**Ziel:** Eine zentrale Einstellungsseite, die die aktuell fehlende
Persistenz-/Konfigurationsschicht der App schafft.

**Betroffen:**
- Neuer Nav-Eintrag in `templates/base.html` (Nav-Liste, ca. Zeile 30-38)
- Neue Route in `app.py`
- Neues Template `templates/einstellungen.html`
- Neue Key/Value-Tabelle `settings` in `db.py` (`SCHEMA` + `_migrate()`)
- Getter/Setter-Helfer in `db.py`

**Erste Inhalte:** `PAGE_SIZE` (heute hart in `db.py:73`), Standard-Zeitraum
für die Statistik, evtl. der `--online`-Warnhinweis. Später Andockpunkt für
Backup/Export.

**Tests:** neue Route in `GET_ROUTEN` (`tests/test_routes_smoke.py`),
Migrationstest (`tests/test_migration.py`).

## 2. Nutzerverwaltung

**Ziel:** Mehrbenutzerfähigkeit + Login. Hebt die "keine Anmeldung"-Warnung
auf und macht den `--online`-Betrieb (LAN-Zugriff) absicherbar.

**Betroffen (greenfield, da bisher nichts existiert):**
- Neue `users`-Tabelle, Passwort-Hash via bereits vorhandenem Werkzeug
  (`generate_password_hash`)
- Echter `app.secret_key` (heute `"dev-only-change-me"`, `app.py:22`)
- Session/Login-Mechanismus — Entscheidung zwischen Flask-Bordmitteln und
  einer neuen Abhängigkeit wie flask-login (kollidiert mit der bisherigen
  "Flask-only, keine Extra-Deps"-Philosophie)
- `@login_required` auf allen bestehenden Routen
- Login-/Logout-/Registrierungs-Templates
- Nutzerverwaltung als Unterseite im Settings-Tab

**Datenmodell:** `user_id`-FK an `accounts`, `categories`, `transactions`
(`transaction_items` erbt darüber implizit). Anpassung aller Queries,
inklusive `FLATTENED_CTE` (`db.py:81`), sowie Erweiterung von `_migrate()`.

**Hinweis:** größter Einzeleingriff im Projekt, da die Annahme "alle Daten
sind global" aktuell überall verdrahtet ist (Salden, Kategorien, Statistiken,
Export).

## 3. Remote DB / Sync

**Ziel:** Daten geräteübergreifend verfügbar machen (Desktop ↔ die
Android-WebView-App in `android/`).

Zwei mögliche Richtungen:

- **A – Remote-DB:** Abstraktion über `db.get_connection()` einführen und die
  Verbindungsdaten aus dem Settings-Tab konfigurierbar machen; Wechsel von
  lokalem SQLite auf z. B. Postgres oder gehostetes SQLite. Steht im
  Konflikt mit der bewussten "kein ORM"-Entscheidung — hoher Aufwand.
- **B – Sync:** lokales SQLite bleibt bestehen, zusätzlich Push/Pull bzw.
  Backup-Sync der DB-Datei oder ein Änderungs-Sync. Näher am bestehenden
  Design, braucht aber Nutzerverwaltung (Punkt 2) als Basis für Eigentümer-
  und Konfliktauflösung.

**Abhängigkeit:** setzt den Settings-Tab (für Konfiguration) und sinnvollerweise
die Nutzerverwaltung voraus — deshalb als letzter Schritt eingeplant.

## 4. Ersteinrichtung

**Ziel:** Ein geführter Onboarding-Flow beim allerersten Start der App, der
die Lücke schließt, dass heute nach der Installation direkt das leere
Dashboard erscheint. Setzt Punkt 2 (Nutzerverwaltung) und Punkt 3
(Remote DB / Sync) voraus, da hier deren initiale Konfiguration erst möglich
wird.

**Ablauf (Vorschlag):**
1. Erkennung "erster Start" (z. B. keine Zeile in `users`) → Redirect auf
   `/ersteinrichtung`, solange dieser Zustand besteht.
2. Anlage des ersten Admin-Benutzers (nutzt die `users`-Tabelle und den
   Passwort-Hash-Mechanismus aus Punkt 2).
3. Wahl der Datenhaltung: lokal (Status quo) oder Remote-DB/Sync verbinden
   (nutzt die in Punkt 3 geschaffene Abstraktion + Settings-Persistenz aus
   Punkt 1).
4. Optional: erstes Konto (`accounts`) und Startkategorien anlegen, danach
   Redirect ins reguläre Dashboard.

**Betroffen:**
- Neue Route(n) in `app.py` (z. B. `/ersteinrichtung`, evtl. mehrstufig)
- Neues Template `templates/ersteinrichtung.html`
- Before-Request-Hook oder Middleware, die bei fehlendem Erststart-Zustand
  auf den Setup-Flow umleitet
- Wiederverwendung der Bausteine aus Punkt 2 (User-Anlage) und Punkt 3
  (Verbindungs-/Sync-Konfiguration) statt Doppelimplementierung

**Tests:** Route(n) in `GET_ROUTEN` bzw. eigener Smoke-Test für den
Redirect-Zwang im Erststart-Zustand; Test, dass die App nach abgeschlossener
Ersteinrichtung nicht mehr dorthin umleitet.

---

Konventionen für alle vier Punkte: jede Schema-Änderung muss `_migrate()` in
`db.py` erweitern, und jede neue Route bzw. neue Analyse bekommt einen Test
(siehe Testkonventionen in `PROJECT.md`).
