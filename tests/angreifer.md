# Prüfauftrag für ein Release

Lies den Diff als Code-Review und als Angriff. Du prüfst nur diesen Diff, nicht den ganzen Kern.

## Angreifer

- Ein fremdes, geklontes Wiki: `wiki.toml`, Karten, SVG, `design.css`, Symlinks, Lauf-Ordner.
- Eine fremde Webseite im Browser, während `okfw serve` läuft.
- Eine Webseite, die den Agenten im Container steuert.

Keine Grenze: Eingaben der Person selbst, Wettläufe desselben Nutzers und alles, was eine Sitzung mit Bash ohnehin kann.

## Befund

Je Befund: Datei und Zeile, Versuch (Befehl oder Eingabe), beobachtetes Ergebnis, überschrittene Grenze.
Ein Befund ohne Versuch heißt `Vermutung`. Fehlerhaftes Verhalten ohne Angriff heißt `Fehler`, mit dem Weg dorthin.
Gibt es nichts, schreibe `keine Befunde`.
