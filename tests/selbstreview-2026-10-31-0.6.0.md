# Selbstreview okfw 0.6.0 · 31.10.2026

Geprüfter Stand: Commit 4038df0 (31.10.2026), `VERSION = "0.6.0"`, im CHANGELOG „Unveröffentlicht“ mit den Abschnitten 0.6.0 und
„Aus 0.5.0“. Verfahren: [selbstreview.md](selbstreview.md). Was der Bericht zu 0.5.0
([selbstreview-2026-10-31-0.5.0.md](selbstreview-2026-10-31-0.5.0.md)) schon führt, ist nicht neu erarbeitet; geprüft ist der Zuwachs:
Bearbeiten im Dashboard, Modellrollen, Skills `recherche` und `lesen`, Schaubilder, Design, Aufnahme (Rückzugsprüfung, `--aus-lauf`).
Wegwerf-Wiki: `.nachbau/selbstreview-0.6.0/` (nicht im Repo), Lint dort 199 Seiten · 0 Fehler · 0 Warnungen · 0 Hinweise.

## Zahlen

| | Zahl |
|---|---|
| Konzeptkarten | 169 (19 Befehle, 70 Lint-Regeln, 76 `wiki.toml`-Schlüssel, 4 Hooks) und 4 Übersichten |
| Widersprüche (Doku sagt anderes, als der Code tut) | 2 |
| Ungenauigkeiten (Doku zu eng oder zu absolut formuliert) | 12 |
| Lücken (Doku verschweigt Verhalten) | 7 |
| Sonstiges (Tippfehler, veraltete Verweise im README) | 2 |
| Mechanisch: im Code, in keiner Doku | 0 |
| Mechanisch: in der Doku, nicht im Code | 0 |
| Mechanisch: ohne Test | 0 |
| Mechanisch: Schwere oder Hook-Eintrag weicht ab | 0 |
| Von den Helfern gemeldet, nach Prüfung am Original verworfen oder abgestuft | 4 |

Zuwachs gegenüber 0.5.0: 11 Karten (2 Befehle `serve` und `abbildung`, 5 Lint-Regeln `figure_caption`, `figure_missing`, `figure_rights`,
`rollen_unknown`, `design_unsafe`, 4 Schlüssel `[sources] retraction_field`, `[rollen] daten`, `[rollen] sperre`, `[dashboard] design`).

Vom Hauptagenten am Original geprüft, ohne Befund: Jeder Befehl, jede Option und jeder Exit-Code, den `skills/recherche/SKILL.md` und
`skills/lesen/SKILL.md` nennen, existiert und verhält sich so (`okfw.py`: `status`, `lint`, `new source` mit `--doi`, `--aus-lauf`,
`--kandidat`, `--set`, `--access`, `fundweg` mit `--aus-lauf` und `--add`, `lizenz --fetch`, `rechte --json`, `capture`, `hash --from`,
`body --detail`, `verify`; `api.py`: `suche`, `dienste`, `eintrag`, `zusammenfuehren`, `aufloesen`, `archivieren`, `volltext` mit
`--xml` und PMCID, `abstract`, `anker`; `rollen.py`: `zeige --json`, `setze`, `status`, `auftrag --rolle --datei --anhang`; `web-recherche`
mit `--ziel`, `--domains`, `--modell`; Exit 3 bei Rückzug in `okfw.py`, Exit 2, 3, 10 in `rollen.py`). Die Schlüssel von `kandidaten.json`
(`nr`, `funde`, `nur_websuche`, `aufgeloest`, `at_aus_auftrag`) schreibt `api.py zusammenfuehren` so, wie `okfw.py fundwege_aus_lauf`
sie liest. Die Variablenliste in „Design“ (`konfiguration.md` Z. 842–854) deckt sich mit `:root` und `:root[data-theme="dark"]` der
Vorlage, die Routen- und Köpfe-Tabellen des Servers mit `okfw_serve.py`, die Elementliste und die Grenzen der SVG-Bereinigung (200 KB,
5000 Elemente, 60 Ebenen) mit `clean_svg`, die Sperrgründe mit `rollen.loese`.

Nicht getan: Die Befunde stehen nicht als Vermerke in den Wegwerf-Karten, und die Gegenprüfung mit `beleg-pruefer` (Abschnitt 3 des
Verfahrens) ist nicht gelaufen; sie läuft getrennt. Jede Fundstelle unten hat der Hauptagent selbst im Original gelesen.

## Blockiert das Release

