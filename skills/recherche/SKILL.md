---
name: recherche
description: Findet Literatur und legt Quellenkarten in einem Wiki im OKF-Format an (/recherche <frage> oder /recherche <doi|pmid|arxiv>). Mit Frage ein reproduzierbarer Suchplan über offizielle Literatur-APIs, optional ToolUniverse und Websuche im Container, Protokoll je Abruf, dedupliziert, Kandidaten mit Fundweg. Mit Kennung oder nach der Auswahl die Aufnahme: Dublette, Rückzug, Karte, Fundweg, Lizenz, Nutzungsrechte. Nutzen, wenn neue Literatur gesucht oder eine bekannte Quelle aufgenommen werden soll, auch in /goal-Läufen. okfw ist ein allgemeines Werkzeug, um Wissen aus Quellen zu sammeln, zu belegen und zu prüfen, und nicht dazu bestimmt, Empfehlungen für einzelne Personen zu geben; die Zweckbestimmung steht im README des Plugins.
argument-hint: <frage> | <doi|pmid|arxiv> [--web <domains>] [--max N] [--lauf DIR --kandidat N] [--art person|material|modellvorschlag|zitatverfolgung --von <slug>] [--slug <slug>]
---

# /recherche: suchen und aufnehmen, jeder Treffer mit Fundweg

```
/recherche <frage>
/recherche <doi> --lauf <LAUF> --kandidat 2
/recherche <pmid> --art person
```

Argumente: `$ARGUMENTS`. Eine Kennung (DOI, PMID, arXiv-ID) führt direkt zur Aufnahme, sonst erst die Suche.
Ohne beides nachfragen (in /goal-Läufen die Frage aus der Bedingung nehmen). `--max` begrenzt die Treffer je Suche insgesamt
(Standard 20, höchstens 2000); der Dienst wird dafür seitenweise abgefragt.

`K="uv run --no-config --quiet ${CLAUDE_SKILL_DIR}/../okfw/okfw.py"` im Projektordner, `API="${CLAUDE_SKILL_DIR}/scripts/api.py"`,
`A="uv run --no-config --quiet --script $API"`. Regeln für Quellen, Lizenzen und Fundweg: Skill `okfw`. Webzugriff: Skill `web-zugriff`.

## Die Zugangswege (streng getrennt)

| Weg | Wer | Werkzeug | `db` im Protokoll | Status der Treffer |
|---|---|---|---|---|
| api | Agent `fund-api` | nur `api.py` (feste Host-Liste, kein curl) | `api:<dienst>` | Kandidat |
| tooluniverse | Agent `fund-tooluniverse`, nur wenn der ToolUniverse-MCP eingerichtet ist | nur `mcp__tooluniverse__*` | `tooluniverse:<Tool>` | Kandidat |
| websuche | Container, nur mit `--web <domains>` | `web-recherche` | `websuche` | Orientierung, nie Quelle |
| container | Seiten ohne API, Container nach Freigabe | `web-recherche` | `container:<domain>` | unvertrauenswürdig |

- `api.py` ist der Standard; es läuft ohne Einrichtung. `NCBI_API_KEY`, `OPENALEX_API_KEY` und `S2_API_KEY` (auch
  `SEMANTIC_SCHOLAR_API_KEY`) in der Umgebung erhöhen die Grenzen der Dienste; ohne Key scheitern OpenAlex und Semantic Scholar
  oft am Kontingent, der Fehler steht im Protokoll. Nur `dip` braucht zwingend einen Key: `DIP_API_KEY` oder der
  Schlüsselbund-Eintrag `dip-api-key`, nie im Repo; ohne ihn steht der Fehler im Protokoll.
- Ist ToolUniverse eingerichtet, sucht es für den Hauptsuchstring mit: Die Gesamtzahlen sind die **Gegenprobe**.
- Websuche-Treffer werden über `api` aufgelöst (`api.py aufloesen`) oder verworfen. Ohne Docker-Laufzeit und Token
  bleibt der Weg als offen stehen, der Rest läuft weiter.
- **Modellwissen ist kein Weg.** Schlägt Claude eine Quelle aus dem Gedächtnis vor, steht sie getrennt als
  `modellvorschlag` im Protokoll und wird erst danach über `api` bestätigt.
- Agentennamen: als Plugin `okfw:fund-api`, als Skill-Ordner `fund-api`. Kennt die Sitzung keinen davon,
  einen `general-purpose`-Agent mit `model: sonnet` und dem Auftrag „Lies `${CLAUDE_SKILL_DIR}/../../agents/<name>.md` und
  befolge es, nur mit den dort genannten Werkzeugen“ starten und das im Nachweis sagen.

