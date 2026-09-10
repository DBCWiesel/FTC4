# Das Hersteller-Werkzeug portabel machen

Das offizielle **SD_TOOL** (ATW Service Tool, Ver. 8.02) kommt als
Installationsordner. `tools/sdtool_portable/` baut daraus eine Fassung, die
sich entpacken und starten lässt und den Wirtsrechner unverändert zurücklässt.

```bash
tools/sdtool_portable/build.sh SDCardToolv21.zip ~/ausgabe
```

Ergebnis: `SDCardTool-Portable/` und ein ZIP daneben. Die Herstellerdateien
gehören nicht ins Repository und werden deshalb zur Bauzeit aus dem Archiv
geholt — geprüft wird, dass `ATW.exe` im Paket Byte für Byte dem Original
entspricht.

## Was `ATW.exe` tatsächlich braucht

Die folgenden Punkte stammen aus dem IL der Datei, nicht aus Vermutungen.

| Verhalten | Fundstelle | portabel? |
|-----------|-----------|-----------|
| `Teigi\RCSetting_{0}.xls`, `ItemSetting_{0}.xls`, `HeatPump.xls` über `Application.StartupPath` | `FrmMainRC::loadSetting` | ja, ordnerrelativ |
| Keinerlei Registry-Zugriff | keine Referenz auf `Microsoft.Win32.Registry` | ja |
| Alle Bibliotheken liegen neben der EXE | 9 DLLs, kein GAC | ja |
| Einstellungen nach `%APPDATA%\ATW\Config\*.xml` | `Config::InternalLoad`, `GetFolderPath(26)` | **nein** |
| Programmlogs nach `%APPDATA%\ATW\Log\` | `LogFile::GetFileName`, `GetFolderPath(26)` | **nein** |
| Dateidialoge starten in „Eigene Dateien“ | 3× `GetFolderPath(5)` | **nein** |
| Laufzeit `v2.0.50727` | CLR-Header | **nein** |

`GetFolderPath(26)` ist `SpecialFolder.ApplicationData`, `GetFolderPath(5)` ist
`MyDocuments`. Ermittelt mit `dnfile` über die IL-Ströme; die beiden
`ApplicationData`-Stellen wurden zusätzlich als Bytefolge
`ldc.i4.s 26 / call GetFolderPath / ldstr` im Abbild gegengeprüft.

Die drei ordnerrelativen Zeilen sind der Grund, warum überhaupt eine portable
Fassung möglich ist: alles, was das Werkzeug *liest*, liegt schon im eigenen
Ordner. Zu lösen war nur, wohin es *schreibt*.

## Wohin geschrieben wird

`Environment.GetFolderPath(SpecialFolder.ApplicationData)` fragt die Shell
(`SHGetFolderPath`), nicht die Umgebungsvariable `%APPDATA%`. Ein Starter, der
bloß `set APPDATA=…` setzt, bewirkt deshalb **nichts** — der verbreitetste
Kurzschluss bei portablen Fassungen.

`SD-Card-Tool.cmd` hängt den Pfad stattdessen für die Dauer des Laufs um:

1. **Verweis** (`mklink /J`, ohne Administratorrechte): `%APPDATA%\ATW` wird
   zur Verzweigung auf `Data\`. Das Werkzeug schreibt unmittelbar dorthin,
   ein Abbruch kann nichts verlieren. Nach dem Lauf entfernt `rmdir` — ohne
   `/s` — nur die Verzweigung.
2. **Kopie** als Rückfallebene: vor dem Start hin, nach dem Beenden zurück.

Welcher Weg genommen wird, entscheidet nicht der Rückgabewert von `mklink`,
sondern eine Gegenprobe: nur wenn die Kennungsdatei *durch den Verweis
hindurch* sichtbar ist, gilt er als gültig. Diese Gegenprobe stammt aus dem
Test — unter Wine meldet `mklink /J` Erfolg und legt doch nur einen leeren
Ordner an. Auf einem Windows mit Richtlinien gegen Reparse Points wäre der
Effekt derselbe, und die Kopie fängt ihn ab.

Ein vorhandenes `%APPDATA%\ATW` einer echten Installation wandert währenddessen
nach `%APPDATA%\ATW.vor-portable` und kommt danach zurück. Beide Fassungen
stören einander also nicht.

### Abbruch mitten im Lauf

Zwei Kennungsdateien halten den Zustand fest, statt ihn zu raten:

| Datei | Bedeutung |
|-------|-----------|
| `portable-sd-card-tool.txt` | Dieser Ordner gehört uns, nicht dem Benutzer |
| `kopiermodus.txt` | Es ist ein echter Ordner, keine Verzweigung |

Der nächste Start liest sie und holt die Einstellungen eines abgebrochenen
Laufs zurück, bevor er aufräumt. Daraus folgt die Sicherheitsregel des
Starters: **`rd /s` läuft ausschließlich auf einen Ordner mit
`kopiermodus.txt`.** Auf eine Verzweigung wird nie rekursiv gelöscht — unter
älteren Windows-Fassungen wäre das durch den Verweis hindurch gegangen und
hätte `Data\` mitgenommen.

## .NET Framework 2.0

`ATW.exe` ist gegen `v2.0.50727` gebaut; Windows 10 und 11 bringen diesen
Zweig nicht mehr mit und fragen nach „.NET Framework 3.5“. `App\ATW.exe.config`
nennt zwei Laufzeiten in dieser Reihenfolge:

```xml
<supportedRuntime version="v2.0.50727" />
<supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.0" />
```

Ist der 2.0-Zweig da, läuft das Werkzeug wie gehabt darauf; sonst übernimmt die
4.x-Laufzeit, die seit Windows 8 zum System gehört. Das ist unbedenklich, weil
`ATW.exe` und alle neun Bibliotheken reines IL ohne gemischten nativen Code
sind — im CLR-Header steht bei allen `ILONLY`. Deshalb steht auch
`useLegacyV2RuntimeActivationPolicy` **nicht** in der Datei: sie regelt das
Laden gemischter Baugruppen, und solche gibt es hier nicht.

Dazu `loadFromRemoteSources` (Start von einem Netzlaufwerk oder aus einem
heruntergeladenen ZIP) und `generatePublisherEvidence=false` (spart auf einem
Rechner ohne Internet die Wartezeit, die die 2.0-Laufzeit sonst mit dem
Prüfen von Authenticode-Sperrlisten verbringt).

## Was geprüft ist

Getestet unter Wine mit `cmd.exe` und einem Platzhalter anstelle der 32-Bit-EXE:

| Fall | Ergebnis |
|------|----------|
| Erster Lauf, sauberer Rechner | Einstellungen landen in `Data\`, `%APPDATA%` bleibt leer |
| Folgeläufe | Einstellungen wandern mit |
| Daneben eine echte ATW-Installation | beiseite gelegt, unverändert zurück, keine Vermischung |
| Abgebrochener Lauf | ungesicherte Einstellungen gerettet, Rest aufgeräumt |
| Zweiter Start bei laufendem Werkzeug | Abfrage, Abbruch oder bewusstes Weitermachen |
| `ATW.exe` bzw. Kennung fehlt | benannter Fehler, `%APPDATA%` unberührt |

**Nicht geprüft:** der Verweis-Modus. Wine setzt `mklink /J` nicht um, und die
32-Bit-EXE selbst läuft dort nicht. Getestet ist damit genau der Weg, den die
Gegenprobe wählt, wenn die Verzweigung nicht trägt — die Rückfallebene ist
also die belegte, nicht die geratene.

## Was unverändert bleibt

`ATW.exe` und die neun Bibliotheken sind Byte für Byte die Originaldateien; der
Bauvorgang prüft das. Hinzu kommen nur `ATW.exe.config`, der Starter und
`LIESMICH.txt`. Nicht behoben ist die Voreinstellung der Dateidialoge: sie
zeigen beim ersten Mal „Eigene Dateien“ und danach den zuletzt benutzten
Ordner. Dieses Gedächtnis liegt in `Data\Config` und wandert deshalb mit —
ab dem zweiten Start steht dort das SD-Kartenlaufwerk.
