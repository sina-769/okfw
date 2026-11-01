# Selbstreview okfw 0.5.0 · 31.10.2026

Geprüfter Stand: Commit 0502867 (31.10.2026), im Kern noch `VERSION = "0.4.0"`, im CHANGELOG der Abschnitt
„Unveröffentlicht“ (das ist der Inhalt von 0.5.0). Verfahren: [selbstreview.md](selbstreview.md).
Wegwerf-Wiki: `.nachbau/selbstreview-0.4.0/` (nicht im Repo; der Ordnername folgt der Version im Kern), Lint dort
183 Seiten · 0 Fehler · 0 Warnungen · 0 Hinweise.

## Zahlen

| | Zahl |
|---|---|
| Konzeptkarten | 158 (17 Befehle, 65 Lint-Regeln, 72 `wiki.toml`-Schlüssel, 4 Hooks) und 4 Übersichten |
| Widersprüche (Doku sagt anderes, als der Code tut) | 2 |
| Ungenauigkeiten (Doku zu eng oder zu absolut formuliert) | 5 |
| Lücken (Doku verschweigt Verhalten) | 7 |
| Mechanisch: im Code, in keiner Doku | 0 |
| Mechanisch: in der Doku, nicht im Code | 0 |
| Mechanisch: ohne Test | 0 |
| Mechanisch: Schwere oder Hook-Eintrag weicht ab | 0 |
| Von den Helfern gemeldet, nach Prüfung am Original verworfen | 1 |

Zusätzlich vom Hauptagenten am Original geprüft: Die Tabelle „Lint-Codes und Standardschwere“ in
`konfiguration.md` stimmt mit `SEVERITY` überein (65 Codes: 24 Fehler, 31 Warnungen, 10 Hinweise, kein Code
fehlt, keiner steht an falscher Stelle), und jeder Code in `SEVERITY` hat mindestens einen `add(…)`-Aufruf, jeder
`add(…)`-Aufruf einen Eintrag in `SEVERITY` (keine Regel ohne Wirkung, keine unbekannte).

Nicht getan: Die Befunde stehen nicht als Vermerke in den Wegwerf-Karten, und die Gegenprüfung mit
`beleg-pruefer` (Abschnitt 3 des Verfahrens) ist nicht gelaufen. Jede Fundstelle unten hat der Hauptagent
selbst am Original unter `_stand/` gelesen.

## Blockiert das Release

Beides sind falsche Sätze in der Doku, je mit einem Satz zu beheben; kein Datenverlust, keine wirkungslose Regel.

1. **Guard, „Exit 2“** (`docs/konfiguration.md` Z. 710–712, Abschnitt zu `uv`): Die Doku begründet, warum der
   Guard-Aufruf kein `|| echo …` trägt: Die Rückfallmeldung „verschluckte die Ablehnung (Exit 2)“. Der Guard lehnt
   aber nie mit Exit 2 ab: `okf_hook.py` Z. 210–214 gibt `permissionDecision: "deny"` als JSON auf stdout aus und
   endet mit Exit 0 (`main`, Z. 407–419; im ganzen Hook-Skript steht kein `sys.exit`). Ein `|| echo` griffe nur bei
   Exit ≠ 0 und berührt die Ausgabe nicht. Der Schluss (Guard ohne Meldung bei fehlendem `uv`, Hinweis kommt einmal
   vom SessionStart) bleibt; nur die Begründung stimmt nicht. Gleiche Aussage stand schon im Backlog zum Selbstreview
   0.3.0 („Guard meldet ein fehlendes `uv` nicht; ein `|| echo` würde die Ablehnung (Exit 2) verschlucken“).
   Vorschlag: Begründung auf „käme bei jedem Edit in jedem Projekt, auch ohne Wiki“ kürzen.
2. **CHANGELOG, Abschnitt „Unveröffentlicht“** (Z. 41): „SessionStart und Stop suchen die `wiki.toml` aufwärts ab dem
   Projektordner, wie Guard und post-edit.“ Guard und post-edit suchen ab der Zieldatei
   (`okf_hook.py` Z. 58–63, `find_config` über `path.parents`), SessionStart und Stop ab `$CLAUDE_PROJECT_DIR`
   (Z. 291–296). Der Docstring des Hooks (Z. 16–17) und `SKILL.md` Z. 207–208 sagen es richtig.
   Vorschlag: „…, Guard und post-edit ab der Zieldatei.“

## Später