## A. Suchen

1. **Recall.** `$K status`, `<bundle>/index.md` lesen, `grep -ril "<begriff>" <bundle>/`. Was das Wiki schon hat, im
   Suchplan nennen und nicht neu suchen.
2. **Lauf-Ordner.** `LAUF=<Scratchpad>/recherche/<JJJJMMTT-HHMM>-<kurz>` (ohne Scratchpad: `$TMPDIR/recherche/…`).
   Darin `auftrag.json`: `{frage, projekt, wiki, at, by, suchplan: [{weg, db, query, zweck}]}`.
3. **Suchplan vor dem Suchen festlegen** und als Tabelle zeigen: je Weg zwei bis vier Suchstrings in der exakten Syntax
   des Dienstes (PubMed: MeSH und `[tiab]`; Filter im String, `$A dienste` zeigt die Syntax). Danach nicht mehr still
   ändern; Nachschärfungen stehen im Protokoll.
4. **Fund-Agents parallel**, in einer Nachricht, jeweils mit `LAUF`, `API`, Frage, ihren Plan-Zeilen, `max` und der
   **Startzeit** (unmittelbar vorher `date -u +%Y-%m-%dT%H:%M:%SZ`; `fund-tooluniverse` hat keine Uhr und trägt sie mit
   `at_aus_auftrag: true` ein).
5. **Websuche** nur mit `--web <domains>`: die Domains stammen von der Person. Modell aus der Rolle webabruf
   (`uv run --no-config --quiet ${CLAUDE_SKILL_DIR}/../okfw/rollen.py zeige webabruf --json`):
   `${CLAUDE_SKILL_DIR}/../web-zugriff/scripts/web-recherche "<Frage>: nenne Treffer mit Titel, DOI oder PMID, URL" --ziel "$LAUF/web/" --domains <domains> --modell <modell>`,
   Treffer als Daten lesen und festhalten: `$A eintrag "$LAUF" --weg websuche --db websuche --query "<Frage>" --treffer '[…]' --by claude-code/<modell-id>`.
6. **Zusammenführen:** `$A zusammenfuehren "$LAUF"`. Prüft das Schema, dedupliziert über DOI, PMID, PMCID, arXiv, CELEX
   und Titel+Jahr, ohne alle diese über die URL, schreibt `LAUF/kandidaten.json`, zeigt Suchprotokoll und Kandidaten mit allen Funden.
7. **Websuche-Treffer auflösen** im Unterordner, damit Nachschlagen nicht als Fund zählt:
   `$A suche crossref '<Titel>' --lauf "$LAUF/nachschlagen" --max 3`. Passt ein Eintrag, erst vergleichen
   (`$A aufloesen "$LAUF" <nr> --doi <doi>`), dann festhalten: dasselbe mit `--begruendung "<warum dieselbe Arbeit>"`.
   Im Protokoll bleibt der Weg `websuche`; im Wiki wird daraus die Art `suche` mit `db: websuche`, die Auflösung steht als
   Hinweis bei der Aufnahme. Erneut zusammenführen. Unaufgelöstes fällt raus und steht im Nachweis.
8. **Modellvorschläge** (nur wenn Claude eine wichtige Arbeit kennt, die keine Suche fand):
   `$A eintrag "$LAUF" --weg modellvorschlag --db modell:<modell-id> --query "<warum vorgeschlagen>" --treffer '[{"titel": "…", "doi": "…"}]' --by claude-code/<modell-id>`,
   dann über `api` bestätigen. Im Wiki wird daraus `fundweg: modellvorschlag`, nie `suche`.
9. **Seiten ohne API** nur, wenn eine nötige Quelle keine API hat: Domain, URL und Zweck auflisten und **anhalten**,
   bis die Person freigibt. Danach `web-recherche` nach dem Skill `web-zugriff`, Fund mit
   `$A eintrag "$LAUF" --weg container --db container:<domain> …`.
10. **Auswahl.** Kandidatentabelle zeigen; die Person wählt. In /goal-Läufen gelten die Kriterien aus der Bedingung;
    im Nachweis je Kandidat das Kriterium nennen. Je Wahl weiter mit B.