Beides sind falsche Sätze in der Doku, je mit einem Satz zu beheben; kein Datenverlust, keine wirkungslose Regel.

1. **Skill `pruefen` gibt es nicht mehr** (`skills/okfw/docs/konfiguration.md` Z. 396, Tabelle „Freigabe über Claude“, Stufe
   „geprüft“): „Gegenprüfung am Original (Skill `pruefen` oder ein Prozess)“. Der Skill `pruefen` entfällt mit 0.6.0 (CHANGELOG
   Z. 15–16: „`/quelle` und `/pruefen` entfallen“); die Gegenprüfung ist der Pflichtschritt von `lesen` und einzeln `/lesen --pruefen`
   (`skills/lesen/SKILL.md` Z. 97–114; so nennt es auch `konfiguration.md` Z. 169 und `skills/okfw/SKILL.md` Z. 141). Im ganzen Plugin
   ist das die einzige Stelle, die den alten Skill noch nennt. Vorschlag: „Skill `lesen` (`/lesen --pruefen`)“.
2. **`rollen.py zeige` nennt die Herkunft nur mit `--json`** (`konfiguration.md` Z. 543: „aufgelöste Rolle mit Herkunft und Sperre“, und
   Z. 549: „`zeige` nennt die Herkunft jedes Felds“). Die Textausgabe (`rollen.py` Z. 619–621) enthält Rolle, Harness, Anbieter, Modell und
   gegebenenfalls die Sperre; das Feld `herkunft` steht nur im Wörterbuch aus `loese` (Z. 147–148) und damit nur in der JSON-Ausgabe.
   Vorschlag: Doku auf „mit `--json`“ ändern oder die Herkunft in die Textzeile aufnehmen (und `test_rollen.py` darauf setzen).

## Später

Verhalten ist richtig oder harmlos, die Doku ist nur zu eng, zu absolut oder schweigt. Eine Datei nach der anderen nachziehen, je Gruppe
ein Commit.

Ungenauigkeiten:

1. `konfiguration.md` Z. 886: „liest oder schreibt nichts außerhalb des Bundles (einzige Ausnahme: die Designdatei im Projektordner)“. Der
   Server schreibt auch die Nutzerdatei von `rollen.py` (`okfw_serve.py` Z. 369, 396, 404; die Doku selbst Z. 931), legt Temp-Ordner an
   (Z. 313, 508) und lässt `rollen.py` ein Protokoll schreiben. Gemeint ist: keine Pfade aus Anfragen, nichts im Wiki außerhalb des Bundles.
2. `konfiguration.md` Z. 946: „415 kein JSON“. Der Server sendet 415 nur bei falschem `Content-Type` (`okfw_serve.py` Z. 651–652); ein
   Körper, der kein JSON ist, ergibt 400 (Z. 660–662, 663–664). Ebenso fehlen in der Liste 400, 411 (kein `Content-Length`, Z. 654–656),
   500 (Z. 685) und 501 (Z. 325).
3. `konfiguration.md` Z. 497 und 508: Die Bereinigung setzt `role="img"` und `aria-label` „beim Ablegen und bei jedem Bau“. Beim Ablegen
   ruft `cmd_abbildung` `clean_svg` ohne Titel auf (`okfw.py` Z. 3785), die abgelegte Datei hat also kein `aria-label`; erst der
   Dashboard-Bau übergibt den Alt-Text (Z. 3216).
4. `konfiguration.md` Z. 67: `[sources] updates_field` „bei Rückzug oder Korrektur“. Der Code schreibt das Feld bei jedem `updated-by`-Typ
   von Crossref (`okfw.py` Z. 1452, 3632–3633), auch bei „expression_of_concern“.
5. `viewBox`: CHANGELOG Z. 59–60 und `konfiguration.md` Z. 69 („fehlendes `viewBox` → Exit 2“). Fehlt `viewBox`, aber `width` und `height`
   sind Zahlen, leitet `clean_svg` es ab (`okfw.py` Z. 436–440); Exit 2 gibt es nur ohne beides. `konfiguration.md` Z. 499–501 sagt es richtig.
6. `konfiguration.md` Z. 535: Harness `container` mit „Anbieter fest“. `rollen.loese` verlangt für `webabruf` nur den Harness `container`
   und einen bekannten Anbieter (`rollen.py` Z. 152–157); fest ist der Anbieter nur in der Oberfläche (`serve.js` Z. 568–571) und
   ab Werk (`anbieter.toml` Z. 51–54).