Verhalten ist richtig oder harmlos, die Doku ist nur zu eng, zu absolut oder schweigt. Eine Datei nach der anderen
nachziehen, je Gruppe ein Commit.

Ungenauigkeiten:

1. `fulltext_rights`: Doku (`konfiguration.md` Z. 296–297, `SKILL.md` Z. 125–126) sagt „obwohl das Recht dazu
   ungeklärt ist“; der Code (`okfw.py` Z. 1781) meldet bei `keep[0] in ("nein", "fragen")`, also auch bei
   ausdrücklich verneintem Recht (etwa über `[rights.<klasse>]`). Doku: „ungeklärt oder nicht erlaubt“.
2. `fulltext_shared`: Doku (`konfiguration.md` Z. 297–298) „obwohl die Lizenz keine Weitergabe erlaubt“; der Code
   (Z. 1785) meldet auch bei „fragen“ (Klasse `unbekannt`). Doku: „nicht erlaubt oder ungeklärt“.
3. Ereignis-Labels im Log (`konfiguration.md` Z. 270–271): „`create` steht immer als ‚Erstellt‘“; der Code
   (`okfw.py` Z. 1381, 1397) nimmt das Label aus `[log] labels`, und das Beispiel auf Z. 111
   (`labels = {create = "Creation"}`) überschreibt es. Doku: „standardmäßig“.
4. `dashboard --oeffentlich`: Der Hilfetext (`okfw.py` Z. 3530–3531) nennt fest
   `build/wiki-dashboard-oeffentlich.html`; der Code (`dashboard_path`, Z. 2764–2771) nimmt `[dashboard] public_path`
   oder leitet den Namen aus `[dashboard] path` ab. Hilfetext auf „Standard: `<path>` mit `-oeffentlich`“ ändern.
5. `[scope] derive` (`konfiguration.md` Z. 221–223): „der engste Umfang der Seiten, die sie verlinken oder in
   `sources[]` führen“; der Code (`okfw.py` Z. 1830) erbt nur von Seiten, die selbst keine Quellenkarten sind
   (`p.id not in sources`). `[visibility] derive` (Z. 1883–1893) dagegen schließt Quellenkarten als Nutzer ein
   und leitet transitiv ab. Eine Quelle, die nur von anderen Quellenkarten zitiert wird, bekommt beim Umfang
   `scope_missing`, bei der Sichtbarkeit erbt sie. Doku zu beiden Schlüsseln angleichen; ob die Asymmetrie gewollt
   ist, entscheidet die Person.

Lücken:

6. `raw_tampered` meldet auch, wenn eine im Manifest gelistete Datei **fehlt** (`okfw.py` Z. 1748–1749,
   `not target.exists() or …`); die Doku (`konfiguration.md` Z. 278, `SKILL.md` Z. 200) nennt nur „manipuliert“.
7. `source_missing` prüft nur Ressourcen mit Endung `.md` im Bundle (`okfw.py` Z. 1671–1673); externe Ressourcen und
   andere Endungen bleiben ungeprüft. Kein Auslöser in der Doku beschrieben.
8. `raw_uningested` ordnet ein Paket auch zu, wenn nur der Ordnername zum Dateinamen einer Quellenkarte passt
   (`stems`, `okfw.py` Z. 2171–2175), und prüft nur Ordner unter `[paths] raw`, keine losen Dateien. Kein Auslöser
   in der Doku beschrieben.
9. `[types.<typ>] label` ohne Wert nimmt den Ordnernamen, der selbst den Typnamen als Standard hat
   (`okfw.py` Z. 741–742); `konfiguration.md` Z. 177 nennt keinen Standard, obwohl Z. 77 „Nicht gesetzte Werte
   nehmen den Standard in Klammern“ verspricht.
10. `[trust] human`: Ein gesetzter, aber ungültiger Wert (ohne `human:`-Präfix oder gegen die Akteurskonvention)
    wird still ignoriert und durch die häufigste Kennung ersetzt (`okfw.py` Z. 2509–2511);
    `konfiguration.md` Z. 159 beschreibt nur den Fall ohne Angabe.
11. Der Stop-Hook nennt höchstens 20 Seiten (`STOP_LINES`, `okf_hook.py` Z. 42, 368–370) und hängt „… und N weitere
    Seiten“ an; `konfiguration.md` Z. 685 und `SKILL.md` Z. 216 schweigen über die Grenze (ein Test deckt sie:
    `test_reibung.py` Z. 203–208).