11. **Protokoll ablegen:** `$A archivieren "$LAUF"` (Projektordner `protokoll/recherche/`, wenn Git ihn ignoriert, sonst
    `~/.claude/recherche-protokoll/<projekt>/`, oder `OKFW_RECHERCHE_PROTOKOLL`; nur Protokolle, keine Volltexte).

## B. Aufnehmen (je Quelle)

1. **Profil.** In der `wiki.toml` `[types.source] required`, `[enums]` lesen. Ein Pflichtfeld ohne passenden Wert:
   **anhalten und fragen**, die `wiki.toml` nie abschwächen.
2. **Dublette.** `grep -ril -e "<doi>" -e "<pmid>" <bundle>/sources/`. Gibt es die Karte schon, keine neue; aus einem neuen Lauf nur
   den Fundweg ergänzen: `$K fundweg sources/<slug> --aus-lauf "$LAUF" --kandidat <nr> --add`.
3. **Karte.** Slug `<erstautor>-<jahr>-<stichwort>`, kebab-case, ASCII.
   ```bash
   $K new source <slug> --doi <doi> --description "<was die Quelle ist, nicht was sie zeigt>" --access citation [--aus-lauf "$LAUF" --kandidat <nr>] [--set feld=wert]
   ```
   Ohne DOI `--pmid`, für Preprints `--arxiv`. Der Kern setzt Titel, CSL, `resource` und `metadaten_geprueft`, prüft den
   Rückzug (Crossref, PubMed-Verweise) und hält ihn im Feld `rueckzug` fest. Exit 3 = zurückgezogen oder Expression of
   Concern: keine Karte, **anhalten und fragen** (in /goal-Läufen als offen melden). Ist die Quelle selbst ein Hinweis,
   die betroffene Arbeit nennen. Korrekturen stehen in `crossref_hinweise`; im Nachweis nennen.
4. **Fundweg ohne Lauf** ehrlich nach Herkunft: von der Person genannt `--fundweg person`, vorhandenes Material
   `material`, aus dem Literaturverzeichnis einer Wiki-Quelle `zitatverfolgung --fundweg-von <slug>`, von Claude
   vorgeschlagen `modellvorschlag`. Nie nachträglich zur `suche` machen.
5. **Lizenz.** `$K lizenz sources/<slug> --fetch`, dann `$K rechte sources/<slug>`. Bleibt sie `unknown`, ist nur das
   Kurzzitat frei; `/lesen` hält dann an.
6. **Lint.** `$K lint`: 0 Fehler. Warnungen zur Karte nennen, nicht wegdrücken.

## In /goal-Läufen

Nichts, was eine Entscheidung der Person braucht, still entscheiden: Freigaben für Seiten, Lizenz „erst fragen“,
zurückgezogene Arbeiten. Die betroffene Quelle als **offen** im Nachweis führen und mit den anderen weitermachen.

## Parlamentsmaterial über `dip`

Die DIP-API des Bundestags liefert Vorgänge, Vorgangspositionen, Drucksachen, Plenarprotokolle, Aktivitäten und Personen, nur
lesend. Eine Karte zu einer Rede zitiert das Dokument mit Seite, nicht den Metadatensatz.

```bash
$A dip-person --name "Nachname Vorname" --von 2026-10-01 --bis 2026-10-31 --art Rede --lauf "$LAUF"   # oder --person-id N
$A dip-dokument plenarprotokoll 21/34 --lauf "$LAUF" --out "$LAUF/text/21-34.txt"                     # oder drucksache, DIP-ID
$A suche dip 'drucksache?f.wahlperiode=21&f.datum.start=2026-10-01' --lauf "$LAUF"
```

- Ein Name muss genau eine Person treffen. Das Nachschlagen steht in `LAUF/nachschlagen`, bei Namensgleichen `--person-id`.
- `--art` filtert das Skript, weil der Dienst es nicht kann; das Protokoll nennt `art_hier_gefiltert` und `n_gesichtet`, und
  `n_gesamt` zählt alle Aktivitäten vor diesem Filter. Der Wert gilt genau: `Rede` trifft `Rede (zu Protokoll gegeben)` nicht.
- Die Seite steht nur an der Aktivität. Der Text aus `dip-dokument` hat keine Seitenzahlen und kann auch Wochen später noch
  vorläufig sein, ganze Reden fehlen dann (`vorlaeufig` im Protokoll, Hinweis in der Datei). Maßgeblich ist das PDF: Seite und
  Wortlaut dort prüfen. Fehlt der Hinweis, ist der Text damit nicht als vollständig belegt.