7. „Geschrieben wird nur über den Kern“ (CHANGELOG Z. 25, `skills/okfw/SKILL.md` Z. 164) stimmt für Karten, Freigabe und Abbildungen,
   nicht für Einstellungen und Design: Der Server schreibt die Nutzerdatei und `design.css` selbst (`okfw_serve.py` Z. 369–404, 452–454),
   ohne Ereignis. `konfiguration.md` Z. 890 begrenzt den Satz richtig auf Karten.
8. `skills/recherche/SKILL.md` Z. 64: „Der Fundweg bleibt `websuche`“. Im Wiki gibt es die Art `websuche` nicht (`FUNDWEG_ARTEN`, `okfw.py`
   Z. 213–216); ein aufgelöster Treffer wird Art `suche` mit dem `db` des auflösenden Abrufs (`fundwege_aus_lauf`, Z. 2402). Im Protokoll
   bleibt `weg: websuche`. Der Satz sagt nicht, welches von beiden gemeint ist.
9. `skills/recherche/SKILL.md` Z. 16 und 123: `--max` setzt „die Treffer je Abruf“, in `api.py` sind es die Treffer insgesamt, seitenweise
   geholt (Z. 908); die Trefferzeile nennt neun Felder, `api.py` schreibt je Dienst weitere (`epmc`, `oa`, `typ`, `openalex`, `s2`, `url`).
10. `skills/lesen/SKILL.md` Z. 109: „Warnt `$R status`, dass Lesen und Gegenprüfen dasselbe Modell nutzen“. `rollen.warnungen` vergleicht
    Anbieter und Modell zusammen (`rollen.py` Z. 180); `konfiguration.md` Z. 577 sagt „demselben Anbieter und Modell“.
11. CHANGELOG Z. 32–34 und 61–62: Die Rückzugsmeldung nennt bei PubMed auch die Hinweis-PMID (`okfw.py` Z. 491–492), und `ergebnis` kann mit
    `--trotz-rueckzug` `bedenken` oder `zurueckgezogen` sein (Z. 1509–1510; `konfiguration.md` Z. 259 nennt alle fünf). `figure_rights`
    meldet bei jedem Recht außer „erlaubt“ (`okfw.py` Z. 2131), nicht nur bei „nicht erlaubt“; `konfiguration.md` Z. 342–345 stimmt.
12. `skills/okfw/SKILL.md` Z. 85–86: Die Rückzugsprüfung nennt von PubMed nur `RetractionIn` und `ExpressionOfConcernIn`; der Code prüft auch
    den Publikationstyp „Retracted Publication“ (der allein Exit 3 auslöst), `ErratumIn` und `CorrectedandRepublishedIn`
    (`okfw.py` Z. 513–522; `konfiguration.md` Z. 240–241 vollständig).


Lücken:

13. `konfiguration.md` Z. 939–940: Die Schnittstellentabelle nennt für `POST /api/seite` das Feld `vorschlag` nicht (`okfw_serve.py` Z. 241;
    die Oberfläche setzt es nach „Annehmen“, `serve.js` Z. 446) und für `POST /api/neu` keine Felder (`typ`, `slug`, `titel`,
    `beschreibung`, Z. 266).
14. Der Modellvorschlag prüft bei Quellenkarten die Nutzungsrechte: `ki_auswertung` „nein“ lehnt mit 422 ab, „bedingt“ und „fragen“ geben
    eine Warnung (`okfw_serve.py` Z. 499–505). `konfiguration.md` Z. 908–915 schweigt dazu.
15. Design-Ablehnung unvollständig beschrieben (`konfiguration.md` Z. 347 und 833–836, `skills/okfw/SKILL.md` Z. 197–198, CHANGELOG
    Z. 67–68): `css_problem` lehnt auch `expression(`, `-moz-binding`, `behavior:`, `url(` ohne Ende ab (`okfw.py` Z. 462–467), und
    `design_css` eine Datei, die nicht UTF-8 ist (Z. 1894). Der Server verwirft zusätzlich Designs über 256 KB (`okfw_serve.py`
    Z. 36, 74–75), was `konfiguration.md` Z. 929–930 („Die Prüfung ist die des Lints `design_unsafe`“) nicht sagt; der Lint kennt die Grenze nicht.
