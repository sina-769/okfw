# Selbstreview

Ein Wegwerf-Wiki über okfw selbst, mit okfw gebaut, macht sichtbar, wo Doku, Code und Tests auseinanderlaufen.
Es bleibt unter `.nachbau/` (git-ignoriert); heraus kommt nur der Bericht
als Anhang am Release. Vorbild ist ein okfw-Wiki über ein fremdes Projekt, dessen
Konzeptkarten Widersprüche als `> ⚠️ **Widerspruch:**` mit beiden Fundstellen führen und eine Synthese
„Wo Doku und Code auseinanderlaufen“ bündelt.

Den mechanischen Teil (Kartenliste und Lücken) prüft `tests/test_abdeckung.py` mit derselben Funktion `inventory()`
bei jedem Testlauf, auch auf GitHub. Karten lesen, Widersprüche vermerken und gegenprüfen gehören zur vollen Prüfung,
die nur bei eigenem Anlass läuft (siehe docs/entwicklung.md, „Release-Schritte“).

## 1. Wiki und erste Befundliste (mechanisch)

```bash
uv run --script tests/selbstreview.py [--rev REV] [--neu]      # im Plugin-Ordner, Standard HEAD
```

Das Skript nimmt den Stand eines Commits, nie den Arbeitsbaum, und schreibt nach
`.nachbau/selbstreview-<version>/`:

- `_stand/`: die Dateien des Commits; geprüft wird der Kern dieses Stands.
- ein Wiki aus `init --profile dokumentation`, je Datei ein Rohquellen-Paket (`capture --commit`) und eine
  Quellenkarte: `SKILL.md`, `README.md`, `docs/rundgang.md`, `docs/entwicklung.md`, `CHANGELOG.md`, `docs/konfiguration.md`, `okfw.py`, die Module unter `okfw_kern/`, `okf_hook.py`,
  `hooks/hooks.json`, `settings.example.json`, `profiles/*.toml`, `tests/*.py`.
- je Unterbefehl, Lint-Regelcode, Konfigurationsschlüssel und Hook eine Konzeptkarte mit den Fundstellen
  in Code, Doku und Tests als Fußnoten mit Zeile, dazu vier Übersichtskarten. Die Liste kommt aus dem Code
  (argparse, `SEVERITY` und `add(…)`, `DEFAULT_CONFIG`, `TYPE_DEFAULTS` und jeder Lesezugriff auf `cfg`,
  Handler in `okf_hook.py`), nicht aus der Doku.
- `befunde.md`: im Code, aber in keiner Doku; in der Doku, aber nicht im Code; ohne Test; Schwere und
  Hook-Einträge, die abweichen; Aliase (zwei Namen für eine Sache). `inventar.json`: dieselben Daten roh.

Die Treffer sind Textsuche: Sie sagen, wo zu lesen ist, nicht, dass etwas stimmt.

## 2. Karten lesen (Entwurf durch Helfer, Prüfung durch den Hauptagenten)

Je Gruppe (Befehle, Lint-Regeln, Schlüssel, Hooks mit Ereignissen und Freigabe) liest ein Helfer mit nur
lesenden Werkzeugen die Doku-Aussagen zu jedem Konzept und den Code, der sie umsetzt, alles unter `_stand/`.
Auftrag an jeden Helfer:

- Für jede Aussage der Doku über Verhalten, Standardwert, Schwere, Option oder Ausgabe: Was tut der Code?
- Ergebnis als Liste, je Befund: Karte (Slug), Aussage A mit Datei, Zeile und wörtlichem Ausschnitt,
  Aussage B ebenso, Art (Widerspruch, Lücke, Ungenauigkeit), Vorschlag (Doku oder Code nachziehen, Test
  ergänzen), Sicherheit. Nichts ohne Fundstelle; Vermutungen als solche markieren.
- Dazu: Begriffe, die an zwei Stellen verschieden heißen (Doku gegen Code, Doku gegen Doku).

Was das Skript nicht ausliest, bekommt eigene Gruppen: `okfw_serve.py` (Routen, Statuscodes, Köpfe), `rollen.py` (Unterbefehle,
Optionen, Exit-Codes, Sperren), `anbieter.toml`, `skills/recherche/scripts/api.py` und die Skills `recherche` und `lesen` (jeder
Befehl und jede Option, die sie nennen, gegen den Code). Dafür stellt der Hauptagent den Stand mit `git archive <rev>`
bereit. Genau hier fallen Optionen, Felder und Befehle durchs mechanische Raster, die kein Doku-Eintrag erwähnt.

Der Hauptagent prüft jede Fundstelle am Original unter `_stand/`, verwirft, was nicht trägt, und setzt die
übrigen Befunde mit `body` in die Karten: je Widerspruch ein Vermerk

```markdown
> ⚠️ **Widerspruch:** [konfiguration.md](/sources/okfw-konf.md) sagt X;[^konf-z61] der Kern tut Y.[^kern-z3035]
```

mit eigenen Fußnoten je Fundstelle (`sources <seite> --add konf-z61=okfw-konf`). Betrifft ein Widerspruch zwei
Karten, steht er auf beiden.

## 3. Gegenprüfung

`/lesen --pruefen` mit dem Agenten `beleg-pruefer` (frisch, nur lesend) über alle Karten mit Widerspruchsvermerk und
eine Stichprobe der übrigen. Befunde einarbeiten; Gegenprüfung mit `verify <seiten> --by <[wiki] actor> --note …`
vermerken. Nie `--by human:…`: Das Wiki wird weggeworfen, freigegeben wird es nicht.

## 4. Bericht

Synthese `syntheses/doku-und-code.md` im Wiki (Frage, Kerninsichten mit Tabelle der Widersprüche, Begriffe,
Lücken, Verwandte Seiten). Eine Kopie als Anhang am Release, ohne
Wiki-Links und Fußnoten, mit Datum, Commit, Zahl der Karten, Widersprüchen und Lücken, Modell und Anbieter je
Arbeitsschritt und was daraus wurde (behoben mit Commit, offen im Backlog).

## 5. Befunde umsetzen

- Eindeutige Widersprüche beheben (Doku oder Code nachziehen, Test ergänzen), je Gruppe ein Commit.
- Strittiges, das eine Entscheidung braucht, ins Backlog des Projekts.
- Ein neuer Lauf des Skripts auf dem Commit danach zeigt, ob die mechanischen Lücken zu sind.