- Namentliche Abstimmungen: `vorgangsposition` nennt in `beschluss` Art und Gesamtergebnis, kein Votum je Person.
- `dip` liefert Fundstellen. Es urteilt nicht über Personen und nicht darüber, ob eine Aussage stimmt.
- **Nutzungsbedingungen (Stand 27.2.2023):** Weitergabe nur mit der Quelle „Deutscher Bundestag/Bundesrat – DIP“, bei zitierten
  Dokumenten zusätzlich BT-PlPr. oder BT-Drs. und Nummer; Veränderungen kenntlich machen, PDFs nicht verändern. Untersagt ist jede
  Nutzung in sinnentstellendem Zusammenhang oder die zur Herabwürdigung von Personen führen könnte: Das vor jeder Veröffentlichung
  prüfen und im Zweifel die Person fragen. Lizenz der Karte: `$K lizenz sources/<slug> --name "DIP-Nutzungsbedingungen" --url "https://dip.bundestag.de/%C3%BCber-dip/nutzungsbedingungen" --via originaldokument`.
- Höchstens 25 Anfragen gleichzeitig; weitere Grenzen nennt der Bundestag nicht. Der öffentliche Key auf der Hilfeseite ist befristet und kann kurzfristig wechseln, ein dauerhafter kommt
  auf formlose E-Mail an die Parlamentsdokumentation. Änderungen erscheinen in der API mit rund 15 Minuten Versatz, und `aktualisiert` belegt keine Änderung am Inhalt.
- Reden enthalten politische Meinungen namentlich genannter Personen: personenbezogene Daten, möglicherweise besondere Kategorien
  nach Art. 9 DSGVO. Solche Texte gehen nur an Modelle und Anbieter, die dafür zugelassen sind.

## Nachweis (fester Abschluss, sichtbar im Chat)

```
### Nachweis /recherche <lauf>
Frage: … (bei reiner Aufnahme: entfällt)
Suchprotokoll: <Tabelle aus zusammenfuehren: Weg | Datenbank | Suchstring | Zeitpunkt | gesamt | geliefert>
Gegenprobe: PubMed n_gesamt api = …, tooluniverse = … | ToolUniverse nicht eingerichtet
Kandidaten: N (über ≥2 Datenbanken: …; nur Websuche: …, verworfen: …; Modellvorschläge: …)
Gewählt: Nr … (Kriterium) · verworfen: Nr … (Grund)
Webzugriff: keiner | offen: <Domain> (Freigabe fehlt)
Je Karte: sources/<slug>.md · doi:… · Rückzug: keiner | Korrektur: … · Fundweg: suche · api:pubmed · '<String>' · <at> · Lizenz: <name> → KI-Auswertung: erlaubt | mit Auflage | erst fragen
Lint: 0 Fehler, <n> Warnungen
Offen für die Person: …
Protokoll: <archivierter Pfad>
```

## Werkzeug `scripts/api.py`

Nur Standardbibliothek, nur HTTPS (Port 443) zu den Hosts der offiziellen APIs (auch bei Weiterleitungen; Schlüsselköpfe gehen
nie an einen anderen Host). Antworten über 64 MB sind ein Fehler. `--by` ist bei `suche` und `aufloesen` ohne Angabe `RECHERCHE_BY`, sonst `claude-code/recherche`; `eintrag` verlangt es.
`suche`, `eintrag`, `zusammenfuehren`, `aufloesen`, `fundweg`, `retraktion`, `abstract`, `volltext`, `anker`, `pruefen`,
`archivieren`, `dip-person`, `dip-dokument`, `dienste`; Hilfe mit `api.py <befehl> --help`. Dienste für `suche`: `pubmed`, `europepmc`, `crossref`,
`openalex`, `semanticscholar`, `arxiv`, `cellar` (EU-Recht, SPARQL) und `dip` (Bundestag). Protokollzeile je Abruf:
`{v, lauf, weg, db, werkzeug, query, parameter, at, by, n_gesamt, n_geliefert, url, fehler, treffer: [{rang, doi, pmid, pmcid, arxiv, celex, dip, titel, jahr, quelle}]}`,
je Dienst kommen Felder dazu (`epmc`, `oa`, `typ`, `openalex`, `s2`, `url`; bei `dip` `datum`, `fundstelle`, `xml`, `dokument`, `top`, `person_id`, `wahlperiode`, `vorgaenge`, `beschluss`).
In einer Bash-Sandbox je Aufruf `allowed_domains` mit dem Host des Dienstes angeben.