16. `konfiguration.md` Z. 541–547: Der Befehlsblock zu `rollen.py` nennt den Befehl `anbieter` (Optionen `--name`, `--openai`, `--anthropic`,
    `--schluessel`, `--schluessel-env`, `--hinweis`, `--daten`, `--entfernen`), die Option `--wiki` und `--json` bei `zeige`, `modelle`
    und `auftrag` nicht (`rollen.py` Z. 597–611; der Kopf der Datei Z. 7–12 führt sie).
17. `vorbefehl` lässt sich weder über die Befehlszeile noch über die Oberfläche setzen oder löschen; ein vorhandener Wert bleibt beim
    Speichern erhalten (`rollen.py` Z. 402–403). Wer ihn braucht, trägt ihn von Hand in die Nutzerdatei ein; `konfiguration.md` Z. 539
    und 925 sagen nur, die Oberfläche setze ihn nicht.
18. Das Schreiben der Nutzerdatei (`rollen.py` Z. 329–342, 401–405) erzeugt sie neu aus `[person]`, `[anbieter]` und `[rollen]`:
    Kommentare und fremde Tabellen gehen verloren, und beim Speichern eines eigenen Anbieters über Oberfläche oder `rollen.py anbieter`
    entfällt dessen von Hand eingetragene Liste `modelle` (die Oberfläche übergibt sie nicht, `okfw_serve.py` Z. 384–392). Kein Fehler,
    aber unbeschrieben.
19. `api.py` hat mehr, als die Texte nennen: den Dienst `cellar` (EU-Cellar, SPARQL; CHANGELOG Z. 17 führt sechs Dienste, `agents/fund-api.md`
    führt ihn), den Schlüssel `SEMANTIC_SCHOLAR_API_KEY` als Alias zu `S2_API_KEY` (`api.py` Z. 77, 318; CHANGELOG Z. 18,
    `skills/recherche/SKILL.md` Z. 31) und den Befehl `abstract` in der Befehlsliste (`skills/recherche/SKILL.md` Z. 121–122; der Befehl
    selbst steht in `skills/lesen/SKILL.md` Z. 57 richtig).

Sonstiges:

20. `konfiguration.md` Z. 830–831: „bettet der Bau nach der Vorlage in denselben `<style>`-Block einbettet“ (doppeltes Verb).
21. `README.md` Z. 157 und 185 nennen als letzten Selbstreview den zu 0.3.0 und als letztes Protokoll das zu 0.4.0. Mit diesem Bericht und
    dem Nachbau zu 0.6.0 nachziehen (Release-Schritt 2 und 3).

Verworfen oder abgestuft nach Prüfung:

- Ein Helfer meldete als Widerspruch, `README.md` Z. 152–153 nenne `install.py`, das nicht auffindbar sei. Es liegt im Arbeits-Repository
  (`install.py` Z. 9: `anwendungen`); der Helfer sah nur den Plugin-Ordner. Kein Befund.
- Ein Helfer meldete „415 kein JSON“ als blockierenden Widerspruch. Der Satz lässt sich als „kein JSON-`Content-Type`“ lesen; abgestuft auf
  Ungenauigkeit 2.
- Ein Helfer meldete, `skills/lesen/SKILL.md` Z. 54 „nummeriert Abschnitte und Absätze“ stimme nicht, weil Abschnitte nur Überschriften
  tragen. Der Satz erklärt in derselben Klammer die Form (`## Abschnitt`, `[Abs. n]`); Wortklauberei, kein Befund.
- Ein Helfer meldete, der Exit-Code von `rollen.py auftrag` weiche mit `vorbefehl` ab. Der Platzverwalter startet `rollen.py` selbst und
  reicht dessen Exit-Code durch (`rollen.py` Z. 638–641). Kein Befund.

## Begriffe

Verschieden benannt, ohne dass es verschiedene Dinge sind (Neues gegenüber dem Bericht zu 0.5.0, dessen Liste sonst weiter gilt):

- Schaubild (Doku, CHANGELOG) gegen Abbildung (Befehl `abbildung`, Ordner `abbildungen/`, Meldungen) gegen `figure_*` (Lint-Codes, Funktionen).
  Die zwei Platzhaltertexte „Abbildung nicht eingebunden“ (Schaubild fehlt oder abgelehnt) und „Bild nicht eingebunden“ (jedes andere Bild)
  stehen beide in `okfw.py` Z. 3218, 3224.
- Weg (Protokoll von `recherche`: `tooluniverse`, `api`, `websuche`, `container`, `modellvorschlag`) gegen Art (Fundweg im Wiki:
  `suche`, `modellvorschlag`, `material`, `person`, `zitatverfolgung`); `fundwege_aus_lauf` bildet die Wege auf Arten ab, der Skill nennt
  beide „Fundweg“.
