# Android-Wrapper (Chaquopy)

Bettet den bestehenden Flask-Server aus `../app.py` per Chaquopy in eine
installierbare APK ein; eine WebView zeigt `http://127.0.0.1:5000/`. Kein
Remote-Server beteiligt.

## Vor dem ersten Build

Der Gradle-Wrapper (`gradlew`, `gradlew.bat`, `gradle-wrapper.properties`) ist
im Repo committed – kein Android Studio zum Erzeugen nötig, siehe
`README.md` im Projekt-Root für den Bau-Befehl. Trotzdem fehlt noch:

- Android Studio + JDK 17 + Android SDK (Platform + Build-Tools) installieren.
- App-Icons (`res/mipmap-*/ic_launcher*`) fehlen bewusst – über Android
  Studios "Image Asset"-Tool generieren, sonst schlägt der Build wegen
  fehlender Standard-Icons ggf. fehl, falls `android:icon` im Manifest
  gesetzt wird (aktuell absichtlich weggelassen).
- `chaquopy`-Plugin-Version und Python-Version (`android/app/build.gradle.kts`)
  gegen die aktuelle Kompatibilitätsmatrix auf chaquo.com prüfen, passend zur
  installierten AGP/Gradle-Version.

## Struktur

- `app/src/main/python/` wird **nicht** manuell gepflegt – die Gradle-Task
  `copyPythonSources` kopiert `app.py`, `db.py`, `templates/`, `static/` aus
  dem Projekt-Root (`../`) hierher, vor jedem Build. Einzige Quelle der
  Wahrheit bleibt der Projekt-Root.
- `MainActivity.kt` startet Flask in einem Daemon-Thread
  (`app.start(host, port, db_path)`, additive Funktion in `app.py`), pollt
  den Port bis der Server bereit ist, und fängt den CSV-Export-Link ab, um
  ihn über die `MediaStore.Downloads`-API ins echte Downloads-Verzeichnis zu
  schreiben.

## Bekannte offene Punkte

- Chaquopy-Lizenz: kostenlos für FOSS-Nutzung, kommerziell lizenzpflichtig –
  vor Veröffentlichung auf chaquo.com prüfen.
- Google Fonts in `templates/base.html` sind (bewusst, siehe Hauptplan) noch
  nicht vendored – Statistikseite ist offline-fähig (Chart.js lokal), die
  Schriftart fällt offline auf eine Systemschrift zurück.
