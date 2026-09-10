@echo off
rem ===================================================================
rem  Ecodan SD Card Tool (ATW Service Tool)  -  portabler Start
rem
rem  Startet das Werkzeug ohne Installation und laesst den Wirtsrechner
rem  sauber zurueck: Einstellungen und Logs bleiben im Ordner "Data"
rem  neben dieser Datei statt in %APPDATA%\ATW.
rem
rem  Ordner:  App\    Programmdateien des Herstellers
rem           Data\   Config\ und Log\ - wandern mit dem Stick
rem
rem  Ohne Umlaute, damit die Ausgabe in jeder Konsolen-Codepage stimmt.
rem ===================================================================

setlocal EnableExtensions
title SD Card Tool (portabel)

if not defined APPDATA (
    echo FEHLER: %%APPDATA%% ist nicht gesetzt - kein Windows-Benutzerprofil?
    goto :Abbruch
)

rem pushd faengt auch UNC-Pfade ab - es blendet dafuer einen Laufwerks-
rem buchstaben ein, den cmd sonst nicht betreten koennte.
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo FEHLER: Der Ordner "%~dp0" ist nicht erreichbar.
    goto :Abbruch
)

set "BASE=%CD%"
set "APPDIR=%BASE%\App"
set "DATADIR=%BASE%\Data"
set "EXE=%APPDIR%\ATW.exe"
set "TARGET=%APPDATA%\ATW"
set "BACKUP=%APPDATA%\ATW.vor-portable"
set "KENNUNG=portable-sd-card-tool.txt"
set "KOPIEFLAG=kopiermodus.txt"
set "SPERRE=%DATADIR%\.laeuft"

if not exist "%EXE%" (
    echo FEHLER: "%EXE%" wurde nicht gefunden.
    echo Diese Datei gehoert in denselben Ordner wie App\ und Data\.
    popd
    goto :Abbruch
)
if not exist "%DATADIR%\%KENNUNG%" (
    echo FEHLER: "%DATADIR%\%KENNUNG%" fehlt.
    echo Ohne diese Kennung laesst sich der eigene Datenordner nicht
    echo von einer vorhandenen ATW-Installation unterscheiden.
    popd
    goto :Abbruch
)

if not exist "%DATADIR%\Config" mkdir "%DATADIR%\Config" 2>nul
if not exist "%DATADIR%\Log"    mkdir "%DATADIR%\Log"    2>nul

rem --- nur eine Instanz gleichzeitig --------------------------------
rem  mkdir ist unteilbar: es scheitert genau dann, wenn es den Ordner
rem  schon gibt. Damit laesst sich ein zweiter Start erkennen, ohne
rem  auf Prozesslisten oder Zeitstempel angewiesen zu sein.
mkdir "%SPERRE%" 2>nul
if not errorlevel 1 goto :Frei
call :SperreAbfragen
if errorlevel 1 (
    popd
    goto :Abbruch
)

:Frei
call :Start
rmdir "%SPERRE%" 2>nul
popd
endlocal
exit /b 0


rem ===================================================================
:Start
rem  Kehrt immer mit 0 zurueck - der Rueckgabewert steuert oben nur die
rem  Erkennung der belegten Sperrdatei.
rem ===================================================================

rem --- Reste eines abgebrochenen Laufs beseitigen --------------------
if exist "%TARGET%\%KENNUNG%" (
    echo [i] Rest eines abgebrochenen Laufs gefunden.
    if exist "%TARGET%\%KOPIEFLAG%" (
        echo     Einstellungen werden nach "%DATADIR%" zurueckgeholt.
        call :NachDataKopieren
        rd /s /q "%TARGET%" 2>nul
    ) else (
        rmdir "%TARGET%" 2>nul
    )
)

rem --- eigene ATW-Daten des Benutzers beiseite legen -----------------
set "VERSCHOBEN="
if exist "%TARGET%" (
    if exist "%BACKUP%" (
        echo FEHLER: "%BACKUP%" gibt es bereits.
        echo Dieser Ordner stammt aus einem frueheren Lauf. Bitte von
        echo Hand pruefen und nach "%TARGET%" zurueckbenennen.
        pause
        exit /b 0
    )
    move "%TARGET%" "%BACKUP%" >nul 2>&1
    if errorlevel 1 (
        echo FEHLER: "%TARGET%" laesst sich nicht beiseite legen.
        echo Laeuft das ATW-Werkzeug vielleicht noch?
        pause
        exit /b 0
    )
    set "VERSCHOBEN=ja"
    echo [i] Vorhandene ATW-Daten liegen waehrend des Laufs unter
    echo     "%BACKUP%" und kommen danach zurueck.
)