- „Oberfläche“ (`konfiguration.md` Z. 931) gegen „`okfw.py serve`, Einstellungen“ (`anbieter.toml` Z. 2): dasselbe Schreibwerkzeug.
- Option `--schluessel-env` (`rollen.py` Z. 606) gegen Feld `schluessel_env` (TOML, Z. 63).
- „Hinweis“ als Lint-Schwere `info` gegen `rueckzug.hinweise` (Befunde von Crossref und PubMed).
- Strg+C (Doku) gegen Ctrl+C (Ausgabe von `serve`, `okfw_serve.py` Z. 702).

## Lauf

Mechanisch (`tests/selbstreview.py --rev 4038df0 --neu`): Die Befundliste ist leer; die 11 neuen Karten des Zuwachses stehen in der Doku und
werden von je einem Test berührt. Das Skript prüft nur, was es aus `okfw.py`, `okf_hook.py` und den Profilen liest. Nicht in `_stand/` und
nicht im Inventar sind `okfw_serve.py` (Routen, Statuscodes, Köpfe), `rollen.py` (Unterbefehle, Optionen, Exit-Codes), `anbieter.toml`,
`recherche/scripts/api.py` und die Skills `recherche` und `lesen`; für diesen Lauf wurden sie mit `git archive 4038df0 plugins/okfw` im
Scratchpad bereitgestellt und gelesen. Die Lücken 13, 16 und 19 zeigen, was dabei durchs Raster fällt (Optionen, Felder, Befehle, die kein
Doku-Eintrag erwähnt). Vorschlag für das Skript: `rollen.py` und `api.py` wie `okfw.py` über argparse auslesen, die Routen aus
`okfw_serve.py`.

Die Helfer lasen je Gruppe Doku und Code im Original und meldeten zusammen 26 Befunde; 22 trugen nach der Prüfung, 3 sind verworfen und
1 abgestuft. Z. 396 (Skill `pruefen`) hat kein Helfer gefunden; der Hauptagent fand ihn mit einer Suche nach den entfallenen Skills im
ganzen Plugin. `zeige` meldete der Helfer zu `rollen.py` als Ungenauigkeit; der Hauptagent stufte es hoch, weil die Doku zweimal behauptet,
`zeige` nenne die Herkunft, und die Textausgabe es nicht tut. Das heißt nicht, dass nichts mehr auseinanderläuft: Jeder Helfer sah nur seine
Gruppe, und ein kleines Modell übersieht Verhalten, das in keiner Doku vorkommt.

## Modelle

| Schritt | Modell, Anbieter |
|---|---|
| Skript, Aufträge, Prüfung jeder Fundstelle am Original (Server, `rollen.py`, `clean_svg`, `check_retraction`, Skills gegen alle Befehle), Suche nach entfallenen Skills, Bericht | Claude Sonnet 5.5, Anthropic |
| Befund-Entwurf (a1) „Bearbeiten im Dashboard“ gegen `okfw_serve.py`, `serve.js` | GLM 5.3 Flash, externer Anbieter, 527 s, 72 Züge |
| Befund-Entwurf (a2) „Modellrollen“ gegen `rollen.py`, `anbieter.toml` | GLM 5.3 Flash, externer Anbieter, 73 s, 6 Züge |
| Befund-Entwurf (b1) Skill `recherche` gegen `okfw.py`, `api.py`, `rollen.py`, Agenten | GLM 5.3 Flash, externer Anbieter, 360 s, 42 Züge |
| Befund-Entwurf (b2) Skill `lesen` gegen `okfw.py`, `api.py`, `rollen.py`, `beleg-pruefer` | GLM 5.3 Flash, externer Anbieter, 281 s, 27 Züge |
| Befund-Entwurf (c) CHANGELOG 0.6.0 und README gegen den Code | GLM 5.3 Flash, externer Anbieter, 231 s, 74 Züge |
| Befund-Entwurf (d) Schaubilder, Design, Aufnahme gegen `okfw.py` | GLM 5.3 Flash, externer Anbieter, 204 s, 35 Züge |
| Gegenprüfung (`beleg-pruefer`) | nicht gelaufen, läuft getrennt |

Die Inhalte waren öffentlich (Plugin-Code), die Worker liefen nur lesend und ohne Netz außer dem Modellzugang.
