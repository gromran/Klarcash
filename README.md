# Hauptbuch – Ausgabenverwaltung

Kleine Webanwendung zur Verwaltung von Einnahmen, Ausgaben und Kontobeständen
(Konto / Bar / Anlage). Backend: Python (Flask), Datenhaltung: SQLite.
Keine externen Dienste, keine Cloud-Abhängigkeit – läuft komplett lokal.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Start

```bash
python app.py
```

Danach im Browser öffnen: http://127.0.0.1:5000

Beim ersten Start wird automatisch `ausgaben.db` (SQLite-Datei) im
Projektverzeichnis angelegt, inklusive einiger Standardkategorien.
Konten legst du bewusst selbst an (**Konten → Neues Konto**) – es wird
kein Platzhalterkonto vorausgesetzt, damit Anfangsbestände nicht geraten
werden müssen.

## Funktionsumfang

- **Konten**: beliebig viele Konten vom Typ Konto, Bar oder Anlage, jeweils
  mit eigenem Anfangsbestand. Der aktuelle Bestand wird aus dem
  Anfangsbestand plus allen Buchungen berechnet, nicht separat gespeichert.
- **Buchungen**: Einnahme, Ausgabe oder Umbuchung zwischen zwei Konten
  (z. B. Bargeld → Girokonto), jeweils mit Datum, Betrag, Kategorie und
  Beschreibung.
- **Kategorien**: frei definierbar, getrennt nach Einnahme/Ausgabe, optional
  mit einer Unterkategorie-Ebene (z. B. Lebensmittel → Brot, Getränke →
  Bier). Unterkategorien sind rein optional – jede Kategorie funktioniert
  auch ohne. Es sind maximal zwei Ebenen vorgesehen. Kategorien lassen
  sich nachträglich umbenennen, in ihrer Art ändern oder einer anderen
  Hauptkategorie zuordnen (bzw. zur Hauptkategorie befördern). Ändert sich
  die Art einer Hauptkategorie, ziehen ihre Unterkategorien automatisch
  mit. Kategorien, die bereits in Buchungen verwendet werden oder noch
  Unterkategorien besitzen, lassen sich nicht löschen (Datenintegrität).
- **Übersicht**: Gesamtbestand über alle Konten, Einnahmen/Ausgaben/Saldo
  des laufenden Monats, letzte Buchungen.
- **Buchungsliste**: filterbar nach Konto, Art, Zeitraum und Volltextsuche
  (Beschreibung), inkl. Summenzeile für die aktuelle Filterauswahl und
  Paginierung (25 Buchungen je Seite).
- **Buchungen bearbeiten**: nachträgliches Anpassen von Datum, Betrag,
  Konto, Kategorie und Beschreibung.
- **Kassenzettel-Posten**: eine Einnahme oder Ausgabe lässt sich optional
  in einzelne Posten aufteilen (z. B. die Artikel eines Kassenbons),
  jeweils mit eigener Beschreibung, Betrag und Kategorie. Posten dürfen
  negativ sein – etwa für eine Pfandrückgabe auf demselben Bon. Der
  Buchungsbetrag ergibt sich automatisch als Summe aller Posten; die
  einzelne Kategorie-Auswahl der Buchung entfällt in diesem Fall zugunsten
  der Kategorien je Posten. Statistiken und Berichte werten die
  Posten-Kategorien mit aus, sodass z. B. "Brot" und "Pfand" vom selben
  Einkauf getrennt in der Kategorie-Auswertung erscheinen. Buchungen mit
  Posten sind an einem 🧾-Symbol erkennbar.
- **Papierkorb**: Löschen verschiebt eine Buchung zunächst in den
  Papierkorb (Soft Delete) statt sie sofort zu entfernen. Von dort lässt
  sie sich wiederherstellen oder endgültig löschen. Gelöschte Buchungen
  fließen nicht mehr in Kontostände oder Statistiken ein.
- **Statistiken**: Einnahmen/Ausgaben je Monat (Balkendiagramm),
  Vermögensentwicklung über die Zeit (Liniendiagramm), sowie Ausgaben und
  Einnahmen je Kategorie (Donut-Diagramm + Rangliste mit Anteilen).
  Zeitraum wählbar (6/12/24 Monate oder gesamter Verlauf). Die Diagramme
  werden mit Chart.js gerendert, das per CDN nachgeladen wird – dafür ist
  beim Aufruf im Browser eine Internetverbindung nötig.
- **Berichte**: frei gruppierbare Auswertung – nach Kategorie,
  Hauptkategorie (Unterkategorien werden dabei zusammengefasst), Konto,
  Monat, Quartal, Jahr, Wochentag oder Buchungsart. Dazu freier Zeitraum,
  Konto-Filter und Auswahl der Buchungsarten (z. B. nur Ausgaben, oder
  inklusive Umbuchungen). Ergebnis lässt sich direkt als CSV
  exportieren (Semikolon-getrennt, für Excel/Tabellenkalkulation).

## Projektstruktur

```
app.py              Flask-Routen
db.py                SQLite-Zugriff, Schema, Saldenberechnung
templates/           Jinja2-Templates
static/style.css     Styling
tests/               pytest-Suite
ausgaben.db          SQLite-Datenbank (wird beim ersten Start erzeugt)
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Die Tests laufen ausschließlich gegen temporäre Wegwerf-Datenbanken – deine
`ausgaben.db` wird dabei niemals gelesen oder verändert.

## Hinweise

- Aktualisierst du von einer älteren Version dieser App, wird deine
  bestehende `ausgaben.db` beim nächsten Start automatisch migriert
  (fehlende Spalten für Papierkorb und Unterkategorien sowie die Tabelle
  für Kassenzettel-Posten werden ergänzt) – vorhandene Buchungen und
  Kategorien bleiben erhalten.
- Die App ist für den lokalen Einzelnutzer-Betrieb gedacht (kein
  Login/Mehrbenutzerbetrieb). `app.secret_key` in `app.py` ist daher bewusst
  einfach gehalten – für einen Betrieb außerhalb von localhost sollte er
  durch einen zufälligen, geheimen Wert ersetzt werden.
- Für einen produktiveren Einsatz (z. B. Dauerbetrieb im Netzwerk) empfiehlt
  sich ein WSGI-Server wie `waitress` oder `gunicorn` statt des eingebauten
  Entwicklungsservers.