rem --- Data\ nach %APPDATA%\ATW spiegeln -----------------------------
rem  Erste Wahl ist eine Verzweigung (Junction): das Programm schreibt
rem  dann unmittelbar auf den Stick, nichts kann verloren gehen.
rem  mklink /J braucht keine Administratorrechte. Scheitert es doch
rem  (Richtlinie, exotisches Dateisystem), wird kopiert.
set "MODUS=KOPIE"
mklink /J "%TARGET%" "%DATADIR%" >nul 2>&1
rem  Gegenprobe statt blindem Vertrauen auf den Rueckgabewert: erst wenn
rem  die Kennungsdatei durch den Verweis hindurch sichtbar ist, zeigt er
rem  wirklich auf Data\. Es gibt Umgebungen, die Erfolg melden und doch
rem  nur einen leeren Ordner anlegen - dann wird kopiert, und der leere
rem  Ordner ist genau der, den der Kopiermodus ohnehin braucht.
if not errorlevel 1 if exist "%TARGET%\%KENNUNG%" set "MODUS=VERWEIS"

if "%MODUS%"=="KOPIE" (
    mkdir "%TARGET%" 2>nul
    copy /y "%DATADIR%\%KENNUNG%" "%TARGET%\%KENNUNG%" >nul 2>&1
    >"%TARGET%\%KOPIEFLAG%" echo Kopiermodus - dieser Ordner wird beim Beenden geloescht.
    if exist "%DATADIR%\Config" xcopy "%DATADIR%\Config" "%TARGET%\Config\" /e /i /q /y >nul 2>&1
    if exist "%DATADIR%\Log"    xcopy "%DATADIR%\Log"    "%TARGET%\Log\"    /e /i /q /y >nul 2>&1
)

echo.
echo   Einstellungen und Logs: "%DATADIR%"
if "%MODUS%"=="VERWEIS" echo   Modus: Verweis - das Programm schreibt direkt dorthin.
if "%MODUS%"=="KOPIE"   echo   Modus: Kopie - wird beim Beenden zurueckgeschrieben.
echo.
echo   Das SD Card Tool wird gestartet.
echo   Dieses Fenster bitte offen lassen, es raeumt am Ende auf.
echo.

pushd "%APPDIR%"
"%EXE%"
popd

rem --- aufraeumen ----------------------------------------------------
if "%MODUS%"=="VERWEIS" (
    rmdir "%TARGET%" 2>nul
    if exist "%TARGET%" (
        echo WARNUNG: Der Verweis "%TARGET%" liess sich nicht entfernen.
        echo Er zeigt auf "%DATADIR%" und darf von Hand geloescht werden
        echo - mit "rmdir", nicht mit "rd /s".
    )
) else (
    call :NachDataKopieren
    rem  rd /s laeuft nur auf einen Ordner, den dieses Skript selbst
    rem  angelegt hat - erkennbar an der Kopiermodus-Kennung.
    if exist "%TARGET%\%KOPIEFLAG%" rd /s /q "%TARGET%" 2>nul
)

if defined VERSCHOBEN (
    if exist "%BACKUP%" move "%BACKUP%" "%TARGET%" >nul 2>&1
)

echo   Fertig - auf diesem Rechner ist nichts zurueckgeblieben.
exit /b 0


rem ===================================================================
:NachDataKopieren
rem  Config\ und Log\ aus %APPDATA%\ATW zurueck nach Data\ holen.
rem  Nur diese beiden Unterordner, damit die Kennungsdateien oben
rem  nicht in Data\ landen.
rem ===================================================================
if exist "%TARGET%\Config" xcopy "%TARGET%\Config" "%DATADIR%\Config\" /e /i /q /y >nul 2>&1
if exist "%TARGET%\Log"    xcopy "%TARGET%\Log"    "%DATADIR%\Log\"    /e /i /q /y >nul 2>&1
exit /b 0


rem ===================================================================
:SperreAbfragen
rem  ERRORLEVEL 0 = weitermachen, 1 = abbrechen.
rem  Die Abfrage steht bewusst in einem Unterprogramm und nicht in einem
rem  Klammerblock: dort waere %WEITER% schon beim Einlesen des Blocks
rem  ersetzt worden, also bevor set /p ueberhaupt gefragt hat.
rem ===================================================================
echo.
echo   Es gibt bereits eine Sperre:
echo     "%SPERRE%"
echo.
echo   Entweder laeuft das SD Card Tool schon aus diesem Ordner - dann
echo   bitte hier abbrechen und das andere Fenster benutzen.
echo   Oder ein frueherer Lauf wurde hart abgebrochen - dann ist die
echo   Sperre nur liegengeblieben und der Start ist gefahrlos.
echo.
set "WEITER="
set /p "WEITER=Trotzdem starten? (j/N): "
if /i "%WEITER%"=="j" exit /b 0
echo Abgebrochen.
exit /b 1


rem ===================================================================
:Abbruch
echo.
pause
endlocal
exit /b 1