12. `gitattributes` fehlt in der Befehlstabelle von `konfiguration.md` (Z. 56–73), steht aber in `SKILL.md`
    Z. 61 und im CHANGELOG.

Verworfen nach Prüfung: Der Helfer für die Befehle meldete, `tests/test_abdeckung.py` fehle (`README.md` Z. 116–118
nennt sie). Sie liegt im Commit; `_stand/` enthält sie nicht, weil das Skript die Prüfwerkzeuge selbst nicht ins
Wegwerf-Wiki nimmt. Kein Befund.

## Begriffe

Verschieden benannt, ohne dass es verschiedene Dinge sind (Neues gegenüber dem Bericht zu 0.3.0, dessen Liste
sonst weiter gilt, auch die deutschen und englischen Optionen und die Aliase, die das Skript auflistet):

- Ablehnung des Guards: „Exit 2“ (Doku) gegen `permissionDecision: "deny"` (Code), siehe Blockiert 1.
- „PostToolUse-Hook“ (`SKILL.md` Z. 73) gegen Handler `post-edit` (`okf_hook.py` Z. 415, `hooks.json`);
  `SKILL.md` Z. 73 nennt „Edit oder Write“, der Matcher heißt `Write|Edit|MultiEdit`.
- Zwei Runden des Stop-Hooks: „höchstens zweimal je Sitzung“, „zwei Runden je `session_id`“ (Doku) gegen
  `STOP_ROUNDS` (Code); gleiche Sache.
- Projektionen, generierte Dateien, generierter Export (Guard-Meldung `okf_hook.py` Z. 124) gegen `[export.*]`,
  das nur CSL und Literaturverzeichnis meint.
- „zitieren“ (`konfiguration.md` Z. 139) gegen „verlinken oder in `sources[]` führen“ (Z. 223) für dieselbe
  Ableitung des Umfangs.
- Quellenkarte: Doku über den Typ `source`; der Code zählt auch Karten im Quellenordner mit anderem Typ
  (`okfw.py` Z. 2179–2182).
- „offene `[!todo]`“ (Statuszähler) gegen „Offene Stellen“ im Dashboard (`[!todo]` plus Widerspruchskästen und
  „zu prüfen“): ähnlicher Name, verschiedener Umfang.

## Lauf

Mechanisch (`tests/selbstreview.py --rev 0502867 --neu`): Die Befundliste ist leer, alle Lücken aus dem Lauf zu 0.3.0
sind zu. Das Lesen der Karten fand deutlich weniger als zu 0.3.0 (dort 23 Widersprüche und 25 Lücken), weil die
Umsetzung der damaligen Befunde die Doku angeglichen hat. Die Helfer lasen für jede Gruppe Karte, Doku und Code im
Original unter `_stand/`; mehr als die Hälfte der Karten meldeten sie ohne Befund. Das heißt nicht, dass nichts mehr
auseinanderläuft: Jeder Helfer sah nur seine Gruppe, und ein mittelgroßes Modell übersieht Verhalten, das in keiner
Doku vorkommt.

## Modelle

| Schritt | Modell, Anbieter |
|---|---|
| Skript, Aufträge, Prüfung jeder Fundstelle am Original (Code, Doku, `SEVERITY`-Abgleich), Bericht | Claude Sonnet 5.5, Anthropic |
| Befund-Entwurf Befehle (17 Karten, Parser, Exit-Codes) | GLM 5.3, externer Anbieter, 965 s, 59 Züge |
| Befund-Entwurf Hooks (4 Karten, Ereignisse, Freigabe) | GLM 5.3, externer Anbieter, 259 s, 24 Züge |
| Befund-Entwurf Lint-Regeln 1 (33 Karten) | GLM 5.3 Flash, externer Anbieter, 258 s, 109 Züge |
| Befund-Entwurf Lint-Regeln 2 (32 Karten) | GLM 5.3 Flash, externer Anbieter, 506 s, 115 Züge |
| Befund-Entwurf Schlüssel 1 (36 Karten) | GLM 5.3 Flash, externer Anbieter, 648 s, 159 Züge |
| Befund-Entwurf Schlüssel 2 (36 Karten) | GLM 5.3 Flash, externer Anbieter, 262 s, 111 Züge |
| Gegenprüfung (`/pruefen`, `beleg-pruefer`) | nicht gelaufen |

Die Inhalte waren öffentlich (Plugin-Code), die Worker liefen nur lesend und ohne Netz außer dem Modellzugang.
