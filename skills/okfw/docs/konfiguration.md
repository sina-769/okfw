# okfw: Aufbau und Konfiguration

## Aufbau des Skills

```text
~/.claude/skills/okfw/
├── SKILL.md                  Arbeitsweise für Claude (Recall → Capture → Ingest → Retro → Lint)
├── okfw.py                   Kern: PEP 723 (Python ≥ 3.11, PyYAML und Markdown über uv), CLI und Bibliothek
├── okfw_kern/                 Code des Kerns nach Aufgaben: basis, frontmatter, bereinigung, rechte, konfig, quellen, wiki*, cli
├── rollen.py                 Modellrollen (Harness, Anbieter, Modell je Arbeit), nur Standardbibliothek, siehe „Modellrollen“
├── anbieter.toml             Anbieter und Rollen ab Werk (unterste Schicht der Modellrollen)
├── okfw_serve.py             lokaler Server hinter `okfw.py serve` (Bearbeiten im Dashboard), nur Standardbibliothek
├── okfw_auftraege.py, okfw_quelle.py   Aufträge im Hintergrund von `serve`; Quellen ablegen, abgeschottet auslesen, Anker prüfen
├── hooks/okf_hook.py         Guard und Ereignisprotokoll für Claude Code, nur Standardbibliothek
├── hooks/settings.example.json   Hook-Block für .claude/settings.json eines Projekts
├── profiles/default.toml     Grundprofil für neue Wikis (okfw.py init)
├── profiles/dokumentation.toml   Profil für Wikis über Projektdoku und Code (init --profile dokumentation)
├── profiles/…                weitere fertige wiki.toml je Installation (nicht im Plugin)
├── templates/dashboard.html  Vorlage des HTML-Dashboards (okfw.py dashboard)
├── templates/serve.js, auftraege.js, auftrag_*.js, serve.css, auftraege.css   Bearbeiten im Dashboard; nur `serve` fügt sie ein, nie die Dashboard-Datei
├── templates/fonts/          Bricolage Grotesque, Instrument Sans, Newsreader, JetBrains Mono (SIL OFL 1.1, Lizenztexte dabei), werden eingebettet
├── templates/apa.csl         Zitierstil APA 7 (Zotero, CC BY-SA 3.0), Standard für Literaturangaben
├── docs/konfiguration.md     diese Datei
├── local.example.md          Vorlage für local.md
└── local.md                  optional: Ergänzung je Installation (Projekte, Begleit-Skills), nicht im Plugin
```

`profiles/dokumentation.toml` ist für Wikis über Projektdoku und Code gedacht: Quellen mit den Unterarten
Dokumentation, Code, Spezifikation und Datensatz (`format`), Lizenz und Rohquellen-Paket als Pflicht,
fehlender Fundweg als Warnung.

Ohne `local.md` gilt allein `SKILL.md`; `local.example.md` zeigt die Abschnitte, die eine Installation
dort festlegen kann (Webzugriff, externe Dienste, Begleit-Skills, Altbestand).

Aufteilung: Der **Kern** kennt OKF v0.2, das gemeinsame pi-llm-wiki-Profil und alles, was in mehr
als einem Wiki gebraucht wird (auch Lizenzsuche und Metadatenabruf über offizielle APIs). Alles, was
sich zwischen Projekten unterscheidet, steht in der **`wiki.toml`** des Projekts. Nur projekteigene
Register und Korpora bleiben **im Projekt** (etwa ein Prüfskript für Zitate oder ein Aussagenregister,
beschrieben in der `CLAUDE.md` des Projekts).

Aufs Netz greift der Kern nur in Befehlen zu, die dafür da sind (`new source --doi/--pmid/--arxiv`:
Crossref, sonst DataCite, PubMed, arXiv, dazu bei DOI und PMID die Rückzugsprüfung bei Crossref und PubMed; `lizenz --fetch`: Crossref oder DataCite, Europe PMC,
OpenAlex), nie bei `lint`, `rebuild`, `status` oder `dashboard`. `serve` geht nur auf Klick ins Netz: über `rollen.py` (Modellliste, Vorschlag) und beim Aufnehmen einer DOI oder Kennung über dieselben Metadatendienste wie `new source` und `lizenz --fetch`, für freien Text auch über Europe PMC und die Skripte von `recherche`. Ohne `wiki.toml` schreibt er
nichts; `status --hook`, `rebuild --hook` und `rebuild --quiet` beenden sich dann still, damit Hooks in
fremden Ordnern nicht stören. Andere Bedienfehler (kaputte `wiki.toml`, ein bekannter Abschnitt, der keine Tabelle ist, `requires_core` zu neu)
melden `status --hook` und `rebuild --hook` als eine Zeile (`systemMessage`, Exit 0).

## Aufruf

```bash
uv run --no-config --quiet ~/.claude/skills/okfw/okfw.py <befehl> …
```

Die `wiki.toml` wird aufwärts ab dem aktuellen Ordner gesucht. `--root DIR` nimmt `DIR/wiki.toml`,
`--config PFAD` eine Datei an anderem Ort. Projektordner ist immer der Ordner der `wiki.toml`; `--root` und `--config` auf
verschiedene Ordner sind ein Bedienfehler (Exit 2). `init` legt die Datei unter `--root` bzw. im aktuellen Ordner an,
`init --config` ist ein Bedienfehler mit Hinweis auf `--root`.

Exit 0 ist ok, 1 steht für `lint` mit Fehlern (mit `--strict` auch Warnungen) oder `rebuild --check` mit veralteten
Projektionen, 2 für einen Bedienfehler (`status --hook` und `rebuild --hook` enden dabei mit 0), 3 für `new source`, wenn die
Rückzugsprüfung einen Rückzug oder Bedenken meldet (dann entsteht keine Karte, siehe [Rückzugsprüfung](#rückzugsprüfung)).

| Befehl | Zweck |
|---|---|
| `status [--hook]` | Seiten je Typ, draft, veraltet (`stale_after` erreicht), offene `[!todo]` (ohne Code), Vertrauensstufen, gültige und verfallene Freigaben (davon „stable, seit Änderung ungeprüft“) und Seiten „zur Freigabe“, Seiten mit Gegenprüfung ohne Prüfwert, Rohquellen ohne Karte, Umfang, bei `[visibility] field` die Zahl verborgener Seiten, Fundweg, Zahl der Ereignisse und Zeitpunkt des letzten; `--hook` gibt SessionStart-JSON aus |
| `lint [--strict] [--json] [--summary]` (`--streng` wie `--strict`) | OKF-Konformität und Profilregeln; Exit 1 bei Fehlern (mit `--strict` auch bei Warnungen) |
| `rebuild [--check] [--quiet \| --hook [--befunde]]` (Alias `index`) | `index.md` je Ordner, `log.md`, Exporte; `--check` schreibt nichts und endet mit Exit 1, wenn etwas veraltet ist; `--hook` erzeugt still neu und meldet Lint-Fehler als eine Zeile (`systemMessage`), ohne zu blockieren; `--befunde` gibt sie zusätzlich je Seite im JSON aus, für `okf_hook.py stop`, das daraus die Rückgabe an die Sitzung baut |
| `new <typ> <slug> --title … [--description] [--sources a,b] [--tags] [--access] [--resource] [--csl DATEI\|-] [--set feld=wert] [--fundweg ART --db --query --fundweg-von SLUG --at ZEIT --by AKTEUR]` | Seite aus Vorlage, `generated` gesetzt, `stale_after` nur bei Typen mit `stale_months`, Quellen mit `license: unknown` (mit `--set raw_packet=…` die Lizenz aus dessen `manifest.json`, `via: capture`) und auf Wunsch `fundweg` (`--at`/`--by` für den echten Suchzeitpunkt und -akteur, sonst jetzt und `[wiki] actor`; beide nur mit `--fundweg`), Ereignis `create`. Deutsche Optionen `--titel`, `--beschreibung`, `--quellen` |
| `new source <slug>` bzw. `new-source <slug>` mit `--doi` · `--pmid` · `--arxiv` · `--manuell --titel … --url … [--autor] [--jahr] [--csl-typ]` · `--aus-lauf LAUF --kandidat N`, dazu `[--volltext PFAD --von URL] [--evidenz E] [--trotz-rueckzug]` | Quellenkarte mit Metadaten aus Crossref (DOIs, die Crossref nicht kennt: DataCite), PubMed oder arXiv (CSL, Kurzbeleg, APA-Block); setzt `[sources] checked_field` (`{by, at}` mit dem Dienst, der geantwortet hat: `process:crossref`, `process:datacite`, `process:pubmed` oder `process:arxiv`) und bei jedem `updated-by`-Hinweis von Crossref (Rückzug, Bedenken, Korrektur) `[sources] updates_field`; `access` ohne Angabe `citation`, bei `--manuell` `originaldokument`, wenn das Profil ihn kennt. Bei `--doi` und `--pmid` prüft der Kern vor dem Schreiben den Rückzugsstatus (Exit 3 und keine Karte bei Rückzug oder Bedenken; `--trotz-rueckzug` legt sie mit Vermerk an) und schreibt `[sources] retraction_field` (siehe [Rückzugsprüfung](#rückzugsprüfung)). `--aus-lauf LAUF --kandidat N` (Nr, DOI oder PMID) liest `LAUF/kandidaten.json` der Recherche (`api.py zusammenfuehren`): Kennung des Kandidaten statt `--doi`/`--pmid`/`--arxiv`, dazu je Fund ein Fundweg mit echtem Zeitpunkt, `db`, `query` und `by` (ein Fund als Zuordnung, mehrere als Liste); ein reiner Websuche-Treffer ohne Auflösung über Weg 1 oder 2 wird abgelehnt. Wird zugleich eine eigene Kennung angegeben, vergleicht der Kern sie mit der des Kandidaten: eine andere Arbeit ist ein Bedienfehler (Exit 2, nichts übernommen), ohne gemeinsame Kennung steht auf stderr „Zuordnung nicht geprüft“ |
| `fundweg <seite> --art … [--db] [--query] [--von] [--at] [--by] [--add]` · `fundweg <seite> --aus-lauf LAUF --kandidat N [--add]` | Fundweg einer Quelle setzen; `--add` hängt einen weiteren an; `--at`/`--by` für Nachträge mit dem echten Zeitpunkt; zieht `generated` nach. `--aus-lauf` setzt statt `--art` alle Funde des Kandidaten aus `LAUF/kandidaten.json` (Zeitpunkt, `db`, `query`, `by` aus dem Lauf) in einem Schreibvorgang: Art `suche`; ein Fund, dessen `weg` selbst eine Fundweg-Art ist (`modellvorschlag`, `person`, `material`, `zitatverfolgung`), behält sie, und `zitatverfolgung` braucht `von`. Vorher vergleicht der Kern die Kennungen des Kandidaten mit denen der Karte: eine andere Arbeit ist ein Bedienfehler (Exit 2, nichts übernommen), ohne gemeinsame Kennung steht auf stderr „Zuordnung nicht geprüft“. `--add` hängt einen Fundweg, der schon dasteht, nicht noch einmal an. Auf einer Seite, die keine Quellenkarte ist, Bedienfehler (Exit 2) wie `new --doi` |
| `abbildung <name> --file x.svg [--ersetzen] [--rolle R [--modell M]]` | SVG-Schaubild prüfen, bereinigen und als `<bundle>/abbildungen/<name>.svg` ablegen, Ereignis `create` (bei `--ersetzen` `update`), mit `--rolle` nennt es das Modell; mehr als 200 KB, nicht UTF-8, `<!ENTITY` oder ein `<!DOCTYPE` außer dem Standardkopf, ungültiges XML, weder `viewBox` noch `width` und `height` als Zahlen mit absoluter Einheit, ein Symlink als Ziel oder ein Ordner `abbildungen/` außerhalb des Bundles → Exit 2; Entferntes steht auf stderr, im Dashboard fragt der Dialog vorher (siehe [Schaubilder](#schaubilder)) |
| `capture <slug> --url … --file PFAD[=REPO-PFAD] … [--source-path] [--commit] [--license … --license-url …] [--note]` | Rohquellen-Paket mit Manifest (SHA-256, 0444); Symlinks werden abgelehnt. `--file` mehrfach, je Datei mit ihrem Pfad im Quell-Repository (`--file /tmp/x/hooks.json=hooks/hooks.json`); `--source-path` gilt für Dateien ohne eigenen. `--commit` warnt, wenn es kein voller SHA ist. Prüft vorher „Volltext lokal ablegen“ für die Lizenz aus `--license`/`--license-url`: nicht erlaubt → Abbruch, erst fragen oder Auflage → Warnung (ohne `--license` gilt die Lizenz als unbekannt) |
| `hash <pfad> [--from URL]` (Alias `sha`) | `fulltext`-Block für Karten ohne Rohquellen-Paket |
| `verify <seite> [<seite> …] --by human:<id> [--status STATUS] [--note] [--force]` | Prüfvermerk nach OKF §5.2 mit Prüfwert des Seitenkörpers (`body_sha256`, siehe [Freigabe](#freigabe-über-claude)) für eine oder mehrere Seiten (eine Charge), `--note` für alle, ein Ereignis je Seite; ändert nur `verified` und `status`, alles andere bleibt byte-genau. `--status` nimmt `draft` und `deprecated` mit jedem Akteur, `stable` nur mit einem `human:`-Akteur und nur, wenn jede Seite eine Gegenprüfung durch Claude oder einen Prozess (Akteur ohne `human:`) mit Prüfwert zum jetzigen Inhalt hat; sonst Abbruch ohne Änderung, `--force` gibt trotzdem frei und vermerkt das im Ereignis |
| `event <ereignisart> <seite> [detail] [--by]` | Ereignis anhängen |
| `deprecate <seite> --by AKTEUR [--nachfolger SEITE] [--note N]` | Erste Stufe, siehe [Karten ablösen und löschen](#karten-ablösen-und-löschen): `status: deprecated`, mit `--nachfolger` dazu `superseded_by`, Ereignis `deprecate`. Die Karte bleibt |
| `delete <seite> [--yes --by human:<id>] [--belege-brechen] [--mit-rohquelle] [--json]` | Zweite Stufe: Ohne `--yes` nur die Folgen zeigen; mit `--yes` die abgelöste Karte in den Papierkorb, Ereignis `delete`, Indexe neu |
| `restore [<eintrag> --by AKTEUR] [--json]` | Ohne Eintrag den Papierkorb zeigen; mit Eintrag zurückholen, Ereignis `restore`, Indexe neu |
| `body <seite> <datei\|-> [--detail TEXT] [--rolle R [--modell M]]` · `body <seite> --nachziehen [--neu] [--detail TEXT]` | Körper ersetzen; im Frontmatter ändert sich nur `generated {by, at}`, auf einer stable-Seite ohne gültige Freigabe (auch ohne jede Freigabe) dazu `status: draft` (`[trust] edit_resets_stable`; `verified` bleibt stehen, das Ereignis nennt die verfallene Freigabe). `--detail` ist der Text des Ereignisses. Gleicher Inhalt (gleicher Prüfwert) ändert nichts. `--nachziehen`: der Körper ist schon geändert (Edit oder Write, ruft der PostToolUse-Hook auf), nur `generated` und `status` wie eben; `--neu` (Write einer neuen Seite) schreibt das Ereignis `create` statt `update` |
| `sources <seite> [--add a,b] [--remove c]` (Alias `quellen`) | `sources[]` chirurgisch ergänzen oder kürzen, zieht `generated` nach und gibt die Fußnotenzeilen für den Text aus. `--add ID=SLUG` legt eine weitere Fußnote auf dieselbe Karte an (eigene Fundstelle). `--remove` nur, wenn der Text `[^id]` nicht mehr zitiert |
| `init [--profile NAME]` | `wiki.toml` aus `profiles/NAME.toml`, Ordner, erstes Ereignis |
| `gitattributes` | `merge=union` für Ereignisstrom und Projektionen in `.gitattributes` eines bestehenden Wikis eintragen; wiederholbar |
| `rechte [<seite>] [--aktion A] [--json]` (Alias `rights`) | Lizenzen und Nutzungsrechte: Übersicht je Lizenzklasse, eine Quelle im Detail oder alle Quellen nach einem Recht gruppiert (`--aktion abbildung`) |
| `lizenz <seite> --name … [--url] [--class] [--via]` · `lizenz <seite> --fetch [--force]` · `lizenz --missing --fetch [--dry-run]` (Alias `license`) | Lizenz setzen oder suchen (DOI, PMID, PMCID): Crossref bzw. DataCite, Europe PMC, OpenAlex (je Fundort, Verlagsfassung zuerst; `via` nennt Dienst und Fassung); schreibt `license {name, url, checked, via}` chirurgisch, zieht `generated` nach und protokolliert; `--fetch` und `--missing --fetch` überschreiben nur Lizenzen ohne Namen oder mit `unknown` (und Gleichwertigem), eine benannte Lizenz, auch eine nicht eingeordnete, nur mit `--force` (`--missing` nennt am Ende, wie viele es übersprungen hat); `--dry-run` gilt für jede Form und zeigt, was geschrieben würde |
| `dashboard [--out PFAD] [--oeffentlich] [--open]` | eine HTML-Datei mit Netz, Kennzahlen, Struktur, Pfaden, Glossar, offenen Stellen, „Zur Freigabe“ und Tabelle; `--oeffentlich` (Alias `--public`) ohne verborgene Seiten aus `[visibility]` |
| `pfad neu <titel>` · `pfad pruefen <pfad> [--json]` · `pfad uebernehmen [--dry-run] [--json]` · `pfad export <pfad> [--out PFAD] [--oeffentlich] [--notizen] [--ohne-interne]` | Pfade anlegen, vor dem Vortrag prüfen, den Standardpfad in eine Datei übernehmen, als eine HTML-Datei mit nur seinen Karten ausgeben (siehe [Pfade](#pfade)) |
| `serve [--port N] [--no-open]` | dasselbe Dashboard als lokale Seite mit Bearbeiten: Karten, Eigenschaften, Freigabe, neue Karten, Schaubilder, Modellvorschlag, Einstellungen und Design ([Bearbeiten im Dashboard](#bearbeiten-im-dashboard)); nur aus einem Projektordner mit `wiki.toml`, nur auf diesem Rechner. Ohne `--port` wählt das System einen freien; `--no-open` gibt nur die Adresse aus |

## wiki.toml

Alle Pfade relativ zum Projektordner, dem Ordner der `wiki.toml` (so liest sie auch der Guard). Pfade, in die der Kern schreibt (`[wiki] bundle`, `[paths] events` und `raw`, `[dashboard] path` und `public_path`, `[export.*] path`), müssen aufgelöst, auch über Symlinks, im Projektordner liegen, sonst ist es ein Bedienfehler (Exit 2): Ein geklontes Wiki soll keine Datei außerhalb überschreiben. `dashboard --out` bleibt frei. Nicht gesetzte Werte nehmen den Standard in Klammern. Schlüssel, die hier
nicht stehen, wirken nicht; Lint meldet sie als `config_unknown` (Warnung), ebenso unbekannte Codes in
`[lint.severity]`. Frei wählbar sind nur die Schlüssel unter `[enums]`, `[labels.*]`, `[rights.*]`, `[log] labels`,
`[licenses.patterns]`, `[types.<typ>] defaults` und `todo`, die Tabellen je Rolle unter `[rollen]` (prüft `rollen.py`) sowie die Namen der Typen selbst.

```toml
[wiki]
title = "…"                     # Überschrift des Root-Index ("Wiki")
bundle = "wiki"                 # OKF-Bundle-Wurzel ("wiki")
actor = "claude-code/unknown"   # generated.by ("claude-code/unknown"); WIKI_ACTOR geht vor, nach Write und Edit in Claude Code ersetzt der Hook einen claude-code/…- oder human:…-Akteur durch das Modell aus dem Transkript
okf_version = "0.2"
requires_core = "0.1"           # Version als MAJOR.MINOR ("0.3", ein Patch-Teil zählt nicht), anderes ist ein Bedienfehler; Kern verweigert die Arbeit, wenn er älter ist

[paths]
events = "wiki/.events.jsonl"   # Ereignisprotokoll ("<bundle>/.events.jsonl")
raw = "raw"                     # Rohquellen-Pakete; leer = keine Pakete, nur fulltext-Blöcke
protected = ["quellen/volltexte/**"]   # weitere Muster für den Guard-Hook: fnmatch auf den Pfad relativ zum Projektordner, * passt auch über /, ** hat keine eigene Bedeutung

[index]
root = "overview"               # overview: „Zuerst lesen“ + Bereiche mit Anzahl | full: jede Seite im Root-Index
sort = "title"                  # title | slug
entry_tag = "einstieg"          # Seiten mit diesem Tag kommen nach „Zuerst lesen“, sortiert nach order
start_heading = "Zuerst lesen"  # leer = Einstiegsseiten direkt unter dem Titel
areas_heading = "Bereiche"
unit = "Seiten"                 # „54 Seiten“ / „54 Karten“
badges = ["access", "license"]  # Felder als `Badge` hinter dem Link; license zeigt den Lizenznamen
star_field = "core"             # ★ hinter dem Titel, wenn das Feld wahr ist
banner = "> Generiert – nicht von Hand bearbeiten."   # Zeile unter jeder Index-Überschrift

[log]
title = "Aktivitätslog"
banner = ""
link_text = "title"             # title | id (bei id trägt das create-Ereignis den Titel als detail)
show_actor = false              # „(claude-code/…)“ anhängen
labels = {create = "Creation"}   # Beschriftung je Ereignisart, Standard: deutsche Wörter

[enums]                         # Feld = erlaubte Werte; gilt auf allen Seiten, die das Feld tragen
access = ["fulltext", "abstract", "citation"]

[lint]
orphans = "inbound"             # inbound | isolated
fulltext_access = ["fulltext"]  # access-Werte, die raw_packet oder fulltext verlangen
claims_section = "Kernaussagen" # Abschnitt, in dem bei access: citation nur Hinweiskästen (> [!todo] …) und Fußnotendefinitionen stehen dürfen; jede andere Zeile meldet citation_with_claims
priority_field = ""             # z. B. "core": warnt, wenn solche Quellen nur citation haben

[lint.severity]                 # Code = "error" | "warn" | "info" | "off"
path_not_kebab = "error"        # Beispiel, Standard warn

[export.csl]
path = "quellen/references.json"   # CSL-JSON aller Quellenkarten (id = Slug): der csl-Block, sonst abgeleitet wie im Dashboard
field = "csl"                   # Frontmatter-Feld mit dem CSL-Eintrag
style = "quellen/apa.csl"       # Zitierstil für pandoc (APA-Block in neuen Karten, Literaturverzeichnis)
lang = "de-DE"

[export.bibliography]
path = "export/literaturverzeichnis.md"   # Markdown über pandoc --citeproc
title = "Literaturverzeichnis"

[scope]                         # Umfang: eine Ebene, auf die das Dashboard einengt (ohne field: aus)
field = "umfang"                # Frontmatter-Feld auf allen Seitentypen; Werte vom Kern nach außen in [enums]
label = "Umfang"
default = "kern"                # Voreinstellung im Dashboard ("" = alle); die Wahl merkt sich der Browser
derive = true                   # Quellen ohne Wert erben den engsten Umfang der Seiten, die sie zitieren

[visibility]                    # Sichtbarkeit: was in der öffentlichen Ausgabe ganz fehlt (ohne field: aus)
field = "sichtbarkeit"          # Frontmatter-Feld; erlaubte Werte in [enums], sonst default und hidden
label = "Sichtbarkeit"
default = "oeffentlich"         # Wert für Seiten ohne Feld
hidden = ["intern"]             # diese Werte fehlen in dashboard --oeffentlich
derive = true                   # Quellen ohne Wert, die nur verborgene Seiten nutzen, fehlen dort ebenfalls

[fundweg]
field = "fundweg"               # Feld für den Fundweg der Quellen

[sources]                       # new source --doi/--pmid/--arxiv/--manuell
checked_field = "metadaten_geprueft"   # Metadatenabgleich {by: process:<dienst>, at} – nicht verified
updates_field = "crossref_hinweise"    # Crossref updated-by, jeder Typ (Rückzug, Bedenken, Korrektur); im Text als ⚠-Zeile unter dem APA-Block, in eigener Vorlage über {{hinweise}}
retraction_field = "rueckzug"   # Ergebnis der Rückzugsprüfung {geprueft, via, ergebnis, hinweise, trotz} (siehe „Rückzugsprüfung“)
manual_csl_type = "document"    # CSL-Typ bei --manuell ohne --csl-typ (z. B. legislation für Gesetze)

[trust]                         # Freigabe (siehe „Freigabe über Claude“)
edit_resets_stable = true       # body auf einer stable-Seite setzt status: draft
signed_human = false            # true: Lint human_unsigned prüft die Commit-Signatur jeder human:-Freigabe (nur mit Git)
human = "human:jdoe"            # Kennung, die „Zur Freigabe“ im Dashboard vorbelegt (sonst die häufigste im Wiki)
check_command = "/lesen --pruefen" # Aufruf der Gegenprüfung für „Gegenprüfung ohne Prüfwert“ (sonst ein Hinweis an Claude); das Dashboard übernimmt nur Slash-Befehle (`/befehl [--option] …`), jeder andere Wert wird ignoriert, damit eine fremde wiki.toml keine Shell-Zeile zum Kopieren unterlegt

[licenses.patterns]             # weitere Muster (regulärer Ausdruck, gesucht in Name und URL in Kleinbuchstaben) = Lizenzklasse
"example\\.org" = "vorbehalten"

[rights.vorbehalten]            # Rechtematrix je Klasse überschreiben: "ja", "bedingt: …", "nein: …", "fragen: …"
ki_auswertung = "fragen: Verlag erlaubt kein Text-und-Data-Mining"

[dashboard]
path = "build/wiki-dashboard.html"   # Ziel von okfw.py dashboard; der Guard schützt die Datei
auto = false                    # true: rebuild (also auch der Stop-Hook) schreibt das Dashboard mit (nie öffentlich)
public_path = ""                # Ziel von dashboard --oeffentlich ("": <path> mit Zusatz -oeffentlich vor der Endung, also build/wiki-dashboard-oeffentlich.html)
glossary = ["concept", "entity"]  # Typen, deren Seiten ins Glossar des Dashboards kommen ([] = kein Glossar)
design = "anders.css"           # CSS-Datei, relativ zum Projektordner, nach der Vorlage eingebettet (Standard: design.css, falls vorhanden; siehe „Design“)

[rollen]                        # Modellrollen (rollen.py): Datenklasse des Wikis und gesperrte Anbieter
daten = "intern"                # oeffentlich | intern | personenbezogen (Standard intern); sonst Lint rollen_unknown
sperre = ["openai"]             # Anbieter, an die dieses Wiki nichts schickt; je Rolle eine eigene Tabelle [rollen.lesen] (siehe „Modellrollen“)

# Je Typ ein Block. Reihenfolge = Reihenfolge der Bereiche im Root-Index.
[types.source]
folder = "sources"              # (Typname)
label = "Quellen"
description = "Je eine Karte pro Quelle"
required = ["title", "description", "resource", "access", "status", "generated"]   # Fehler
recommended = ["license"]       # Warnung ([title, description])
sections = ["Kurzreferat", "Kernaussagen", "Relevanz", "Verbindungen", "Zugang"]  # H2, Präfixvergleich
min_links = 0                   # ausgehende interne Links im Text (2, bei source 0)
needs_footnotes = false         # Seite braucht mindestens eine Fußnote [^id] (nicht bei status: deprecated)
stale_months = 0                # new setzt stale_after = heute + N Monate
template = ""                   # eigene Vorlage, gesucht neben der wiki.toml, dann im Skill unter profiles/ und templates/;
                                # Platzhalter {{title}}, {{description}}, {{apa}}, {{kurzbeleg}}, {{access}}, {{footnotes}},
                                # {{todo_kurzreferat}}, {{todo_kernaussagen}}, {{hinweise}} und {{fm.<feld>}}
defaults = {evidenz = "peer-reviewed"}   # Werte für neue Seiten, wenn nichts angegeben ist

[types.source.todo.abstract]    # Platzhaltertexte je access für Kurzreferat und Kernaussagen überschreiben
kernaussagen = "> [!todo] …"
```

## Gemeinsames Frontmatter-Profil

Fest im Kern (OKF v0.2): `type` Pflicht; `status` ∈ draft | stable | deprecated; `generated {by, at}`
und `verified [{by, at, body_sha256}]` mit Akteurskonvention `<producer>/<version>`, `human:<id>`, `process:<id>`
und ISO-8601-Zeitpunkt mit Offset; `stale_after` als Zeitpunkt; `sources [{id, resource}]`;
Fußnotenlabel = `sources[].id`. `body_sha256` in `verified` ist eine Erweiterung von okfw (OKF §5.2 kennt nur
`by` und `at`; zusätzliche Schlüssel erlaubt §4.1): SHA-256 des Markdown-Körpers ohne Frontmatter, Zeilenenden als
LF, Leerzeilen an Anfang und Ende entfernt. Andere OKF-Werkzeuge lesen den Eintrag weiter als `{by, at}`. Zeitstempel bleiben beim Lesen Strings (kein Umbau zu `datetime`),
YAML-Anker und -Aliase sind verboten.

`generated.modelle` (Erweiterung von okfw, §4.1 wie oben) hält fest, welche Modelle am Körper beteiligt waren, als Liste von
`{harness, anbieter, modell, rolle, art, by, at, body_sha256}`. `by` ist der Akteur, der den Text eingebracht hat, bei einem
übernommenen Vorschlag also der Mensch; `body_sha256` der Körper zu diesem Zeitpunkt, so ist erkennbar, ob der Text seither
geändert wurde. `art` ist eine von:

| `art` | Bedeutung | Wer schreibt ihn |
|---|---|---|
| `text` | Ein Akteur `<producer>/<version>` hat den Körper geschrieben | `body`, Hook nach Write und Edit |
| `entwurf` | Der Text beruht auf dem Entwurf einer Rolle | `body --rolle <rolle> [--modell M]` |
| `vorschlag` | Vorschlag im Dashboard unverändert übernommen, auch von Hand hineinkopiert | `serve` |
| `vorschlag-bearbeitet` | Vorschlag überarbeitet übernommen; `modell: unknown`, wenn die Datei des Vorschlags nicht mehr da ist | `serve` |
| `akteur` | Ein früherer `generated.by` mit Modell wurde überschrieben; ein Modellanteil ist möglich, aber nicht sicher, denn der Akteur kann auch nur Metadaten geändert haben. `…/unknown` entfällt, wenn derselbe Harness danach sein Modell nennt | jeder Befehl, der `generated` nachzieht |

Die Liste wird nur länger: dasselbe Modell in derselben Rolle bleibt ein Eintrag mit dem neuesten Stand, ein bekanntes Modell
ersetzt `unknown` bei gleichem Harness, Anbieter und gleicher Rolle, Freigabe, Metadaten und
späteres Bearbeiten lassen sie stehen. `human:<id>` und `process:<id>` sind kein Modell und erzeugen keinen Eintrag. Die Freigabe
bleibt davon getrennt in `verified`. Der Körper selbst bleibt unmarkiert, weil eine Markierung im Text den Prüfwert der Freigabe
ändern würde; wer nur den Körper kopiert, nimmt die Herkunft nicht mit. Das Dashboard führt die Einträge je Seite in seinen Daten
(`nodes[].modelle`, dazu `gleich`, ob der Text noch dem Prüfwert entspricht) und zeigt sie sichtbar; die öffentliche Ausgabe lässt
`by` weg. Das macht die Herkunft nachvollziehbar. Ob es eine gesetzliche Kennzeichnungspflicht erfüllt, ist nicht geprüft.

Quellenkarten (`type: source`) zusätzlich, wenn vorhanden: `access`; `raw_packet` (Pfad zum
Paket mit `manifest.json`) oder `fulltext` als Zuordnung oder Liste mit `path` und `sha256`
(empfohlen außerdem `retrieved_at`, `retrieved_from`; ältere Schlüssel `abgerufen`, `von`,
`fetched_from` bleiben gültig); `license {name, url, checked, via, class}` (Pflicht im Sinn von
`license_missing`, `unknown` ist ein gültiger Name; `class` legt die Lizenzklasse fest und geht jedem Muster vor); `rights {aktion: "wert: Hinweis"}` für Abweichungen von der Klasse, etwa eine erteilte
Genehmigung; `csl` als CSL-JSON ohne `id`; `fundweg` als Zuordnung oder Liste
`{art, db, query, von, at, by}` mit `art` ∈ `suche` | `modellvorschlag` | `material` | `person` |
`zitatverfolgung` (dann `von: <slug>` Pflicht). `db` nennt Datenbank oder Werkzeug (`pubmed`,
`crossref`, `tooluniverse:PubMed_search_articles`), `query` den exakten Suchstring. `person` heißt, die
Quelle wurde ausdrücklich genannt, etwa im Auftrag „README von pi-llm-wiki am Commit 46bd677“; `material`
heißt, sie lag in mitgegebenem Material, ohne einzeln genannt zu sein, etwa eine Datei aus einem
übergebenen Ordner oder ein Eintrag einer mitgeschickten Literaturliste. Quellen aus
Git-Repositorien tragen `pinned {repo, commit}` mit `repo` als `owner/name` und dem vollen Commit-SHA;
ohne `author` bildet die APA-Angabe daraus den Gruppenautor (der Inhaber aus `repo`, bei `format`
documentation, code, specification oder dataset). `capture --commit SHA` schreibt den Commit ins
`manifest.json` des Rohquellen-Pakets und warnt bei einem Kurz-SHA; dass er mit `pinned.commit` übereinstimmt,
prüft der Kern nicht.

### Rückzugsprüfung

`new source --doi` und `--pmid` prüfen vor dem Schreiben, ob die Arbeit zurückgezogen wurde oder Bedenken oder eine
Korrektur gemeldet sind: bei Crossref `updated-by` (Hinweise auf diese Arbeit) und `update-to` (diese DOI ist selbst
ein Hinweis auf eine andere), bei PubMed (efetch) der Publikationstyp „Retracted Publication“ und die Verweise
`RetractionIn`, `ExpressionOfConcernIn`, `ErratumIn`, `CorrectedandRepublishedIn`. Die Crossref-Antwort des
Metadatenabrufs wird wiederverwendet; zu einer DOI sucht der Kern die PMID über esearch, zu einer PMID nimmt er die DOI
aus den Metadaten. arXiv wird nicht geprüft.

- **Rückzug** (Crossref-Typ `retraction`, `withdrawal`, `removal`, `partial_retraction`; `RetractionIn`) und **Bedenken**
  (`expression_of_concern`; `ExpressionOfConcernIn`): Exit 3, keine Karte. Die Meldung nennt Art, Dienst und die Hinweis-DOI
  (bei PubMed auch die Hinweis-PMID). Mit `--trotz-rueckzug` entsteht die Karte mit Vermerk und einer ⚠-Zeile unter dem
  APA-Block.
- **Hinweis-Karte** (`update-to` von Crossref: diese DOI ist selbst die Rückzugs- oder Bedenkenmeldung zu einer anderen
  Arbeit): ebenso Exit 3. Mit `--trotz-rueckzug` entsteht die Karte mit `ergebnis: rueckzugsnotiz` bzw. `bedenkennotiz` und
  einer ⚠-Zeile, die sagt, dass die Karte selbst ein Hinweis zu der betroffenen DOI ist. Ist die Arbeit zugleich selbst
  zurückgezogen, gilt `zurueckgezogen`.
- **Korrektur** (`correction`, `erratum`, `corrigendum`; `ErratumIn`, `CorrectedandRepublishedIn`): die Karte entsteht,
  `ergebnis: korrektur`.
- Ein Netzfehler bricht nichts ab: Warnung auf stderr, `via` nennt nur die Dienste, die geantwortet haben, und haben
  weder Crossref noch PubMed geantwortet, steht `ergebnis: nicht_geprueft`.

Die Karte trägt das Feld aus `[sources] retraction_field` (Standard `rueckzug`):

```yaml
rueckzug:
  geprueft: 2026-10-31T09:30:00Z     # Zeitpunkt der Prüfung
  via: [crossref, pubmed]            # Dienste, die geantwortet haben
  ergebnis: keiner                   # keiner | korrektur | nicht_geprueft | bedenken | zurueckgezogen | rueckzugsnotiz | bedenkennotiz
  hinweise:                          # nur bei Befund: [{art, doi, pmid, quelle}]
    - {art: correction, doi: 10.1/c, quelle: crossref updated-by}
  trotz: true                        # nur mit --trotz-rueckzug und Rückzug oder Bedenken
```

Das ist ein Metadatenabgleich wie `metadaten_geprueft`, kein `verified`.

Mit `[scope]` trägt jede Seite das Umfangsfeld (z. B. `umfang: kern`). Quellen dürfen es weglassen,
dann gilt der engste Umfang der Seiten, die sie verlinken oder in `sources[]` führen. Abgeleitet
wird nur in Lint, Status und Dashboard, nichts wird geschrieben; ein Wert auf der Karte geht vor.

Mit `[visibility]` darf jede Seite das Sichtbarkeitsfeld tragen (z. B. `sichtbarkeit: intern`); ohne
Feld gilt `default`. Seiten mit einem Wert aus `hidden` fehlen in der öffentlichen Ausgabe ganz,
ebenso Seiten mit einem unbekannten Wert (ein Tippfehler soll nichts veröffentlichen). Unbekannt heißt: nicht in
`[enums]` für das Feld, ohne solchen Eintrag nicht `default` oder `hidden`. Bekannte Werte sind Pflicht: ohne
`[enums]`-Eintrag und ohne `default` meldet Lint `visibility_values_missing` (Fehler), jede Seite mit dem Feld gilt als
verborgen und `dashboard --oeffentlich` bricht mit Exit 2 ab. Quellen ohne
eigenen Wert, die nur von verborgenen Seiten verlinkt oder in `sources[]` geführt werden, gelten als
verborgen (`derive`); auch das wird nur abgeleitet, nicht geschrieben. `[scope]` bleibt davon
unberührt: Der Umfang blendet im Dashboard nur ein und aus, die Sichtbarkeit lässt Seiten weg.

Unbekannte Felder bleiben erhalten (OKF §4.1). `verify`, `body`, `sources`, `lizenz` und `fundweg` schreiben
chirurgisch: nur die betroffenen Schlüssel bzw. der Körper ändern sich, das Ergebnis wird vor dem Schreiben
geparst. Alle außer `verify` sind inhaltliche Änderungen und setzen `generated {by, at}` auf `[wiki] actor`
und jetzt; `verify` hält nur eine Prüfung fest.

## Fundstellen

Die Fundstelle steht im Fußnotentext: `[^q]: [Titel](/sources/q.md), S. 4`. Erkannt werden S., Seite,
Tab., Abb., Abschn., Kap., Art., Abs., §, Folie, Zeile(n), Z., Rn., Anhang und Erwägungsgrund; das
Dashboard setzt sie in den APA-Beleg („…, 2026, Z. 120–135“).

Eine Fußnote trägt nur eine Fundstelle. Belegt eine Seite viele Stellen derselben Quelle, etwa Zeilen
einer Code-Datei, gibt es zwei Wege:

- **Fundstelle im Satz**, eine Fußnote je Quelle: „`verify` hebt den Status nur mit `human:` (okfw.py
  Z. 763–765).[^okfw-kern]“. Einfach; der APA-Beleg im Dashboard zeigt die Zeile dann nicht.
- **Eine Fußnote je Fundstelle** auf dieselbe Karte: `sources concepts/x --add okfw-kern-z763=okfw-kern`
  legt den Eintrag an, im Text dann `…[^okfw-kern-z763]` mit
  `[^okfw-kern-z763]: [okfw.py](/sources/okfw-kern.md), Z. 763–765`. Das Literaturverzeichnis nennt
  die Quelle trotzdem nur einmal.

## Ereignisse

Das Ereignisprotokoll (`[paths] events`, Standard `<bundle>/.events.jsonl`) hat eine JSON-Zeile je Ereignis und
wird nur angehängt; `log.md` (das Log) ist seine lesbare Projektion:

```json
{"ts": "2026-10-25T09:30:00Z", "kind": "verify", "page": "concepts/local-tool", "by": "human:jdoe", "detail": ""}
```

`page` ist die Konzept-ID (Pfad im Bundle ohne `.md`), `raw/<slug>` für Rohquellen, `.` für das ganze
Bundle. Ereignisarten (`kind`): `init`, `bootstrap`, `capture`, `create`, `update`, `verify`, `deprecate`, `delete`, `restore`, `merge`, `enrich`, `export`, `note`, dazu die Hook-Formen `write` und `edit`;
der PostToolUse-Hook schreibt nach einem Edit oder Write, der den Körper geändert hat, über `body --nachziehen`
ein `update` mit dem Akteur wie unter `[wiki] actor` beschrieben (nach einem Write, der die Seite neu anlegt, mit `--neu` ein `create`), sonst
`write`/`edit` mit `by: process:claude-code-hook`; im Log werden nur `write`/`edit` zu „Erstellt“ (erstes Ereignis einer
Seite) bzw. „Aktualisiert“, `create` steht immer als „Erstellt“, `update` als „Aktualisiert“. Ältere Formen werden gelesen, nie
umgeschrieben: `creation`, fehlendes `by`, `page` als bloßer Slug (wird über den Dateinamen aufgelöst). Ist der Ereignisstrom ein
Symlink aus dem Projekt hinaus, schreibt der Kern kein Ereignis und endet mit Exit 2.

## Lint-Codes und Standardschwere

| Schwere | Codes |
|---|---|
| error | `bundle_missing`, `frontmatter_invalid`, `missing_type`, `root_index_frontmatter`, `index_frontmatter`, `log_frontmatter`, `log_date_heading`, `status_invalid`, `generated_invalid`, `verified_invalid`, `timestamp_invalid`, `source_invalid`, `source_duplicate_id`, `source_missing`, `footnote_unsourced`, `missing_required`, `enum_invalid`, `visibility_values_missing`, `raw_missing`, `raw_tampered`, `fulltext_tampered`, `citation_with_claims`, `csl_incomplete`, `fundweg_invalid`, `figure_missing`, `rollen_unknown`, `design_unsafe`, `pfad_karte_fehlt`, `pfad_sichtbarkeit_fehlt` |
| warn | `okf_version_missing`, `log_order`, `actor_format`, `order_invalid`, `modelle_invalid`, `modell_unbekannt`, `modell_herkunft_fehlt`, `stable_unverified`, `verified_outdated` (bei stable error), `human_without_check`, `human_unsigned`, `stale`, `footnote_undefined`, `missing_recommended`, `type_folder_mismatch`, `path_not_kebab`, `sections_missing`, `no_footnotes`, `broken_link`, `superseded_missing`, `link_leaves_bundle`, `few_links`, `orphan`, `projection_stale`, `fulltext_absent`, `fulltext_unanchored`, `claim_on_citation_only`, `priority_citation_only`, `source_undated`, `license_missing`, `fulltext_rights`, `fulltext_shared`, `scope_missing`, `visibility_leak`, `fundweg_incomplete`, `figure_caption`, `figure_rights`, `figure_generated`, `config_unknown`, `pfad_karte_entwurf`, `pfad_karte_veraltet`, `pfad_karte_widerspruch`, `pfad_karte_abgeloest`, `pfad_lage`, `pfad_ohne_station` |
| info | `verified_unhashed`, `check_outdated`, `source_uncited`, `type_unknown`, `contradiction_one_sided`, `projection_unavailable`, `raw_uningested`, `license_unknown`, `license_unclassified`, `fundweg_missing`, `pfad_text_ohne_beleg` |

Umfang, Sichtbarkeit und Fundweg: `scope_missing` = Seite ohne Umfangsfeld (bei Quellen nur, wenn sich nichts
ableiten lässt); ungültige Werte meldet `enum_invalid`, auch für das Sichtbarkeitsfeld.
`config_unknown` = ein Schlüssel oder Abschnitt der `wiki.toml`, den der Kern nicht kennt (etwa ein Tippfehler im
Namen), oder ein unbekannter Code in `[lint.severity]`; gemeldet an der `wiki.toml`, mit dem ähnlichsten
bekannten Namen. `visibility_values_missing` = `[visibility] field` ohne bekannte Werte (weder `[enums]` für das Feld noch `default`);
gemeldet an der `wiki.toml`.
`visibility_leak` = öffentliche Seite verlinkt eine verborgene oder führt sie in `sources[]`; in der
öffentlichen Ausgabe wird der Link zu reinem Text, Beleg, Fußnote und Literaturangabe entfallen, der
übrige Satz bleibt stehen. `fundweg_missing` = Quelle ohne Fundweg
(Standard info, im Profil auch warn; `--strict` lässt `lint` bei Warnungen mit Exit 1 enden, der Befund bleibt `warn`, bei `info` wirkt `--strict` nicht); `fundweg_incomplete` = Suche ohne
`db` oder `query`; `fundweg_invalid` = unbekannte Art, Zitatverfolgung ohne `von` oder `von` ohne
Quellenkarte; ein ungültiges `fundweg.at` meldet `timestamp_invalid`, ein ungültiges `by` `actor_format`.

Schaubilder: `figure_caption` = ein Bild `![…](/abbildungen/<name>.svg)` ohne die Zeile `*Abb.: …*` mit Fußnote direkt
darunter; `figure_missing` = die Datei fehlt oder die Bereinigung lehnt sie ab (nicht UTF-8, kein gültiges XML, `<!DOCTYPE`,
`<!ENTITY`, mehr als 200 KB, weder `viewBox` noch `width` und `height` als Zahlen, zu viele Elemente, auch nach dem Auflösen
von `<use>`); `figure_rights` = die Bildunterschrift nennt das Bild
„übernommen“, und das Recht „Abbildung oder Tabelle übernehmen“ ist für die belegte Quelle laut `rechte` nicht „erlaubt“
(mit Auflage, erst fragen oder nicht erlaubt; eine Genehmigung steht als `rights:` auf der Karte); `figure_generated` = das jüngste Ereignis der Abbildung nennt ein Modell, und die Zeile darunter enthält weder „KI“ noch „generiert“. Einzelheiten unter
[Schaubilder](#schaubilder). `rollen_unknown` = `[rollen] daten` außerhalb von `oeffentlich`, `intern`,
`personenbezogen` oder `[rollen] sperre` keine Liste von Anbieter-Kennungen (siehe [Modellrollen](#modellrollen));
`design_unsafe` = die Designdatei fehlt, obwohl `[dashboard] design` sie nennt, ist nicht UTF-8 oder liegt außerhalb des
Projektordners, oder sie enthält etwas, das Fremdes laden oder das Style-Element verlassen kann (siehe [Design](#design)).
Beide werden an der `wiki.toml` gemeldet.

Pfade: `link_leaves_bundle` = ein Link im Text zeigt aus dem Bundle hinaus, oder eine Seitendatei ist ein Symlink, der nicht
auf eine Datei im Bundle führt. Eine solche Datei ist keine Seite und wird nicht gelesen; `body`, `verify`, `sources` und die
übrigen Befehle, die eine Seite öffnen, enden bei ihr mit Exit 2, und `new` legt nichts durch einen Symlink hindurch an.

Lizenzbefunde: `license_missing` = Quellenkarte mit weder `license.name` noch `license.url`; `license_unknown` = Name
`unknown` oder gleichwertig (unbekannt, unklar, n/a, none, keine Angabe, nur Striche; Groß/klein egal); `license_unclassified` = Name ohne passendes Muster (`license.class` setzen);
`fulltext_rights` = Volltext abgelegt (Rohquellen-Paket oder `fulltext`), obwohl das Recht dazu
ungeklärt ist; `fulltext_shared` = Volltext von Git versioniert, obwohl die Lizenz keine Weitergabe
erlaubt (Git-Status über `git ls-files`). Fehlt das Feld `license` ganz und führt das Profil es als Pflicht- oder
empfohlenes Feld, meldet es nur `missing_required` (dokumentation) bzw. `missing_recommended` (default), mit dem Hinweis
auf `lizenz --fetch`; `license_missing` kommt dann nicht dazu. Zum Volltext selbst: `fulltext_unanchored` =
`access` verlangt einen Volltext (`[lint] fulltext_access`), aber weder `raw_packet` noch `fulltext`-Block, oder ein
`fulltext`-Eintrag ohne `path` oder `sha256`; `fulltext_absent` = die Datei aus `fulltext.path` liegt lokal nicht
vor (Prüfsumme ungeprüft) oder außerhalb des Projektordners (dann ebenfalls ungeprüft, ohne Abbruch: ein
bestehendes Wiki kann so einen Pfad legitime führen); `fulltext_tampered` = sie weicht vom gespeicherten `sha256` ab.
Rohquellen: `raw_missing` = das Verzeichnis aus `raw_packet` liegt außerhalb des Projektordners oder ohne
`manifest.json`; `raw_tampered` = das Manifest ist nicht lesbar (ungültiges JSON, keine Zuordnung, `files` keine
Liste) oder ein Eintrag ohne `path`, mit `path` außerhalb des Projekts oder mit einer Datei, die fehlt oder vom
`sha256` im Manifest abweicht.

Root-Index: `order_invalid` = eine Einstiegsseite trägt `order` nicht als Zahl. Ganzzahliger Text zählt trotzdem als Zahl,
alles andere steht wie eine Seite ohne `order` am Ende.

Pfade (siehe [Pfade](#pfade)): `pfad_karte_entwurf` (warn), `pfad_karte_veraltet` (warn, Freigabe verfallen),
`pfad_karte_widerspruch` (warn, offener Widerspruch in der Karte), `pfad_karte_abgeloest` (warn, `status: deprecated` oder
`superseded_by`) und `pfad_karte_fehlt` (error, die Karte gibt es nicht) melden den Stand der Karten auf einer Station.
`pfad_text_ohne_beleg` (info) = eigener Text mit Zahl und ohne Fußnote. `pfad_sichtbarkeit_fehlt` (error) = in einem Wiki mit `[visibility]` trägt ein Pfad keinen eigenen Wert.
`pfad_lage` (warn) = `lage` nennt eine Station oder ein Element, das es nicht gibt, ist ungültig, oder zwei Elemente liegen
übereinander. `pfad_ohne_station` (warn) = kein Text und keine Überschrift.

Modellherkunft: `modelle_invalid` = Eintrag in `generated.modelle` ohne `modell` oder mit unbekannter `art`; `modell_unbekannt` = das
Modell steht als `unknown` da (`WIKI_ACTOR` oder `[wiki] actor` setzen, bei `body --rolle` auch `--modell`); `modell_herkunft_fehlt`
= das Ereignisprotokoll nennt ein Modell, das in `generated.modelle` fehlt.

Vertrauen: `stable_unverified` = `status: stable` ohne `verified`-Eintrag eines Menschen (`human:<id>`);
ein Vermerk durch Claude oder einen Prozess reicht dafür nicht. `verified_outdated` = die letzte Freigabe
durch `human:<id>` gilt nicht mehr dem jetzigen Inhalt: ihr Prüfwert passt nicht mehr, oder sie hat keinen
(Eintrag aus der Zeit vor dem Prüfwert, nicht vergleichbar) und `generated.at` ist jünger als ihr `at`, oder eine spätere
Prüfung mit Prüfwert galt einem anderen Inhalt.
Warnung, bei `status: stable` Fehler (eigene Schwere in `[lint.severity]` gilt für beides). Mit Prüfwert
entscheidet nur er: `lizenz`, `fundweg` und `sources` ziehen `generated` nach, ändern aber den geprüften Text
nicht und lassen die Freigabe stehen. `human_without_check` = eine stable-Seite ist durch `human:<id>` freigegeben,
aber kein anderer Akteur (`[wiki] actor`, `process:…`) hat denselben Inhalt mit Prüfwert gegengeprüft;
Freigaben ohne Prüfwert lösen das nicht aus. `verified_unhashed` = die gültige Gegenprüfung einer noch nicht
freigegebenen Seite hat keinen Prüfwert (Vermerk aus der Zeit vor 0.3.0): sie gilt weiter, zählt aber nicht als
Gegenprüfung für eine Freigabe; neu gegenprüfen lassen und mit `verify` vermerken. `check_outdated` = eine Seite
ohne Freigabe hat Vermerke von Claude oder einem Prozess, aber keiner passt mehr zum Inhalt. Ein Vermerk ohne
Prüfwert gilt, solange weder `generated.at` noch ein späterer Vermerk mit anderem Prüfwert eine Änderung nach
ihm zeigt. `human_unsigned` (nur mit `[trust] signed_human = true` und in
einem Git-Repo) = ein `verified`-Eintrag durch `human:<id>` ist noch nicht committet oder kam mit einem Commit ohne
gültige Signatur (`git blame` je Seite, dann `%G?` des Commits; gültig sind G und U). OKF §5.2 nennt `verified` unabhängig von `generated.at`; okfw bindet die
Freigabe strenger an den Inhalt.

Datum: `source_undated` = Quellenkarte ohne `csl`-Block, aus deren Feldern sich kein Datum ergibt
(`date`, `created`, `published`, `year`); im APA-Beleg stünde „o. J.“. Für Repo-Dateien und Webseiten
das Commit-, Veröffentlichungs- oder Stand-Datum setzen. Ist eine Quelle wirklich undatiert, das bewusst
mit `date: o. J.` (auch `n. d.`, `undatiert`) vermerken; dann entfällt die Warnung. `csl_incomplete` meldet
einen `csl`-Block ohne Titel, ohne `issued` oder ohne Autorschaft (`author` oder `editor`) und einen Block ohne `DOI`
und ohne `accessed`; ein fehlendes `issued` fällt also schon dort auf.

## Karten ablösen und löschen

Ablösen ist die erste Wahl: `deprecate` setzt `status: deprecated`, die Karte bleibt erhalten, und Links und Belege bleiben gültig. `--nachfolger`
trägt `superseded_by: /<seite>.md` ein; zeigt das Feld ins Leere, meldet der Lint `superseded_missing`. Das darf jeder Akteur, wie
`verify --status deprecated`. Zurück geht es mit `verify <seite> --by … --status draft`; das Dashboard gibt abgelöste Karten nicht frei.

Löschen ist die zweite Stufe. `delete <seite>` zeigt ohne `--yes` nur die Folgen:

- Belege: `sources[]` anderer Karten, die auf sie zeigen, mit der Zahl ihrer Fußnoten
- Fußnoten und Links im Text anderer Karten, Nennungen als Nachfolger, Symlinks auf die Datei
- Karten mit unlesbarem Frontmatter, deren Belege sich nicht prüfen lassen
- das Rohquellen-Paket (`raw_packet`, sonst `<raw>/<slug>` bei Quellenkarten)

Mit `--yes` gilt:

- Nur eine abgelöste Karte und nur `--by human:<id>`. Karten mit unlesbarem Frontmatter lassen sich nicht ablösen und dürfen darum direkt gelöscht werden. Karten aus `[paths] protected` und Symlinks bleiben stehen.
- Belege sperren: Der Lint meldete danach `source_missing` als Fehler. Erst dort ersetzen oder ausdrücklich `--belege-brechen`; das Ereignis nennt die betroffenen Karten.
- Die Rohquelle bleibt liegen (danach `raw_uningested`). Nur `--mit-rohquelle` nimmt sie mit, und nur, wenn keine andere Karte sie nutzt und keine Karte mit unlesbarem Frontmatter das offenlässt.

Der Kern löscht nichts endgültig. Die Karte wandert nach `<bundle>/.papierkorb/<eintrag>/` (`karte.md`, `eintrag.json`, gegebenenfalls `rohquelle/`):

- **Warum nicht Git:** Ein Wiki muss kein Repo sein, und der Kern führt keine Git-Befehle aus. Der Ordner funktioniert überall und auch für noch nicht committete Karten.
- **Warum im Bundle:** Der Punkt am Anfang hält ihn aus Seiten, Indexen, Lint und Dashboard, und er bleibt beim Verschieben des Wikis dabei.
- **Im Repo:** Der Papierkorb trägt ein eigenes `.gitignore`, damit Gelöschtes nicht mit dem nächsten Commit wieder ins Repo kommt. Bleibend ist das Ereignis `delete` im Ereignisstrom. Der Guard-Hook sperrt den Ordner für Edit und Write.
- **Veröffentlichen:** Wer das Bundle als Ordner weitergibt, nimmt `.papierkorb/` nicht mit, denn dort liegt auch, was öffentlich nicht mehr erscheinen soll.

`restore` prüft einen Eintrag wie etwas Fremdes: Das Ziel muss wieder eine Seite im Bundle ergeben, frei sein und außerhalb von `[paths] protected` liegen, und im Eintrag darf kein Symlink liegen. Endgültig entfernen heißt, den Ordner des Eintrags von Hand zu löschen.

Im Dashboard (`serve`) heißen die Knöpfe an der Karte „Ablösen“ und, bei abgelösten Karten, „Löschen“. Der Dialog nennt die Folgen und fragt Belege und Rohquelle einzeln ab. Die Einstellungen haben einen Reiter „Papierkorb“. Akteur ist `human:<kennung>` aus den Einstellungen. Der Server prüft Folgen und Stand der Karte beim Absenden noch einmal: Kam ein Beleg hinzu oder hat sich die Karte geändert, antwortet er 409 und ändert nichts.

## Freigabe über Claude

Ziel: Eine Freigabe, die Claude auf Zuruf schreibt („Charge 3 freigeben“), bleibt an den Inhalt gebunden,
den ein Mensch gesehen hat, und lässt sich außerhalb von Claude nachprüfen. Drei Vertrauensstufen; die Spalte
„in `status`“ nennt das Wort, unter dem `status` sie zählt:

| Stufe | in `status` | Woran erkennbar | Wer |
|---|---|---|---|
| draft | unverified | `status: draft`, kein gültiger Vermerk zum jetzigen Inhalt | Claude schreibt mit `new`, `body`, `sources` |
| geprüft | machine-confirmed | `verified`-Eintrag eines anderen Akteurs (`[wiki] actor`, `process:…`) mit Prüfwert zum jetzigen Inhalt | Gegenprüfung am Original (`/lesen --pruefen` oder ein Prozess), dann `verify <seite> --by <akteur> --note …` |
| stable | human-reviewed | zusätzlich `verified` durch `human:<id>` mit demselben Prüfwert, `status: stable` | nur ein Mensch: `verify <seiten> --by human:<id> --status stable` |

Jeder `verify`-Vermerk trägt den Prüfwert `body_sha256` des Inhalts, den er geprüft hat. Ändert sich der
Körper danach, verfällt die Freigabe: `body` setzt eine stable-Seite auf `draft`, Lint meldet
`verified_outdated`, `status` und Dashboard zählen verfallene Freigaben. Die Vertrauensstufen in `status` zählen nur
Vermerke, die zum jetzigen Inhalt passen; Seiten mit
Vermerken, von denen keiner mehr passt, stehen getrennt als „n verfallen“, dazu „n zur Freigabe“. Ohne frische Gegenprüfung lehnt
`verify --status stable` ab (`--force` schreibt trotzdem und vermerkt das im Ereignis; Lint
`human_without_check`). Das Dashboard fragt in diesem Fall nach, statt abzulehnen. `verify --status deprecated` (Seite überholt) darf jeder Akteur setzen, auch Claude; die Seite
fällt damit aus `no_footnotes` und aus „Zur Freigabe“ (auch aus „n zur Freigabe“ in `status`). `status` und `verified` ändert nur `verify` und `body` (auch `--nachziehen`), das eine stable-Seite auf draft setzt: Der Guard blockiert Edit und Write, die
diese Schlüssel einer Wiki-Seite ändern; eine neue Seite darf `status: draft` tragen. Der Guard sperrt
`status` und `verified` nur für die Schreibwerkzeuge von Claude; ein Prozess mit Shell, auch eine
Sitzung mit Bash, kann die Datei trotzdem ändern und einen `human:`-Vermerk schreiben. Erst
`[trust] signed_human = true` bindet eine Freigabe an eine Signatur, die Claude nicht leisten kann;
wer Freigaben als Beleg braucht, schaltet das ein — `signed_human` ist eine Lint-Regel, kein Guard, und
lässt sich mit `human_unsigned = off` abschalten; die Signatur schützt nur, solange Schlüssel und
Passphrase der Sitzung nicht zugänglich sind.

Ablauf einer Charge:

1. Claude schreibt die Seiten (`body`), ein frischer Agent prüft sie gegen die Originale (`/lesen --pruefen`), Claude
   arbeitet die Befunde ein und setzt `verify <seiten> --by <[wiki] actor> --note "Charge 3: …"`.
2. `dashboard` erzeugen, „Zur Freigabe“ aufklappen: je Seite der ganze Text mit hervorgehobenen Belegen (lange
   Seiten zuerst die belegten Aussagen, der ganze Text klappt auf, eingeklappt mit „n Absätze ohne Fußnote“)
   und die Notiz der Gegenprüfung. Lesen, Seiten markieren, Kennung (aus `[trust] human`) und Notiz („Charge 3“)
   eintragen.
3. Die Zeile darunter kopieren und selbst im Terminal ausführen (oder Claude wörtlich übergeben): ein Aufruf,
   ein Ereignis je Seite. Wer eine Seite danach ändert, findet sie unter „Freigabe verfallen“ wieder.
4. Den Stand committen, mit `[trust] signed_human = true` signiert. Lint `human_unsigned` meldet jede
   Freigabe, die nicht aus einem signierten Commit stammt; diese Signatur kann Claude nicht leisten.

Commit-Signatur einrichten (SSH-Schlüssel, Git ≥ 2.34; einmal je Rechner, der Schlüssel bleibt außerhalb von Claude):

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
echo "$(git config user.email) $(cat ~/.ssh/id_ed25519.pub)" >> ~/.ssh/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers
git commit -S -m "Charge 3 freigegeben"    # oder dauerhaft: git config --global commit.gpgsign true
```

## Lizenzen und Nutzungsrechte

Jede Quellenkarte trägt eine Lizenz. Der Kern ordnet sie über Name und URL einer Klasse zu; passen
mehrere Muster (Mischangaben wie „Metadaten gemeinfrei; Abstracts geschützt“), gilt die strengste. Ein
`license.class` auf der Karte geht jedem Muster vor. Gültige Klassen: `frei`, `by`, `sa`, `nd`, `nc`, `nc-nd`,
`vorbehalten`, `unbekannt`; andere Werte werden ignoriert.
Je Klasse legt eine Matrix fest, was die Person mit der Quelle tun darf. Rechtewerte, wie sie in `wiki.toml` und auf
der Karte stehen, und wie `rechte` und das Dashboard sie anzeigen: `ja` = **erlaubt**, `bedingt: …` = **mit Auflage**
(der Hinweis sagt welche), `nein: …` = **nicht erlaubt**, `fragen: …` = **erst fragen**. Die Matrix ist eine
Arbeitshilfe, keine Rechtsberatung; im Zweifel gilt `fragen`.

| Aktion | Gemeinfrei | Namensnennung (CC BY, MIT, Apache) | … gleiche Lizenz (SA, GPL) | Keine Bearbeitung (ND) | Nicht kommerziell (NC) | NC-ND | Alle Rechte vorbehalten | Unbekannt |
|---|---|---|---|---|---|---|---|---|
| Kurz zitieren mit Beleg | ja | ja | ja | ja | ja | ja | ja (Zitatrecht) | ja (Zitatrecht) |
| Volltext lokal ablegen | ja | ja | ja | ja | ja | ja | bedingt: nur eigene Arbeit | fragen |
| Volltext weitergeben, versionieren | ja | bedingt | bedingt: gleiche Lizenz | bedingt: nur unverändert | bedingt: nicht kommerziell | bedingt: nicht kommerziell, unverändert | nein | fragen |
| Abbildung, Tabelle übernehmen | ja | bedingt | bedingt: gleiche Lizenz | bedingt: nur unverändert | bedingt: nicht kommerziell | bedingt: nicht kommerziell, unverändert | fragen: Genehmigung | fragen |
| Bearbeiten, übersetzen, nachzeichnen | ja | bedingt: Änderungen kennzeichnen | bedingt: Änderungen kennzeichnen, gleiche Lizenz | nein | bedingt: nicht kommerziell | nein | nein | fragen |
| Volltext maschinell auswerten | ja | ja | ja | ja | ja | ja | bedingt: eigene Auswertung, TDM-Vorbehalt beachten, keine Dienste, die speichern oder trainieren | fragen |
| Kommerzieller Rahmen | ja | ja | ja | ja | nein | nein | fragen | fragen |

`bedingt` heißt bei Namensnennung immer auch: Urheber und Lizenz angeben. Projekte überschreiben Zellen in
`[rights.<klasse>]`, einzelne Quellen mit `rights:` auf der Karte (z. B.
`rights: {abbildung: "ja: Abdruckgenehmigung des Verlags vom 2026-10-20"}`; den Beleg dafür als
Quelle erfassen).

Wer eine Rohquelle allein auf § 44b UrhG stützt, muss die Kopie löschen, sobald sie für das Text und Data Mining nicht mehr erforderlich ist (Abs. 2 S. 2); okfw legt Rohquellen dauerhaft ab und löscht sie nicht von selbst, und `delete --mit-rohquelle` verschiebt sie nur in den Papierkorb, aus dem der Eintrag von Hand zu entfernen ist.

Lizenzen finden: `lizenz <seite> --fetch` fragt Crossref (bevorzugt die Lizenz der Verlagsfassung), für
DOIs, die Crossref nicht kennt, DataCite, dann Europe PMC und OpenAlex (je Fundort, Verlagsfassung zuerst);
ohne DOI, PMID oder PMCID bleibt nur das Original (Lizenzhinweis im PDF, Impressum, Kopf einer Datei),
dann `lizenz <seite> --name … --url … --via originaldokument`. Unpaywall braucht eine E-Mail-Adresse
im Aufruf und ist deshalb nicht eingebaut.

`via` sagt, woher die Lizenzangabe stammt. Werte:

| `via` | Herkunft |
|---|---|
| `crossref (vor)`, `datacite`, `europepmc`, `openalex (publishedVersion)` | `lizenz --fetch`, mit Dienst und Fassung |
| `capture` | `capture --license`, von `new source --set raw_packet=…` aus dem Manifest übernommen |
| `originaldokument` | Lizenzhinweis im Dokument selbst (PDF, Impressum, Kopfzeile einer Datei) |
| `repo-lizenz` | `LICENSE`-Datei des Repositoriums am gepinnten Commit; am besten selbst als Rohquelle erfasst |
| `github-api` | Lizenzfeld der GitHub-API (`/repos/<owner>/<name>/license`, `spdx_id`) |
| `manuell` | Standard von `lizenz --name` ohne `--via`; besser einen der Werte oben setzen |

Erläuterungen gehören nicht in `via`, sondern in eine Notiz auf der Karte (`event note …` oder den
Abschnitt „Zugang“).

Graph: Kanten sind Links im Text **und** `sources[].resource`, die auf Seiten im Bundle zeigen
(OKF §5.1: eine Quelle, die ein Konzept ist, ist schon eine Kante). `few_links` zählt nur Links im Text.

## Schaubilder

Schaubilder sind SVG-Dateien unter `<bundle>/abbildungen/<name>.svg` (Name kebab-case), Rasterbilder WebP-Dateien unter
`<bundle>/abbildungen/<name>.webp` (siehe [Bilder](#bilder)); ein Name gilt für genau eine Datei. In einer Seite stehen sie als Bild
mit einer Bildunterschrift direkt darunter; die Unterschrift beginnt mit `*Abb.` und trägt eine Fußnote, die belegt, woher das
Bild kommt:

```markdown
![Ablauf der Prüfung](/abbildungen/ablauf.svg)
*Abb.: Was das Bild zeigt. Eigene Darstellung nach Müller[^mueller].*
```

Rohes HTML bleibt in der Seite abgeschaltet. `abbildung <name> --file x.svg` prüft die Datei, entfernt, was nicht
erlaubt ist, legt sie ab und schreibt ein Ereignis (Größe über 200 KB: Exit 2). Ein Symlink als Ziel oder ein Ordner `abbildungen/`, der
aus dem Bundle hinausführt, ist ebenfalls Exit 2. Der Guard lässt das Schreiben in
`abbildungen/` zu, die Dateien sind keine Seiten: Bundle-Scan, Indexe und Lint ignorieren den Ordner, er erscheint nicht als
Bereich im Root-Index. Hat ein Typ `folder = "abbildungen"`, bleiben dessen `.md`-Seiten Seiten. Es gibt sonst keine Bilder und kein Mermaid; Textdiagramme (```` ```text ````) bleiben daneben.

**Bereinigung** (nur Standardbibliothek, `xml.etree`), beim Ablegen und bei jedem Bau des Dashboards:

- Abgelehnt wird, was sich nicht sicher lesen lässt: alles außer UTF-8 (mit oder ohne BOM), `<!ENTITY` und jedes
  `<!DOCTYPE` außer dem Standardkopf, ungültiges XML, eine Wurzel, die kein `svg` ist, mehr als 200 KB oder 5000 Elemente,
  Verschachtelung über 60 Ebenen, ein `<use>`, das sich selbst enthält oder die Zeichnung nach dem Auflösen aller `<use>`
  auf mehr als 20000 Elemente wachsen ließe, und ein fehlendes `viewBox` ohne umrechenbare `width` und `height`: Zahlen
  oder `px`, `pt`, `pc`, `mm`, `cm`, `in`, `Q` werden in Nutzereinheiten umgerechnet, `%` und `em` nicht.
- Der Standardkopf ist genau `<!DOCTYPE svg PUBLIC "…" "…">` mit einem Paar aus den W3C-Kennungen für SVG 1.0 und 1.1 (wie
  ihn Illustrator oder Graphviz schreiben), vor dem Wurzelelement und ohne internes Subset. Er wird entfernt, nie gelesen;
  die DTD wird nicht geladen.
- Elemente außerhalb der Liste `svg g path rect circle ellipse line polyline polygon text tspan title desc defs
  linearGradient radialGradient stop marker clipPath use` entfallen samt Inhalt (`script`, `a`, `image`,
  `foreignObject` …), ebenso Elemente und Attribute fremder Namensräume (etwa von Inkscape). Aus `<switch>` wird eine
  Gruppe mit der ersten erlaubten Alternative, so bleibt der Ersatztext neben einem `foreignObject` aus draw.io.
- `<style>` und `class` kommen nie auf die Seite. Regeln mit einfachem Selektor (`rect`, `.klasse`, `rect.klasse`, `#id`, `*`)
  werden in das `style`-Attribut der passenden Elemente übernommen, nach Gewicht und Reihenfolge wie im Browser, jede
  Deklaration durch dieselben Prüfungen wie unten. Anderes (`g .a`, `@media`, `@import`, `@font-face`) entfällt und wird
  als `Stilregeln nicht übertragen` gemeldet. Wächst die Datei dadurch über 200 KB, wird sie abgelehnt.
- Attribute nur aus Geometrie und Präsentation (Koordinaten, `viewBox`, `transform`, `fill`, `stroke`, Schrift, …) und `id`;
  kein `on*`. `href` und `xlink:href` nur auf `use` und Verläufen und nur als `#…`; `url(…)` nur als `url(#…)`;
  Ein Wert mit `\`, `/*`, `javascript:`, `data:`, einem `url(` auf anderes als `#…`, `@` im `style` oder einer
  CSS-Ladefunktion (`image-set(`, `src(`, `image(`, `cross-fade(`, `element(`, `paint(`) verwirft das ganze Attribut.
- `style` bleibt auf die Eigenschaften der Zeichnung gekürzt (Füllung, Strich, Deckkraft, Schrift, Marker, Clip, `display`); alles
  andere, etwa `position` oder `width`, entfällt, und die Meldung auf stderr nennt es als `Stil <name>`. `transform` am
  Wurzelelement entfällt, weil es die Zeichnung über die Seite höbe.
- Das Wurzelelement bekommt `role="img"`; `aria-label` mit dem Titel des Bildes aus der Seite setzt erst der Bau des
  Dashboards, die abgelegte Datei trägt es nicht.
- Im Dashboard bekommen `id`s und ihre Verweise je Abbildung ein Präfix (`abb1-`, `abb2-` …), damit sich mehrere Abbildungen
  mit gleichen `id`s nicht stören.

**Dialog.** „Abbildung einfügen“ fragt den Server zuerst nur (`pruefen`), geschrieben ist dann noch nichts. Gibt es den Namen
schon oder ändert das Bereinigen das Bild, zeigt der Dialog die bereinigte Fassung, was entfällt und warum, und fragt:
vorhandene ersetzen (mit den Seiten, die sie schon zeigen) oder unter einem freien Namen ablegen. Reine Angaben der Werkzeuge
(`version`, `xml:space`, `-inkscape-…`, `metadata`, fremde Namensräume) gelten als „ohne Wirkung“ und halten nichts auf.

**Dashboard.** Die Leseansicht bettet das bereinigte SVG inline ein, als `<figure class="abbildung">` mit `<figcaption>`
(die Zeile `*Abb. …*` samt APA-Beleg); die Datei bleibt ohne Netz. Fehlt die Datei, wird sie abgelehnt oder ist sie ein Symlink aus dem Bundle hinaus, steht statt des Bildes
„Abbildung nicht eingebunden: <name>“. Ein WebP aus `abbildungen/` bettet sie nach der Bereinigung als `data:image/webp` ein.
Jedes andere Bild (`http`, `png`, `data:` im Text, ein anderer Ordner) wird nie geladen, es erscheint als
Text „Bild nicht eingebunden: …“. Die öffentliche Ausgabe (`dashboard --oeffentlich`) bettet nur Abbildungen ein, die eine
öffentliche Seite nutzt; die einer internen Seite fehlen, ihre Belege auf verborgene Quellen entfallen wie sonst.

**Rechte.** Eine übernommene Abbildung ist eine Handlung nach „Abbildung oder Tabelle übernehmen“ (`rechte --aktion
abbildung`): Steht in der Unterschrift „übernommen“, prüft Lint `figure_rights` das Recht für die belegte Quelle.
Eine eigene Darstellung nach einer Quelle („Eigene Darstellung nach Müller“) ist keine Übernahme; wer nachzeichnet,
prüft „Bearbeiten, übersetzen, nachzeichnen“ selbst.

## Bilder

Rasterbilder (Fotos, Illustrationen) sind WebP-Dateien unter `<bundle>/abbildungen/<name>.webp`, eingebunden wie ein
Schaubild. WebP, weil es klein ist: Ein Bild 1536x864 hat rund 100 KB, als PNG 3 MB; SVG bleibt für Schaubilder. PNG und
JPEG nimmt okfw nicht an, `abbildung` nennt den Weg (`cwebp -q 80 bild.png -o bild.webp`).

`abbildung <name> --file x.webp` prüft und legt ein vorhandenes WebP ab (über 1 MB oder über 4096 Pixel je Seite: Exit 2).
Mit `--rolle` nimmt `--anfrage DATEI` das JSON der Anfrage, die das Bild erzeugt hat, ins Ereignis auf (so legt `serve` ein
angenommenes Bild ab).
**Bereinigung** (nur Standardbibliothek, `clean_webp`), beim Ablegen und bei jedem Bau: RIFF-Kopf und Länge müssen stimmen,
genau ein Bildblock (`VP8` oder `VP8L`) mit gültigem Kopf; animierte WebP werden abgelehnt. Übernommen werden nur die
Blöcke, die zeichnen; EXIF, XMP, ICC-Profil und Unbekanntes entfallen (gemeldet auf stderr). Die Pixel dekodiert okfw
nicht, das tut der Browser.

`bild <name> --prompt TEXT` erzeugt ein Bild mit dem Modell der Rolle `bild` und legt es als WebP ab. Den Abruf macht
`rollen.py` direkt über `POST /images/generations` eines OpenAI-kompatiblen Anbieters. Vor dem Senden zeigt der Befehl
Modell, Anbieter, Datenklasse des Wikis, Format, Seed, Tempo, ob ein Negativ-Prompt wirkt, die erwartete Dauer,
Hinweise zum Prompt und „Was gesendet wird“ als JSON ohne Key; `--trocken` hört dort auf.

| Option | Wirkung |
|---|---|
| `--wunsch WORTE` | Einstellungen in Worten, etwa „breit, Entwurf, ohne Menschen, drei Varianten“; ausdrückliche Optionen gehen vor. Häufiges nach „ohne“ wird englisch (Menschen → people), Unbekanntes bleibt mit Hinweis stehen |
| `--format F` | `quadrat` 1024x1024 (Vorgabe), `quer` 1536x864, `hoch` 864x1536, `gross` 2048x1152, `4:3` 1280x960, `3:2` 1536x1024 (die beiden letzten noch nicht am Modell ausprobiert) oder `BREITExHÖHE`, auf Vielfache von 16 gerundet (256 bis 2048) |
| `--tempo entwurf\|fein` | `entwurf`: 8 Schritte (`num_inference_steps`), Sekunden; `fein` (Vorgabe): ohne Angabe, rund 24 s bei 1536x864 |
| `--ohne X` | was nicht ins Bild soll, mehrfach; setzt `negative_prompt` und `guidance_scale` 4, denn ohne Guidance wirkt er kaum. Guidance mit verkürzten Schritten ergab leere oder verrauschte Bilder, darum gilt dann immer Tempo `fein` |
| `--seed N` | gleiche Zahl und gleiche Einstellungen: bitgleiches Bild; anderes Format oder Tempo: anderes Bild. 0 bis 2147483644, ohne Angabe zufällig |
| `--varianten N` | 1 bis 4 Bilder `<name>-1` … mit Seed, Seed+1 …, jedes einzeln wiederholbar (statt `n`) |
| `--text` / `--no-text` | Schrift im Bild; Vorgabe aus: okfw hängt „no text, no letters, no logos“ an, weil das Modell sonst Wörter erfindet |
| `--stil S` | eigener Stil aus `[bild.stile]` oder ein eingebauter: `foto`, `tilt-shift`, `linolschnitt`, `risografie`, `stickerei`, `oelgemaelde`, `film-noir`, `isometrisch`, `pop-art`, `flach` |
| `--titel T` | Titel für die Einbindung und die vorgeschlagene Bildunterschrift |
| `--ersetzen` | ein vorhandenes WebP gleichen Namens überschreiben |
| `--trocken` | nur zeigen, was gesendet würde |

Aus `--wunsch` liest okfw: „quadratisch“ → `quadrat`; „breit“, „Folie“, „Querformat“, „16:9“ → `quer`; „hochkant“,
„Plakat“, „Handy“, „9:16“ → `hoch`; „groß“, „Druck“ → `gross`; „Entwurf“, „Skizze“, „schnell“ → `entwurf`; „fein“,
„final“ → `fein`; „ohne X“, „keine X“ → `--ohne X`; „mit Text“ / „ohne Text“; „zwei|drei|vier Varianten“; „in meinem
Design“ → Stil `design`; Namen der eingebauten Stile. Gesendet werden nur `model`, `prompt`, `size`, `seed`, `n`,
`response_format`, `output_format` (`webp`), `num_inference_steps`, `guidance_scale` und `negative_prompt`: Ein Backend wie
vLLM-Omni nimmt unbekannte Felder stumm an, ein Tippfehler fiele nicht auf.

**Hinweise zum Prompt** (aus Versuchen mit 84 Bildern): Unter zwölf Wörtern fragt okfw nach Motiv, Anordnung, Material,
Licht und Stil. Farbnamen, die Gegenstände sind („lemon-yellow“ ergab Zitronen), sollen einem neutralen Namen mit Hex-Code
weichen. Ab vier Elementen stimmt die Zahl oft nicht. Text im Bild gelingt bei kurzen Wörtern in Anführungszeichen; ab drei
Wörtern kommen echt aussehende Fehler, der Satz gehört dann in die Unterschrift. Nicht in den Prompt gehört, was die
Datenklasse des Anbieters nicht erlaubt: keine personenbezogenen Daten, keine realen Personen.

**Eigene Stile** stehen in der `wiki.toml`; `{motiv}` steht für den Prompt, ohne `{motiv}` wird die Vorlage angehängt;
höchstens 600 Zeichen je Vorlage:

```toml
[bild.stile]
design = "Minimal flat editorial illustration, warm alabaster off-white background (#F4F3ED), charcoal line work (#1A1A1A), one bright yellow accent color (#FCE53A), generous empty space, calm, no gradients, no text. {motiv}"
```

**Nachweis.** Jedes Bild schreibt ein Ereignis `create abbildungen/<name>` mit `modelle` (Rolle `bild`) und `bild`:
Anbieter, Endpunkt, Modell, die ganze Anfrage mit Seed, Dauer und die Angaben der Antwort ohne Bilddaten (höchstens zwölf
kurze Werte, ein gespiegelter Key geschwärzt). Der Key steht nie darin. Die vorgeschlagene Unterschrift lautet `*Abb.: <Titel>. KI-generiert mit <Modell> (<Anbieter>), <Datum>.*`;
sie erfüllt `figure_generated`. Eine Fußnote verlangt `figure_caption` bei erzeugten Bildern nicht, denn sie stammen aus
keiner Quelle. Erzeugte Bilder sind keine Belege und tragen keine Sachaussage, die nicht im Wiki belegt ist. Ob ein
Anbieter erlaubt, seine Bilder öffentlich zu zeigen (`dashboard --oeffentlich`), steht in seinen Bedingungen; okfw prüft
das nicht.

## Modellrollen

`rollen.py` (neben `okfw.py`, nur Standardbibliothek) beantwortet, wer welche Arbeit im Wiki erledigt. Fünf Rollen:
`lesen`, `gegenpruefen`, `bearbeiten` (per Prompt), `webabruf` und `bild` (Bilder erzeugen, nur Harness `direkt` mit einem
OpenAI-kompatiblen Anbieter; ab Werk ohne Modell, also aus). Jede zeigt auf einen **Harness**, einen **Anbieter** und
ein **Modell**: der Harness ist der Weg, der Anbieter die Abrechnung, das Modell das Gehirn.

| Harness | Weg |
|---|---|
| `sitzung` | die laufende Claude-Code-Sitzung; der Skill startet einen Subagenten, `rollen.py` startet keinen Prozess (Exit 10) |
| `claude` | `claude -p`; Anbieter `anthropic` über die Anmeldung, andere mit Anthropic-kompatiblem Endpunkt |
| `opencode` | `opencode run` mit eigenem Server, nur der gewählte Anbieter |
| `direkt` | HTTP-Aufruf ohne Harness, OpenAI- oder Anthropic-kompatibel |
| `container` | nur Rolle `webabruf`: `web-recherche` im Container, Modell wählbar, Anbieter fest `anthropic` |

Ein **Anbieter** hat Endpunkte je API-Form (`openai`, `anthropic`), den Namen seines Eintrags im Schlüsselbund (`schluessel`)
oder einer Umgebungsvariable (`schluessel_env`), die Datenklassen, die er bekommen darf (`daten`, Standard `oeffentlich` und
`intern`), und optional einen `vorbefehl`, etwa für einen Platzverwalter (`{modell}` wird ersetzt, der Aufruf steht dahinter).
Seine Kennung besteht nur aus Buchstaben, Ziffern, Bindestrich und Unterstrich; ein Punkt wäre in der TOML-Datei ein Pfad.

```bash
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py [--wiki PFAD] status [--json]   # Harnesses, Anbieter, Rollen, ohne Modellaufruf
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py zeige lesen [--json]   # aufgelöste Rolle mit Herkunft je Feld und Sperre
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py modelle <anbieter> [--bild] [--json]   # Modellliste des Anbieters (Vorschlag, kein Zwang); --bild: nur Bildmodelle (modality image, wo der Anbieter das Feld nennt)
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py setze lesen --harness direkt --anbieter fern --modell m
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py anbieter fern --name … --openai URL [--anthropic URL] [--schluessel NAME | --schluessel-env NAME] [--hinweis …] [--daten oeffentlich,intern]   # oder --entfernen
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py auftrag --rolle lesen --datei auftrag.md [--anhang datei]… [--trocken] [--json] [--ohne-wiki]
uv run --no-config --quiet ~/.claude/skills/okfw/rollen.py --wiki wiki.toml bild --nutzlast anfrage.json --aus bild.webp [--json]   # ein Bild über die Rolle bild; nutzt serve
```

`--wiki PFAD` (vor dem Befehl) nimmt eine `wiki.toml` statt der, die `rollen.py` aufwärts ab dem Arbeitsordner findet; eine
fehlende Datei ist Exit 2. Ohne `wiki.toml` sind Datenklasse und Sperren des Wikis nicht geprüft: `status` und `zeige`
sagen es (`zeige --json` im Feld `hinweis`), und `auftrag` bricht mit Exit 2 ab. Nur ausdrücklich mit `--ohne-wiki` läuft er
trotzdem, dann mit der strengsten Datenklasse `personenbezogen`. `zeige --json` nennt zusätzlich `wirksam` (der Anbieter,
dessen Sperren gelten), `status --json` im Feld `sitzung` den Anbieter, über den die laufende Sitzung geht.

**Schichten.** Die spätere gewinnt, Feld für Feld, und `zeige` nennt die Herkunft jedes Felds:
`anbieter.toml` neben `rollen.py` (ab Werk) < `einstellungen.local.toml` daneben (nur eigene Installationen, nicht im Plugin)
< `~/.config/okfw/einstellungen.toml` (`$OKFW_EINSTELLUNGEN`; sie schreiben die Oberfläche und die Befehle `setze` und
`anbieter`, nie die `wiki.toml`) < `[rollen]` der `wiki.toml`. Jede Schicht wird beim Laden geprüft, auch von Hand
geschriebene: Ein falscher Typ, ein unbekanntes Feld oder eine ungültige Kennung ist ein Bedienfehler (Exit 2), nie eine
stille Wirkung.

```toml
[rollen]
daten = "intern"                # Datenklasse des Wikis: oeffentlich | intern | personenbezogen (Standard intern)
sperre = ["openai"]             # Anbieter, an die dieses Wiki nichts schickt

[rollen.lesen]                  # je Rolle freie Felder harness, anbieter, modell; sie gelten für dieses Wiki
harness = "claude"
anbieter = "anthropic"
modell = "sonnet"
```

`okfw.py` kennt `daten` und `sperre` und prüft sie (Lint `rollen_unknown`); die Tabellen je Rolle sind frei, `config_unknown`
meldet sie nicht, ihre Felder prüft `rollen.py`.

**Sperren.** Eine Rolle ist gesperrt (Exit 2, kein Aufruf; `setze` zeigt es mit ⚠), wenn

- die Datenklasse des Wikis für den Anbieter nicht freigegeben ist (`[rollen] daten` gegen `daten` des Anbieters; `daten = []`
  heißt: nichts erlaubt),
- der Anbieter in `[rollen] sperre` steht,
- der Harness den Anbieter nicht ansprechen kann (`claude` braucht die Anthropic-Form),
- `webabruf` nicht im `container` läuft, ein anderer Harness `container` wählt oder der Webabruf einen anderen Anbieter als
  `anthropic` nennt,
- der Harness `sitzung` einen anderen Anbieter nennt als den, über den die Sitzung läuft (dann `opencode` oder `direkt` wählen),
- kein Modell gewählt ist (außer bei `sitzung`), der Modellname mit `-` beginnt oder Harness, Anbieter oder Rolle unbekannt sind.

**Die Sitzung.** `rollen.py` bestimmt, über wen die laufende Sitzung geht. Ohne `ANTHROPIC_BASE_URL` ist es `anthropic`; ist sie
gesetzt, gilt der Anbieter, dessen Endpunkt dazu passt, und passt keiner, `sitzung-eigener-endpunkt`, ein Endpunkt, den
`rollen.py` nicht kennt. Ebenso gilt ein Schalter, der die Sitzung über Bedrock, Vertex AI oder eine andere Plattform leitet (`CLAUDE_CODE_USE_BEDROCK`,
`CLAUDE_CODE_USE_VERTEX` …, Wert nicht leer und nicht `0`, `false`, `no` oder `off`), und er geht `ANTHROPIC_BASE_URL` vor.
Ein unbekannter Endpunkt bekommt keine Daten der Klasse `personenbezogen`; bei `ANTHROPIC_BASE_URL` ändert das, wer ihn als
Anbieter einträgt und die Klasse dort freigibt, beim Plattform-Schalter nichts. Sperre und Datenklasse gelten dann für diesen
Anbieter, nicht für `anthropic`. Steht beim Harness `sitzung` der Anbieter `anthropic` (der Werk-Standard), folgt die Rolle
der Sitzung; läuft die nicht über Anthropic, sagt es `hinweis`. `claude` mit Anmeldung und `container` gehen immer zu
Anthropic; läuft die Sitzung woanders, müssen beide Anbieter erlaubt sein.

Lesen und Gegenprüfen mit demselben Anbieter und Modell warnt, weil sich Fehler dann wiederholen, statt aufzufallen. Ab Werk
liest `sonnet` und prüft `opus` gegen, damit der Werk-Standard seine eigene Warnung nicht auslöst.

**Einstellungen schreiben.** `setze`, `anbieter` und die Oberfläche schreiben die Nutzerdatei neu aus `[person]`, `[anbieter]`,
`[rollen]` und ihren fremden Tabellen und Schlüsseln; Kommentare gehen dabei verloren. Ein eigener Anbieter braucht eine
Adresse (`openai` oder `anthropic`). Nennt die neue Eingabe `daten`, `vorbefehl` oder `modelle` nicht, bleibt der bisherige
Wert stehen, ein erneutes Speichern gibt also keine Daten stillschweigend frei. `vorbefehl` und `modelle` setzen weder die
Befehlszeile noch die Oberfläche; wer sie braucht, trägt sie von Hand in die Nutzerdatei ein. Weil sich die Schichten Feld für
Feld mischen, würde ein überschriebener Anbieter ab Werk seinen Key an die neue Adresse schicken; `anbieter` und die
Oberfläche lehnen darum eine Kennung ab, die ab Werk oder in einer lokalen Datei schon steht, und `--entfernen` löscht nur
eigene Anbieter. Von Hand in der Nutzerdatei geht beides.

**Sicherheit.** Ein Worker bekommt nie Werkzeuge: Anhänge stehen als Daten zwischen Markierungen im Auftrag, das Modell gibt
nur Text zurück (`claude` mit `--tools ""` und `--restricted`, OpenCode mit `permission` auf `deny`, ohne MCP und nur mit dem
einen Anbieter). Gleich benannte Anhänge heißen `Elternordner/Name`, damit keiner den anderen ersetzt. Keys stehen in keiner
Datei, nur ihr Name. Über die Oberfläche und `rollen.py anbieter` nimmt `setze_anbieter` nur Namen an, die wie der Name eines
Keys aussehen: im Schlüsselbund enden sie auf `-api-key`, als Umgebungsvariable auf `_KEY` (auch `_API_KEY`), und jeder Name
gehört höchstens einem Anbieter, sonst ginge ein Key an einen fremden Host. Namen mit `SECRET`, `PRIVATE`, `ACCESS`, `SSH`
(auch `GPG`, `PGP`, `SIGNING`, `ENCRYPT`, `SESSION`, `DEPLOY`) lehnt es ab, ebenso Werte, die selbst wie ein Key aussehen, und
Endpunkte über `http`, außer auf diesem Rechner, oder mit Anmeldedaten oder Parametern in der Adresse. Anfragen folgen keiner
Weiterleitung, der Key geht nur an den eingetragenen Host. Der Key steht in keiner Ausgabe und keiner Protokollzeile
(`~/.local/state/okfw/rollen.jsonl`, `$OKFW_ROLLEN_PROTOKOLL` legt eine andere Datei fest; sie enthält weder Auftrag noch
Antwort).

Der Harness bekommt nur eine feste Auswahl an Umgebungsvariablen (`PATH`, `HOME`, `TMPDIR`, `USER`, `LOGNAME`, `SHELL`, `XDG_*`, Sprache und Terminal, Proxy- und
Zertifikatsvariablen, `CLAUDE_CONFIG_DIR`), nicht den Rest der Umgebung; Key und Anmeldung für den gewählten Anbieter setzt der
Aufruf selbst. Jeder Aufruf läuft in einem eigenen Ordner, den nur der Aufrufer lesen darf und der danach gelöscht wird.
`CLAUDE_CONFIG_DIR` geht weiter, weil `claude` sonst seine Anmeldung nicht findet: Eine `settings.json` in diesem Ordner kann den
Endpunkt eines `claude` mit Anmeldung ändern, ohne dass `rollen.py` es sieht.

Exit: 0 ok · 2 Bedienfehler oder Sperre · 3 Harness oder Anbieter gescheitert · 10 Harness `sitzung`.

## Pfade

Ein Pfad führt durch ein Wiki: Stationen in fester Folge, jede mit eigenem Text und einer oder mehreren Karten. Derselbe Pfad
lässt sich lesen und vortragen. Er ist eine gewöhnliche Karte mit `type: pfad` im Ordner `pfade/`; den Typ kennt jedes Wiki, ohne
Eintrag in der `wiki.toml`. Die Oberfläche in `serve` schreibt die Datei, niemand muss sie von Hand anfassen.

```markdown
---
type: pfad
title: Kaffee verstehen
description: In zwanzig Minuten vom Wasser zur Tasse
status: draft
lage:
  was-im-filter-passiert:
    /concepts/wasser.md: {pos: [0, 0, 6, 5]}
    /concepts/mahlgrad.md: {pos: [6, 0, 6, 5], zeigt: absatz}
    text-1: {pos: [0, 5, 12, 2]}
---

# Was im Filter passiert

Zwei Größen entscheiden, wie die Tasse schmeckt.

- [Wasser](/concepts/wasser.md)
- [Mahlgrad](/concepts/mahlgrad.md)

> [!notiz]
> Hier die zwei Tassen zeigen.
```

- **Stationen:** Jede Überschrift erster Ebene beginnt eine. Ein Listenpunkt, der nur aus einem Link auf eine `.md` besteht, setzt
  diese Karte auf die Station. `> [!notiz]` ist eine Sprechernotiz. Anderer Text wird zum Textfeld; eine Zeile `---` trennt zwei
  Textfelder. Text vor der ersten Überschrift ist ein Vorspann und erscheint unter dem Titel des Pfads als erste Station.
- **Lage:** `pos` sind Spalte, Zeile, Breite und Höhe im Raster (`raster`, Standard 12 Spalten); `zeigt` ist `kurz` (Titel,
  Beschreibung, Prüfstand; Standard), `absatz` oder `ganz` (mit Scrollen). Fehlt die Lage, ordnet okfw die Station selbst an. Der
  Schlüssel der Station entsteht aus der Überschrift: Wer sie von Hand umbenennt, verliert nur die Lage (Lint `pfad_lage`);
  `serve` nimmt sie mit.
- **Format:** OKF v0.2 kennt keine Pfade und braucht dafür nichts: `type` ist frei, weitere Schlüssel sind erlaubt, Links zwischen
  Konzepten sind Markdown-Links relativ zur Bundle-Wurzel. Andere Programme zeigen einen Pfad als Text mit Links. `lage` und `raster` sind okfw-Erweiterungen.
- **Im Graphen:** Pfade sind keine Knoten und zählen dort nicht als Verbindung. Eine Karte, auf die nur ein Pfad verweist, bleibt
  für den Lint verwaist. Das Dashboard listet Pfade in einem eigenen Abschnitt.

**Belege.** Der Pfad macht nichts geprüfter, als es ist. Karten werden nur verwiesen, nicht kopiert, und zeigen immer ihren eigenen
Stand: Rahmen und „geprüft“, sonst ein Abzeichen wie „Entwurf“, „Freigabe verfallen“, „Widerspruch“ oder „Abgelöst“; eine fehlende
Karte erscheint als Platzhalter. Eigener Text hat keinen Rahmen und immer den Vermerk „eigener Text“; Fußnoten sind möglich wie auf
jeder Seite. Eine Freigabe des Pfads (`verify`, nur auf der Befehlszeile) deckt seinen Text und die Folge, nie die Karten; Verschieben auf der Fläche
ändert nur das Frontmatter und lässt sie stehen. `pfad pruefen` und der Knopf „Vortrag starten“ zeigen dieselbe Liste; der Knopf
warnt, du entscheidest.

**Sichtbarkeit.** Eigener Text ist nicht geschützt, nur weil die Karten es sind. In einem Wiki mit `[visibility]` braucht ein Pfad
darum einen eigenen Wert: Ohne ihn meldet der Lint einen Fehler, und er fehlt in jeder öffentlichen Ausgabe. Neue Pfade sind zunächst
intern. Im öffentlichen Dashboard entfällt eine interne Karte ohne Spur, auch ihr Titel.

**Vortrag.** Eine Station je Bild im festen Format 16:9. ← → oder Leertaste blättern, Pos1 und Ende springen, F schaltet Vollbild, N die
Notizen, P druckt je Station eine Seite (im Druckdialog „Als PDF sichern“), Esc beendet. „Notizen im eigenen Fenster“ öffnet ein zweites
Fenster fürs eigene Blickfeld; lässt der Browser das nicht zu, stehen die Notizen unter der Bühne. In der Dashboard-Datei stehen keine
Notizen, `serve` holt sie erst beim Start. In der Pfad-Datei selbst stehen sie im Klartext: Wer das Bundle weitergibt, gibt sie mit.

**Export.** `pfad export <pfad>` schreibt nach `build/pfad-<name>.html` (mit `--oeffentlich`: `pfad-<name>-oeffentlich.html`) eine Datei ohne Nachladen aus dem Netz, die nur die Karten dieses
Pfads enthält; Links auf andere Karten werden zu Text. Abzeichen und Datum sind eingefroren: Die Datei weiß nicht, was sich danach ändert.

| Schalter | Wirkung |
|---|---|
| `--notizen` | nimmt die Sprechernotizen mit; nie zusammen mit `--oeffentlich` |
| `--oeffentlich` | nutzt die öffentliche Ansicht; bricht ab bei fehlenden und abgelösten Karten, bei internen Karten (nennt die Stationen) und wenn der Pfad selbst intern ist oder keinen eigenen Wert trägt |
| `--ohne-interne` | öffentlich: interne Karten weglassen statt abbrechen, ohne Spur in der Datei, die Warnung nennt sie |

**Wikis mit Schlagwort.** Karten mit dem Schlagwort aus `entry_tag` und `order` liefern weiter „Zuerst lesen“ im Root-Index. Damit ein Wiki seine
Reihenfolge behält, erscheinen sie unter „Pfade“ auch als Pfad „Zuerst lesen“ mit einer Karte je Station; geschrieben wird dafür nichts
(`pfad pruefen zuerst-lesen`, `pfad export zuerst-lesen`). „Bearbeiten“ in `serve` oder `pfad uebernehmen [--dry-run]` legen daraus `pfade/zuerst-lesen.md` an und
berichten Auffälligkeiten wie `order` als Text. Die Karten bleiben dabei unverändert; ab dann gilt im Dashboard nur die Datei.

## Als Bibliothek

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".claude/skills/okfw"))
import okfw   # braucht PyYAML im laufenden Python

wiki = okfw.Wiki.load(root="/pfad/zum/projekt")      # liest wiki.toml
findings = [f.as_dict() for f in wiki.lint()]
wiki.rebuild()
wiki.new_page("source", "shaw-2024", csl=csl_dict, access="fulltext",
              extra={"fulltext": wiki.fulltext_block("pdfs/x.pdf")["fulltext"]})
wiki.verify("concepts/iso-9001", by="claude-code/claude-opus-5-5")                 # Gegenprüfung
wiki.verify_pages(["concepts/iso-9001"], by="human:jdoe", status="stable", note="Charge 1")
wiki.append_event("enrich", "sources/shaw-2024", "Lizenz aus Crossref")
```

Was mehr als ein Wiki braucht (z. B. `new-source --doi`), kommt als Befehl in den Kern
Projektskripte nutzen den Kern nur noch für wirklich projekteigene
Register und Korpora.

### Feste Schnittstelle

Darauf dürfen sich Anwendungen verlassen, die ein okfw-Wiki für eine eigene Domäne bauen. Alles andere im Kern kann
sich ohne Ankündigung ändern. Neue Namen und neue Parameter mit Standardwert hinten an einer Methode sind Ergänzungen
und berühren keine Anwendung. Ein Bruch ist, was einen Namen unten umbenennt, entfernt oder Parameter umstellt, außerdem
eine neue oder verschärfte Lint-Regel der Schwere `error`, weil sie Anwendungen stoppen kann, die bei Fehlern nichts bauen. Ein Bruch
steht im `CHANGELOG.md` und erhöht die erste Stelle der Version, vor 1.0 die zweite. `tests/test_schnittstelle.py` prüft
die Tabelle gegen den Code.

| Name | Parameter |
|---|---|
| `okfw.VERSION` | Attribut |
| `okfw.now_iso` | |
| `okfw.Wiki` | Attribut |
| `okfw.Page` | Attribut |
| `okfw.Finding` | Attribut |
| `okfw.WikiError` | Attribut |
| `okfw.ConfigMissing` | Attribut |
| `Wiki.load` | `config`, `root`, `start` |
| `Wiki.root` | Attribut |
| `Wiki.bundle` | Attribut |
| `Wiki.cfg` | Attribut |
| `Wiki.types` | Attribut |
| `Wiki.append_event` | `kind`, `page`, `detail`, `by` |
| `Wiki.capture` | `slug`, `url`, `files`, `commit`, `license_name`, `license_url`, `note`, `source_path` |
| `Wiki.fulltext_block` | `path`, `retrieved_from` |
| `Wiki.fundweg_entry` | `art`, `db`, `query`, `von`, `at`, `by` |
| `Wiki.lint` | |
| `Wiki.new_page` | `type_`, `slug`, `title`, `description`, `sources`, `tags`, `access`, `resource`, `csl`, `extra` |
| `Wiki.parse` | `path` |
| `Wiki.rebuild` | `check` |
| `Wiki.rel` | `path` |
| `Wiki.replace_body` | `page`, `body`, `detail` |
| `Wiki.set_fundweg` | `page`, `entry`, `append` |
| `Wiki.set_license` | `page`, `lic`, `detail` |
| `Wiki.set_sources` | `page`, `add`, `remove` |
| `Wiki.verify` | `page`, `by`, `status`, `note`, `force` |
| `Wiki.verify_pages` | `pages`, `by`, `status`, `note`, `force` |
| `Wiki.write_dashboard` | `out`, `public` |
| `Page.path` | Attribut |
| `Page.fm` | Attribut |
| `Page.body` | Attribut |
| `Page.title` | Attribut |
| `Finding.severity` | Attribut |
| `Finding.code` | Attribut |
| `Finding.path` | Attribut |
| `Finding.message` | Attribut |

## Dashboard

`okfw.py dashboard` schreibt eine eigenständige HTML-Datei zum **inhaltlichen Erkunden** des Wikis:
Daten als JSON eingebettet, Layout und Bedienung in reinem JavaScript, keine Bibliotheken aus dem Netz,
in unter einer Sekunde erzeugt. Technische Angaben (Lint-Befunde, Prüfstatus) stehen bewusst nicht darin;
dafür gibt es `lint` und `status`. Ausnahme ist die Freigabe („Zur Freigabe“, verfallene Freigaben), die nur die
normale Ausgabe zeigt. Dateipfade erscheinen nur als Ablageort der Volltexte auf Quellenkarten,
in der öffentlichen Ausgabe nicht. Zeitangaben (Stand, Fundweg) stehen nur als Datum darin,
auch in den eingebetteten Daten, keine Uhrzeiten. Was die Seiten selbst als offen markieren, zeigt die
Übersicht „Offene Stellen“ (`[!todo]` dort eingeklappt). Mermaid ist bewusst nicht eingebettet (mehrere MB
je Datei, kein Wiki nutzt es); Diagramme stehen als Textdiagramm in einem `text`-Block oder als Schaubild (SVG, siehe
[Schaubilder](#schaubilder)).

Die Datei sichert sich selbst: Sie trägt eine Content-Security-Policy ohne Fremdquellen, Skripte nur mit einem Nonce, der
bei jedem Bau neu ist, auch bei `--oeffentlich`. Aus Seiten wird nichts als HTML ausgeführt: rohes HTML,
Attributlisten (`{: …}`) und Links mit einem anderen Schema als `http`, `https` und `mailto` (`javascript:`, `data:` …)
erscheinen als Text, während `HTTP://…` und `<name@host>` normale Links sind. Eine Lizenz-URL ohne Schema oder mit einem
anderen als `http`, `https` und `mailto` wird nicht verlinkt, der Lizenzname bleibt. `dashboard` und `rebuild` ersetzen einen
Symlink am Zielpfad durch eine Datei, statt durch ihn hindurch zu schreiben.

- **Beschriftung:** Das Dashboard nennt den Typ einer Seite „Art“ (Chips, Tabellenspalte, „Farbe: Art“); hier heißt
  er Typ.
- **Überschrift:** `[wiki] title` ohne einen Zusatz „– zuerst lesen“ (der gehört in den Root-Index).
- **Verfallene Freigaben:** Die Kopfzeile nennt ihre Zahl, Vorschau und Leseansicht zeigen an der Seite
  „stable, seit Änderung ungeprüft“ bzw. „Freigabe verfallen“ (nur in der normalen Ausgabe).
- **Überblick:** Kacheln nur für Seiten je Typ, „Fundweg dokumentiert“ und „Lizenz geklärt“.
- **Netzwerkgraph:** Kanten wie im Lint (durchgezogen „verlinkt“ = Link im Text, gestrichelt „zitiert als
  Quelle“ = nur über `sources[]`). Layout nach Fruchterman-Reingold mit schwachem Zug zum Typ-Anker,
  deterministisch (Startwerte aus der Konzept-ID), ohne Überlappung; das größte Netz links, Inseln
  daneben, Seiten ohne Verbindung als Raster darunter. Runde Knoten, Größe = eingehende Verbindungen;
  bewusst klein (Trefferfläche größer als der Punkt).
- **Vorschau und Karte:** Zeigen öffnet eine kurze Vorschau (Typ, Unterart, Titel,
  Kurzbeleg, Beschreibung, erster Absatz, Abschnitte). Ein Klick heftet eine Karte mit der ganzen Seite
  an, zum Scrollen: Text mit APA-Belegen, Literatur, Verknüpfungen; Links öffnen die Zielseite in
  derselben Karte, „Ganze Seite“ führt in die Leseansicht, × oder Esc schließt. Im Netzwerkgraph liegt
  die Karte über der Seitenleiste, im Schichtengraph neben dem Klick. Doppelklick öffnet direkt die
  Leseansicht.
- **Übersichtsseiten:** Die Unterart im Kopf einer Seite, Lizenz und Fundweg einer Quelle sind klickbar und
  öffnen – in Karte oder Leseansicht – eine Übersicht aller Seiten mit diesem Wert, nach Typ gruppiert,
  mit Kurzbeleg oder Beschreibung. „Zurück“ führt zur Ausgangsseite,
  „Im Graphen hervorheben“ markiert dieselben Seiten im Dashboard.
- **Schichtengraph (Crosswalk):** Umschalter „Netzwerkgraph | Schichtengraph“. Ein geschichteter Graph
  nach dem Sugiyama-Verfahren: je Typ eine Spalte, von der Grundlage zum
  Querschnitt (Quellen, Entitäten, Konzepte, Synthesen, Analysen; Typen außerhalb des Profils neben dem
  Typ ihres Ordners), je Seite eine Zeile mit Titel und zweiter Zeile (bei Quellen Kurzbeleg und
  Unterart, sonst Beschreibung oder erster Absatz), Fäden zwischen Seiten verschiedener Spalten.
  Reihenfolge nach Verbindungen (Baryzentren, weniger Kreuzungen) oder alphabetisch; Seiten ohne Faden
  lassen sich ausblenden. Zeigen: Vorschau; Klick: Karte mit der ganzen Seite und hervorgehobene Fäden.
  Der Bereich ist fast bildschirmhoch (Höhe des Fensters minus 80 px, mindestens 560 px) und scrollt in sich.
- **Filter und Farbe:** Typ-Chips blenden aus, Suche durchsucht Titel, Beschreibung, ersten Absatz,
  Kurzbeleg, Aliase, Schlagwörter und Themenbereiche (nur als Suchtext, angezeigt werden sie nicht).
  „Kategorie“ hebt eine Unterart, Lizenzklasse oder Fundweg-Art hervor. „Farbe“ wechselt zwischen Typ und
  Umfang (nur mit `[scope]`).
- **Reihenfolge der Typen:** Typ-Chips, Kachel „Seiten“, Tabelle (Spalte „Art“), Übersichtsseiten, Glossar,
  Offene Stellen und die Spalten des Schichtengraphen stehen einheitlich von der Grundlage zum Querschnitt:
  Quellen, Entitäten, Konzepte, Synthesen, Analysen, danach weitere Profiltypen in der Reihenfolge der
  `wiki.toml`; Typen außerhalb des Profils neben dem Typ ihres Ordners. Die Reihenfolge der `[types.*]`
  in der `wiki.toml` gilt im Dashboard nur noch für das Layout des Netzwerkgraphen (und weiter für den Root-Index).
- **Bewusst nicht enthalten (nicht wieder einführen):** Themenbereiche und
  Schlagwörter erscheinen nirgends (keine Kachel, keine Tabellenspalte, nicht unter „Kategorie“ oder „Farbe“,
  keine Angaben oder Übersichtsseiten an der Seite), keine Übersichtskästen unter dem Graphen, keine
  Kacheln „Verbindungen“ und „Quellenlage“.
- **Tabelle:** alle Seiten mit Beschreibung, Typ, Unterart, Lizenz und Verbindungen, sortierbar.
- **Pfade:** alle [Pfade](#pfade), je mit „Lesen“ (Stationen untereinander, Karten klappen auf, auf dem Telefon einspaltig) und
  „Vortrag starten“. Ohne Pfad fehlt der Abschnitt. Mit `[scope]` folgen Pfade dem Umschalter: Karten außerhalb des gewählten Umfangs entfallen,
  eigener Text bleibt stehen, eine Station ohne verbliebene Karte oder Text entfällt.
- **Textdiagramme:** Codeblöcke ```` ```text ```` (auch `txt`, `plain`, `plaintext`, `ascii`, `tree`,
  `diagram` und unmarkierte Blöcke mit Rahmenzeichen U+2500–U+257F) erscheinen in der Leseansicht als
  Abbildung: heller Grund, JetBrains Mono ohne Ligaturen, Zeilenhöhe 1,45, damit `│ ├ └` senkrecht
  durchgehen; breite Bäume scrollen waagerecht im Kasten, auf schmalen Bildschirmen in 12 px.
- **Glossar:** eingeklappter Abschnitt mit allen Seiten der Typen aus `[dashboard] glossary` (Standard
  Konzepte und Entitäten), A–Z gruppiert, mit Beschreibung und „Auch: …“ aus `aliases`. Suche über Titel,
  Aliase und Beschreibung, Buchstabenleiste springt zur Gruppe, Klick öffnet die Seite in der Leseansicht.
- **Offene Stellen:** eingeklappter Abschnitt mit allem, was die Seiten selbst als offen markieren, je mit
  Seite, Typ und Textanfang; ein Klick öffnet die Leseansicht an der Fundstelle und hebt sie kurz hervor.
  Erfasst werden Kästen `> ⚠️ **Widerspruch:**` (auch als Absatz oder Listenpunkt), ausdrückliche
  Prüfvermerke „zu prüfen“ (am Absatzanfang, in Anführungszeichen, Klammern oder fett/kursiv; nicht im
  Fließtext wie „um zu prüfen, ob“) und Kästen `> [!todo]`. Widersprüche und Prüfvermerke stehen als Liste,
  `[!todo]` eingeklappt je Seite, weil große Wikis über tausend davon haben. Durch Leerzeilen getrennte
  `>`-Kästen bleiben getrennt, auch wo Markdown sie zu einem Zitat zusammenfassen würde.
- **Zur Freigabe:** eingeklappter Abschnitt (nur normale Ausgabe, nur lesend) mit allen Seiten, die ein anderer
  Akteur als ein Mensch (Claude, `process:…`) zum jetzigen Inhalt gegengeprüft hat und die keine gültige
  menschliche Freigabe haben: je Seite Titel (öffnet die Leseansicht), Gegenprüfung mit Akteur, Datum und
  Notiz aus dem Ereignis, „Freigabe verfallen, neu gegengeprüft“, falls zutreffend, die Zahl der Absätze ohne
  Fußnote und der ganze Text wie in der Leseansicht, Absätze und Listenpunkte mit Beleg hervorgehoben. Ist der
  Text länger als rund 1600 Zeichen, stehen zuerst die belegten Aussagen mit dem Text ihrer Fußnoten, der ganze
  Text klappt darunter auf („Ganze Seite · n Absätze ohne Fußnote“). Darunter die Seiten mit verfallener
  Freigabe ohne neue Gegenprüfung, mit Grund, und „Gegenprüfung ohne Prüfwert“ (Lint `verified_unhashed`) mit
  einer Zeile zum Kopieren, die eine neue Gegenprüfung anstößt: `[trust] check_command` und die Seiten, sonst
  eine Bitte an Claude mit Prüfablauf und `verify --by <[wiki] actor>`; kein `verify`-Aufruf. Kennung
  (vorbelegt aus `[trust] human`, sonst mit der häufigsten `human:`-Kennung im Wiki) und Notiz für die Charge
  eingeben, Seiten markieren; die Zeile darunter ist der fertige Aufruf
  `uv run --no-config --quiet …/okfw.py --root … verify <seiten> --by human:<id> --status stable --note …` mit „Kopieren“.
  Pfade unter dem Home-Ordner stehen mit `~`. Ohne solche Seiten fehlt der Abschnitt.
- **Sprungleiste:** unter dem Titel Links zu Pfade, Glossar, Offene Stellen, Zur Freigabe und Alle Seiten, soweit
  vorhanden; ein eingeklappter Abschnitt klappt dabei auf.
- Pfade, Glossar und Offene Stellen folgen dem Umschalter „Umfang“; die Kopfzeile nennt dann auch die Gesamtzahl.
- **Umfang:** Mit `[scope]` steht neben den Typ-Chips ein Umschalter „Umfang: alle | Kern | …“. Er
  blendet Seiten außerhalb des gewählten Umfangs in Netz, Schichtengraph, Tabelle und Pfaden aus, startet mit
  `[scope] default` und merkt sich die Wahl im Browser. „Farbe: Umfang“ färbt nach Wert; abgeleitete
  Quellen erscheinen als Ring, die Tabelle zeigt „(abgeleitet)“.
- **Sichtbarkeit und öffentliche Ausgabe:** Mit `[visibility]` zeigt die normale Ausgabe alles; verborgene
  Seiten tragen in Vorschau, Karte und Leseansicht ein Abzeichen („Intern“, Name aus
  `[labels.<feld>]`), die Tabelle eine Spalte und die Kopfzeile „davon N nicht öffentlich“.
  `dashboard --oeffentlich` schreibt nach `[dashboard] public_path` (Standard
  `build/wiki-dashboard-oeffentlich.html`) und lässt verborgene Seiten vor allem anderen weg: Sie fehlen
  in Graph, Schichtengraph, Tabelle, Leseansicht, Übersichtsseiten, Kennzahlen, Literaturverzeichnis,
  RIS und Vorschauen, ihre Kanten zählen nicht. Ablageorte der Volltexte stehen in der öffentlichen Datei
  bei keiner Quelle. Links auf sie werden zu reinem Text, Belege auf sie
  entfallen samt Fußnote. Typen, Umfänge und lesbare Namen (`[labels]`), die nur verborgene Seiten
  tragen, und das Sichtbarkeitsfeld selbst stehen nicht in der Datei. Die Konsole nennt jede Stelle
  mit `visibility_leak`; geschrieben wird trotzdem. Ohne `[visibility] field` und `hidden` oder ohne bekannte
  Werte (`visibility_values_missing`) bricht `--oeffentlich` mit Exit 2 ab. Was eine öffentliche Seite selbst über eine interne schreibt, bleibt stehen: Der
  Kern entfernt Seiten und Verweise, keine Sätze.
- **Fundweg:** Kachel „Fundweg dokumentiert“ mit dem Anteil der Modellvorschläge, Fundweg als
  Kategorie und Übersichtsseite, auf jeder Quellenkarte der Abschnitt „Fundweg“ (Art, Datenbank,
  Suchstring, Datum, bei Zitatverfolgung ein Link zur Ausgangsquelle).
- **Lizenzen und Nutzungsrechte:** Kachel „Lizenz geklärt“, Lizenzklasse als Kategorie. Jede
  Quellenkarte hat über der Literaturangabe den Abschnitt „Lizenz und Nutzungsrechte“, eingeklappt
  mit der Lizenz in der Kopfzeile. Aufgeklappt: Lizenzname (verlinkt), Klasse, wo der Volltext liegt und ob Git ihn versioniert (nur in der
  normalen Ausgabe), dazu die Rechte als Tabelle
  (erlaubt, mit Auflage, nicht erlaubt, erst fragen). Die Übersichtsseite einer Lizenzklasse zeigt
  zuerst, was diese Klasse erlaubt, dann alle Quellen mit ihr.

Kategorien in `wiki.toml` (`[taxonomy]` bestimmt nur noch, welches Feld die Suche als Themenbereich
mitliest; angezeigt wird es im Dashboard nicht):

```toml
[taxonomy]
field = "domain"                # Frontmatter-Feld für Themenbereiche (eine Liste oder ein Wert)

[types.source]
subtype = "evidenz"             # Feld der Unterart; ohne Angabe das erste von category, format, evidenz, worktype

[labels.domain]                 # lesbare Namen je Feld und Wert, sonst erscheint der Rohwert
qm = "Qualitätsmanagement"
```
- **Belege nach APA 7:** In der Leseansicht wird jede Fußnote `[^id]`, deren `id` auf eine Quellenkarte
  zeigt, zum APA-Klammerbeleg „(Sarkar & Drosos, 2025)“ vor dem Satzzeichen; mehrere hintereinander
  werden zu „(A, 2020; B, 2021)“. Fundstellen aus dem Fußnotentext (S., Art., §, Abs., Kap., Tab. …)
  wandern in den Beleg („…, 2016, Art. 9“), der übrige Fußnotentext in den Tooltip. Der Beleg ist
  immer das Kurzzitat: Organisationen mit Abkürzung stehen als „(BIH, 2026)“, Gesetze und Quellen
  ohne Autor mit `title-short` aus dem `csl`-Block oder, wenn er fehlt, mit einem gebildeten
  Kurztitel: Abkürzung samt Paragraf („§ 33b EStG“), EU-Nummer („Verordnung (EU) 2024/1689“), sonst
  die ersten vier Wörter bis zum ersten Doppelpunkt, Gedankenstrich, Komma oder zur ersten Klammer.
  Die volle Form steht im Literaturverzeichnis und im Tooltip. Am Ende steht
  genau ein Literaturverzeichnis in APA 7 (alphabetisch, hängender Einzug) mit dem `access` jeder
  Quelle und einem Sprung zur Quellenkarte; Fußnotenblock und eine zweite Quellenliste entfallen.
  Quellenkarten zeigen oben Kurzbeleg und Titel; eine Überschrift im Stil „Autor (Jahr): Titel“ und ein
  Zitatblock mit der Literaturangabe am Anfang des Textes entfallen in der Anzeige. Ganz unten steht
  die Literaturangabe (Vollbeleg nach APA 7) ohne eigene Überschrift, mit klickbaren Links, „Kopieren“
  (reiner Text) und „RIS herunterladen“ für Zotero und andere Literaturverwaltungen; Seiten mit Literatur
  bieten „Literatur als RIS“, die Fußzeile „Alle Quellen als RIS“. Das RIS entsteht aus demselben CSL-Eintrag wie die APA-Angabe. Nackte URLs im Text
  werden klickbar (nicht in Code). Die Seiten selbst bleiben unverändert: Im Wiki gilt weiter die
  Fußnote mit `sources[].id`.
  Formatiert wird mit pandoc `--citeproc` und dem Stil aus `[export.csl] style`, sonst
  `templates/apa.csl` (Zotero „APA Style 7th edition“), Sprache `[export.csl] lang` (de-DE): ein Aufruf
  für alle Quellen, rund 0,4 s für 54 Quellen. Denselben Stil nutzen der APA-Block neuer Quellenkarten
  und `[export.bibliography]`. Ohne pandoc gibt es eine einfache Ersatzform.
  CSL je Quellenkarte: der `csl`-Block; sonst aus flachen Feldern `authors`, `year`, `container`,
  `volume`, `issue`, `pages`, `doi` (Autorenlisten „Nachname“, „V. M.“ werden gepaart);
  sonst aus `title`, `resource`, `author` (Pull Requests), Repo-Inhaber aus `pinned.repo` als
  Gruppenautor, `created` als Datum und `retrieved_at` des Rohquellen-Manifests als Abrufdatum.
  Die APA-Angabe ist nur so gut wie diese Daten (fehlende Daten ergeben „o. J.“, gleiche Autor:innen
  ohne Jahr „o. J.-a, -b …“); für exakte Angaben gehört ein `csl`-Block auf die Karte.
- **Leseansicht:** Doppelklick auf einen Knoten, „Seite lesen“ in der Seitenleiste oder „Lesen“ in der
  Tabelle zeigt die Seite wie einen Blogartikel der Website: Überschrift, Beschreibung, Unterart,
  Stand, formatierter Text mit APA-Belegen, `> [!todo]` als Kasten „Offen“,
  `> ⚠️ **Widerspruch:**` hervorgehoben, Literatur nach APA 7, eingeklappt „Verweist auf“ (ohne die
  Literatur) und „Verlinkt von“. Interne Links öffnen die Zielseite in der Leseansicht; nur dann
  erscheint „Zurück“. „Schließen“ oder Esc beendet die Leseansicht. Der Kern rendert das Markdown (Python-Markdown mit `abbr`, `def_list`, `fenced_code`,
  `footnotes`, `tables` und `sane_lists`, ohne `attr_list` und `md_in_html`); rohes HTML und `{: …}` in Seiten werden nur als
  Text gezeigt, nie ausgeführt.

Gestaltung „Streckennetz“: Alabaster-Grund #F4F3ED,
weiße Karten mit 22 px Radius, Charcoal #1A1A1A, Akzent Gelb #FCE53A (dunkel: Blau #3a5efc) nur als Fläche oder Linie,
nie als Textfarbe. Der Kopf ist ein Schild, Pfade sind Linien mit Haltestellen, im Vortrag trägt die Akzentfarbe das
Plakat. Überschriften Bricolage Grotesque, Text Instrument Sans, in der Leseansicht einer Karte Newsreader, Code
JetBrains Mono; der Artikeltext übernimmt `.prose-content`. Die Schriften liegen in `templates/fonts/` und werden
eingebettet (etwa 530 KB je Dashboard), damit die Datei auch ohne Netz gleich aussieht; ein CSS-Kommentar davor nennt
Copyright und Lizenz.

Bewegung kommt nur aus CSS: Übergänge zwischen Stationen im Vortrag, sanfte Einblendungen, Stationen beim Scrollen,
wo der Browser das kann. `prefers-reduced-motion: reduce` schaltet alles ab, beim Drucken läuft nichts.

### Design

Eine `design.css` im Projektordner, oder die Datei aus `[dashboard] design` (Pfad relativ zum Projektordner), bettet der Bau nach der Vorlage in
denselben `<style>`-Block ein, also mit Vorrang vor der Vorlage, in der normalen wie in der öffentlichen Ausgabe. Sie
überschreibt vor allem Variablen; für den Dunkelmodus gilt `:root[data-theme="dark"]`, das mehr Gewicht hat als `:root`.
Gelesen wird die Datei nur beim Bau; sie muss UTF-8 sein und im Projektordner liegen. Der Kern lehnt sie ab (Exit 2 beim Bau,
Lint `design_unsafe`), wenn sie `</style` enthält oder eines von: `@import`; `url(` mit einem anderen Ziel als `#…` oder
`data:` für Schriften und Rasterbilder (`font/…`, `application/font-…`, `image/png`, `image/jpeg`, `image/gif`,
`image/webp`, `image/avif`), auch in einer Zeichenkette und auch ohne schließende Klammer; die Ladefunktionen `image-set(`,
`src(`, `image(`, `cross-fade(`, `element(` und `paint(`; `expression(`, `-moz-binding` und `behavior:` (`scroll-behavior:` und `overscroll-behavior:` sind erlaubt). Kommentare zählen nicht (außer für
`</style`), CSS-Maskierungen werden vorher aufgelöst, und ein Kommentarzeichen in einer Zeichenkette oder in `url(…)` ohne
Anführungszeichen beginnt keinen Kommentar, versteckt also nichts. `<script` im Design wird beim Einbetten zu `\3c script`
(für CSS dasselbe), damit die Daten-Marke, an der `serve` einsetzt, genau einmal in der Datei steht. Die Datei bleibt dadurch
ohne Netz.

Die Variablen in `:root` (hell) und `:root[data-theme="dark"]` (dunkel):

| Variable | Bedeutung |
|---|---|
| `--font-heading`, `--font-body`, `--font-lesen`, `--font-mono` | Schriften: Überschriften, Text, Leseansicht einer Karte, Code |
| `--schild`, `--auf-schild` | Kopf als Schild, Vortragsleiste und die Schrift darauf |
| `--linie`, `--haltestelle` | Linie durch die Stationen und Füllung der Haltestellen |
| `--okfw-dauer`, `--okfw-kurve` | Dauer und Verlauf der Übergänge |
| `--page` | Grund der Seite |
| `--surface` | Flächen: Karten, Felder, Knotenrand |
| `--ink`, `--ink-2`, `--muted` | Text: voll, abgestuft, zurückgenommen |
| `--grid`, `--border` | Linien und Rahmen |
| `--accent`, `--accent-hover`, `--on-accent` | Akzent (Schaltflächen, Hervorhebung) mit Hover und Schrift darauf |
| `--c1` … `--c4` | Datenfarben für Typ (Quelle, Konzept, Entität, Synthese) oder Umfang; `--c1` färbt auch Prüfvermerke |
| `--c-other` | Datenfarbe für alle weiteren Werte |
| `--warning` | Widerspruch, „erst fragen“, internes Abzeichen |
| `--edge`, `--edge-hi` | Kanten im Graphen, normal und hervorgehoben |
| `--wash` | leise Fläche: Textdiagramme, Kästen „Offen“ |
| `--shadow` | Schatten der Knoten |
| `--code-bg`, `--pre-bg`, `--pre-ink` | Code im Text, Codeblock und dessen Schrift |

Die vier Datenfarben bestehen den Palettenvalidator (siehe Farben unten); eigene Werte prüft das Projekt selbst.

Farben: Nach Typ haben die vier gemeinsamen Typen in jedem Wiki dieselbe Farbe (Quelle blau,
Konzept grün, Entität gelb, Synthese magenta), alle Knoten sind rund; Entitäten erscheinen als Ring,
weil Gelb und Grün im Dunkelmodus für manche Formen von Farbenfehlsichtigkeit nah beieinander liegen.
Weitere Typen sind graue Ringe. Nach Umfang (`[scope]`) bekommen die vier
ersten Werte dieselben vier Farben. Für ein Netz zählen alle Farbpaare; diese Vierergruppe besteht
den Palettenvalidator des dataviz-Skills mit `--pairs all` in hell und dunkel, auch auf den
Website-Flächen (#FFFFFF, #161616). Mehr Farben bestehen ihn nicht, deshalb Grau ab dem fünften Wert.
Legende, Beschriftungen und die Tabelle sind die nicht farbliche Alternative. Umschaltbar ist nur
hell und dunkel; ohne gespeicherte Wahl gilt die Einstellung des Systems.

## Bearbeiten im Dashboard

`okfw.py serve [--port N] [--no-open]` zeigt dasselbe Dashboard, baut es aber bei jeder Anfrage frisch und macht die
Karten bearbeitbar. Gedacht für Menschen, die ohne Editor arbeiten: Lesen, Ändern, Belegen, Freigeben und die
Modelleinstellungen geschehen auf einer Seite. Das Programm schreibt eine Adresse mit Präfix und Token ins Terminal
(`http://127.0.0.1:<port>/<präfix>/?t=<token>`) und öffnet sie; der Token setzt ein Cookie (`okfw_sitzung_<hex>`) nur für den
Pfad des Präfixes, und die Seite leitet auf `/<präfix>/` um. Das Präfix ist zufällig und gilt für einen Serverstart: Alle
Routen liegen darunter, jeder andere Pfad ist 404. Cookies kennen keine Ports, ohne eigenen Pfad bekäme das Cookie jeder
Prozess auf derselben Adresse. Beenden mit Ctrl+C.

**Die Dashboard-Datei zum Weitergeben bleibt eine Leseansicht.** `dashboard` und `dashboard --oeffentlich` enthalten
keinen Bearbeitungscode und keine Adresse eines Servers; die Vorlage hat nur ein paar Andockpunkte
(`window.okfwEdit?.…`), die ohne `serve` ins Leere gehen. Bearbeiten (`templates/serve.js`, `serve.css` und die Dateien für Aufträge) wird erst
beim Ausliefern eingefügt.

**Sicherheit.** Der Server bindet nur `127.0.0.1` und kennt einen zufälligen Token je Start. Jede Anfrage prüft den
Host (nur `127.0.0.1:<port>` und `localhost:<port>`, gegen DNS-Rebinding). Schreibende Anfragen verlangen zusätzlich das
Cookie (HttpOnly, SameSite=Strict), den Kopf `X-OKFW`, eine passende `Origin`, `Content-Type: application/json` und
höchstens 2 MB; nur `/api/quelle/datei` nimmt stattdessen PDF oder Markdown mit den Grenzen unter „Quellen aufnehmen“. Die Seite liefert eine Content-Security-Policy ohne Fremdhosts (`connect-src 'self'`, Skripte nur mit
Nonce). Seiten-IDs kommen immer aus dem geladenen Wiki; der Server setzt nie einen Pfad aus einer Anfrage zusammen. Im Wiki
schreibt er nur über den Kern; außerhalb davon schreibt er allein die Nutzerdatei von `rollen.py` und die Designdatei im
Projektordner (beide ersetzen einen Symlink, statt durch ihn zu schreiben), dazu temporäre Ordner und das Protokoll von
`rollen.py`. Die `wiki.toml` schreibt der Server nie. Keys gibt es in keiner Richtung: Die Seite zeigt nur, ob einer da ist,
und den Terminal-Befehl, ihn abzulegen. Scheitert der Bau des Dashboards, zeigt der Browser eine lesbare Fehlerseite mit dem
Grund (das Wiki ist unverändert), nicht JSON oder einen Traceback.

**Karten schreiben nur über den Kern.** Es gelten dieselben Regeln wie auf der Befehlszeile; Akteur ist `human:<kennung>` mit
der Kennung aus den Einstellungen (ohne Kennung antwortet der Server mit 422 und verweist auf Einstellungen → Person). Einstellungen und Design schreibt der
Server selbst, ohne Ereignis.

| Aktion im Dashboard | Wie der Kern es sieht |
|---|---|
| Inhalt speichern | wie `body`: `generated` wird nachgezogen, `stable` fällt auf `draft`, Ereignis `update` |
| Eigenschaften speichern | gültiges YAML, unbekannte Felder und ihre Reihenfolge bleiben Zeichen für Zeichen; `status`, `verified` (mit dem Prüfwert) und `generated` sind geschützt (422) |
| Freigeben, Alle freigeben | wie `verify --by human:<kennung> --status stable`. Fehlt zum jetzigen Inhalt die Gegenprüfung, fragt das Dashboard je Seite nach; erst „Trotzdem freigeben“ gibt frei wie `--force`. Die Seite gilt dann nicht als gegengeprüft: Das Ereignis vermerkt „freigegeben ohne Gegenprüfung“, die Karte trägt die Marke „ohne Gegenprüfung“, der Lint meldet `human_without_check`. Abgelöste Karten lassen sich nicht freigeben |
| Neue Karte | wie `new`; Pflichtfelder und Befunde zeigt der Lint nach dem Anlegen |
| Abbildung einfügen | Befehl `abbildung` (Prüfung und Bereinigung des SVG im Kern); auch als Entwurf eines Modells aus einer Beschreibung |
| Bild erzeugen | Auftrag im Hintergrund, Rolle `bild` über `rollen.py bild`; „Annehmen“ legt eine Variante über `abbildung --rolle bild --anfrage …` ab |
| Vorschlag holen | Auftrag im Hintergrund, Rolle „Bearbeiten per Prompt“ über `rollen.py auftrag`; geschrieben wird erst mit „Annehmen“ oder „Speichern“ |

Nach jedem Speichern läuft `rebuild`; die Antwort enthält die Lint-Befunde zur Seite. Jede Speicheranfrage trägt den
Prüfwert der Datei (`hash`), den der Server beim Lesen mitgibt. Hat sich die Datei seither geändert, antwortet er mit
409 und dem aktuellen Stand; nichts wird überschrieben, die Seite zeigt beide Stände nebeneinander.

**Bearbeitungsansicht.** An jeder Karte und in der Leseansicht steht „Bearbeiten“. Der Reiter *Inhalt* ist ein
Textfeld mit Markdown, einem Umschalter für eine vereinfachte Vorschau und Knöpfen für Fußnote und Abbildung. Der Reiter
*Eigenschaften* zeigt jedes Feld: einfache als Eingabe, zusammengesetzte als YAML-Text, geschützte nur lesend mit
Erklärung; „Ganzes Frontmatter als Text“ schaltet auf einen einzigen Text um. Darunter steht das Feld „Änderung
beschreiben“: „Vorschlag holen“ schickt den Text dieser einen Seite (nie einen Volltext) an das Modell der Rolle
*Bearbeiten per Prompt* und zeigt den Zeilenvergleich alt und neu. Vorher fragt die Seite nach, wohin der Text geht.
Verschwundene oder neue Fußnoten, verschwundene Links und Fußnoten ohne `sources`-Eintrag erscheinen als Warnung. Bei einer
Quellenkarte prüft der Server vorher die Nutzungsrechte: Ist `ki_auswertung` „nicht erlaubt“, lehnt er mit 422 ab, bei „mit
Auflage“ und „erst fragen“ gibt es eine Warnung; hinaus geht nur diese eine Seite, nie ein Volltext. „Annehmen“
speichert den Vorschlag über denselben Weg wie „Speichern“ und antwortet mit 409, wenn sich die Seite seit dem Senden geändert hat. „Ins Textfeld“ setzt ihn nur ins
Textfeld, „Speichern“ ist dann ein eigener Schritt. Das Ereignis nennt Rolle, Harness, Anbieter und Modell. Die Datenklasse des Wikis (`[rollen] daten`) und gesperrte Anbieter beachtet `rollen.py`; die Harness
`sitzung` kann der Server nicht bedienen und meldet das klar.

**Schaubild aus einer Beschreibung.** Der Dialog „Abbildung einfügen“ hat das Feld „Oder beschreiben“. Es läuft über die Rolle *Bearbeiten per Prompt*; hinaus geht nur die
Beschreibung, weder die Seite noch eine Quelle. Das SVG des Modells durchläuft dieselbe Bereinigung wie ein hochgeladenes. Abgelegt wird es erst mit „Annehmen“, ein vorhandenes Bild ersetzt
dieser Weg nie. Das Ereignis nennt das Modell, und die Bildunterschrift erhält „Mit KI erzeugt (Modell)“, auch in der Datei zum Weitergeben. Das Bild ist eine Illustration, kein Beleg. Ob das
eine gesetzliche Kennzeichnungspflicht erfüllt, ist nicht geprüft.

**Bild erzeugen.** Der Knopf neben „Abbildung einfügen“ öffnet einen eigenen Dialog: Name, Titel, „Was soll zu sehen sein?“
und „Wunsch in Worten“. „In Schalter übernehmen“ (oder Eingabe) setzt aus den Worten Format, Tempo, Varianten, Stil, „Ohne“
und Schrift; jeder Schalter bleibt änderbar, Ausdrückliches gilt. Darunter stehen bei jeder Änderung Modell, Anbieter,
Datenklasse, die Erklärungen und Hinweise zum Prompt wie beim Befehl [`bild`](#bilder) und aufklappbar „Was gesendet wird“
ohne Schlüssel; der Server rechnet das ohne zu senden (`POST /api/bild/vorschau`). Der Seed steht im Feld, damit gesendet
wird, was zu sehen war; „Neuer Seed“ würfelt neu. Ist die Rolle nicht bereit oder ein Name vergeben, bleibt „Senden“ aus.
Der Auftrag holt je Variante ein Bild über `rollen.py bild` und zeigt die Bilder nebeneinander, frisch bereinigt als
`data:`. Abgelegt wird erst die angenommene Variante; die anderen verwirft man. Das Ereignis trägt Rolle `bild`, Modell und
die Anfrage mit Seed, eingefügt wird das Bild mit der Unterschrift „KI-generiert mit …“. Die Auftragsdatei enthält die Bilder
bis zur Entscheidung, nie den Schlüssel.

**Aufträge.** Ein Modellaufruf läuft im Hintergrund des Servers: Die Seite bleibt bedienbar, der Auftrag übersteht ein Neuladen, „Auftrag abbrechen“ beendet den
Prozess samt Kindern, und je Rolle läuft einer zugleich. Offene Aufträge stehen unter „Aufträge“ in der Kopfzeile. Je Auftrag liegt eine JSON-Datei unter
`<bundle>/.auftraege/`, mit eigener `.gitignore`, außerhalb von Seiten, Indexen, Lint und jedem Export. Sie enthält die Anweisung und den vorgeschlagenen Text, nicht den
Schlüssel. Endet der Server während eines Laufs, gilt der Auftrag danach als abgebrochen; fertige Vorschläge bleiben und behalten ihre Modellherkunft. Erledigtes fällt
nach 30 Tagen weg. Dateien in diesem Ordner gelten beim Laden als ungeprüft: Was nicht genau die erwartete Form hat, wird übergangen.

**Quellen aufnehmen.** Ein PDF oder Markdown auf „Neue Karte“ ziehen oder eine DOI oder einen Link eingeben. `serve` zeigt zuerst die Rechte wie in `/lesen`: `ki_auswertung` und
`volltext_ablegen` aus der Matrix des Wikis, dazu die Datenklasse und das Modell der Rolle `lesen`. Bei „bedingt“ oder „erst fragen“ braucht die KI-Auswertung einen Beleg, der als `rights` auf die Karte
kommt; ohne Erlaubnis geht nur „Nur Karte ohne Modell“, das nichts sendet und auch mit der Rolle `sitzung` geht. Eine Lizenz, die eine Datei über sich selbst behauptet, ist nur ein Vorschlag.
Fremde PDFs sind ungeprüfter Inhalt: `pdftotext` (Poppler) bekommt die Datei über die Standardeingabe und läuft in der Sandbox des Systems (macOS `sandbox-exec`, Linux `bwrap`), ohne Netz, ohne
Benutzerordner, ohne Schreibrechte, mit Grenzen für Zeit und Speicher. Fehlt eines davon, wird nichts gelesen und es bleibt die Karte ohne Modell. Das Modell bekommt den Text sowie Kennung und Titel der
Konzeptkarten, keine Werkzeuge. Seine Antwort sind Daten: Der Kern übernimmt nur bekannte Felder, setzt sie als reinen Text (keine Links, Bilder, Fußnoten, HTML), prüft jeden Anker am Original, ermittelt die
Fundstelle selbst und streicht, was nicht wörtlich dasteht; beim Annehmen noch einmal am frisch gelesenen Original. Erst „Annehmen“ oder „Alle annehmen“ schreibt, immer als `status: draft`; eine Konzeptkarte nimmt ihre
Quellenkarte mit. Das Original wartet bis zur Entscheidung unter `<bundle>/.auftraege/eingang/` (PDF bis 64 MB, Markdown bis 2 MB) und fällt danach weg, Liegengebliebenes nach einem Tag; bleibt das Ablegen erlaubt, liegt es dann unter
`raw/<slug>/original/`. Ein Link wird nicht abgerufen: Webseiten gehen nur im Container, also bleibt es bei der Karte mit `resource`. Eine Datei ohne Kennung bekommt `urn:sha256:…` als `resource`.

**Pfad.** „Neuer Pfad“ legt einen an, „Bearbeiten“ öffnet ihn: Stationen anlegen, umbenennen, umsortieren; Karten und Textfelder auf dem Raster ziehen
und am Griff in der Größe ändern. Mit der Tastatur: Tab wählt ein Element, Pfeile verschieben, Umschalt und Pfeile ändern die Größe, Eingabe öffnet
Text und Anzeige, Entf nimmt es heraus. „Als Text bearbeiten“ öffnet den gewöhnlichen Editor,
auch für den Modellvorschlag: Nur der eigene Text des Pfads geht hinaus, die Datenklasse des Wikis gilt. Gespeichert wird in einem Zug (`POST /api/pfad`
mit dem Hash aus `GET /api/pfad`): bei fremder Änderung 409, nichts geschrieben; ein Ereignis; der Körper ändert sich nur, wenn sich Text, Folge oder Notizen
ändern. „Bearbeiten“ beim Pfad „Zuerst lesen“ ohne Datei fragt nach und legt erst dann `pfade/zuerst-lesen.md` an.

**`tags` heißen hier „Suchbegriffe“.** Das Feld gehört zum OKF-Format (v0.2, §4.1, optional) und bleibt in den Dateien, auch was die Oberfläche nicht
zeigt. In okfw wirkt es an zwei Stellen: als Marke für „Zuerst lesen“ im Root-Index und als Suchbegriff im Dashboard. Darum zeigen die Eigenschaften die Begriffe als
Eingabe mit Kommas unter diesem Namen; `order` steht nur noch in der Ansicht
„Ganzes Frontmatter als Text“. Weiter erscheinen Schlagwörter in keiner Lese- oder Übersichtsansicht.

**Einstellungen** (Knopf oben, eigene Ansicht):

1. *Rollen*: je Rolle Harness, Anbieter und Modell. Programme, die fehlen, und Anbieter, die nicht zur Harness oder zur
   Datenklasse des Wikis passen, sind ausgegraut und nennen den Grund. Die Modellliste kommt vom Anbieter
   (`/api/modelle`), Freitext bleibt möglich; der Webabruf kennt nur das Modell. Die Seite warnt, wenn Lesen und
   Gegenprüfen dasselbe Modell nutzen. Harness ist der Weg, Anbieter die Abrechnung, Modell das Gehirn.
2. *Anbieter*: mitgelieferte und eigene, Zustand des Keys, der Terminal-Befehl zum Kopieren und das Formular „Eigener
   Endpunkt“ (Name, OpenAI- oder Anthropic-Adresse, Schlüsselname, erlaubte Datenklassen). Entfernen geht nur für eigene.
   `vorbefehl` setzt die Oberfläche bewusst nicht (das wäre ein Programmaufruf), `modelle` ebenso nicht; von Hand
   eingetragen, bleiben beide beim Speichern stehen (siehe [Modellrollen](#modellrollen)).
3. *Person*: die Kennung für `human:<kennung>`.
4. *Design*: Editor für die Designdatei mit der Liste der `:root`-Variablen der Vorlage; Speichern lädt
   neu. Ohne den Schlüssel legt der Server `design.css` an, die der Bau von selbst findet. Die
   Prüfung ist die des Lints `design_unsafe`; zusätzlich lehnt der Server ein Design über 256 KB ab (der Lint kennt die
   Grenze nicht) und ein verlinktes Design (Symlink) mit 422, beim Lesen wie beim Speichern.

Rollen, Anbieter und Person landen in der Nutzerdatei von `rollen.py` (`$OKFW_EINSTELLUNGEN`), nie in der `wiki.toml`.
Gilt für eine Rolle ein Wert aus `[rollen]` der `wiki.toml`, geht dieser vor; die Seite sagt es.

Schnittstelle des Servers (für Tests und Eigenbau; nur mit Cookie, schreibende nur mit den oben genannten Köpfen):

| Anfrage | Zweck |
|---|---|
| `GET /` | Dashboard, frisch gebaut, mit Bearbeiten |
| `GET /api/seite?id=` · `POST /api/seite` | Seite lesen (Text, Frontmatter, Felder, `hash`, geschützte Felder) · speichern (`id`, `hash`, `body?`, `frontmatter?`, `vorschlag?`: `true` nach „Annehmen“, dann nennt das Ereignis die Rolle) |
| `POST /api/neu` · `POST /api/freigabe` · `POST /api/abbildung` | neue Karte (`typ`, `slug`, `titel`, `beschreibung?`) · freigeben (`ids`) · Schaubild ablegen (`name`, `svg`, `ersetzen?`; mit `pruefen` nur die Auskunft mit bereinigter Fassung, Verlusten und freiem Namen; ein vorhandener Name ohne `ersetzen`: 409) |
| `POST /api/auftrag` · `POST /api/auftrag/abbrechen` · `GET /api/auftraege` · `GET /api/auftrag?id=` | Auftrag im Hintergrund starten (`typ`, dazu die Felder des Typs; speichert nichts) · abbrechen · alle offenen · einer mit Vorschlag |
| `POST /api/vorschlag/annehmen` · `POST /api/vorschlag/verwerfen` | Änderung eines Auftrags schreiben (`auftrag`, `aenderung`) · verwerfen |
| `POST /api/quelle/datei` · `POST /api/quelle/klaeren` · `POST /api/quelle/karte` | Datei ablegen · Metadaten und Rechte zu Datei oder Kennung klären · Karte ohne Modell |
| `GET /api/folgen?id=` · `POST /api/abloesen` · `POST /api/loeschen` · `GET /api/papierkorb` · `POST /api/wiederherstellen` | Folgen zeigen · ablösen · in den Papierkorb · Papierkorb · zurückholen |
| `GET /api/pfad?id=` · `POST /api/pfad` · `POST /api/pfad/neu` · `POST /api/pfad/uebernehmen` | Pfad lesen · speichern · anlegen · aus „Zuerst lesen“ übernehmen |
| `GET /api/einstellungen` · `POST /api/einstellungen/rolle` · `…/anbieter` · `…/person` · `GET /api/modelle?anbieter=` | Einstellungen |
| `GET /api/design` · `POST /api/design` | Designdatei |

Alle Routen liegen unter `/<präfix>/`; die Tabelle nennt sie ohne. Fehler kommen als JSON `{"fehler": "Klartext"}` mit passendem
Code: 400 (kein gültiges JSON, kein Objekt, fehlende oder unpassende Felder), 403 (ohne Token, Cookie, Kopf oder Host), 404 (unbekannte Seite, unbekannter Auftrag oder ein Pfad
außerhalb des Präfixes), 409 (Datei geändert), 411 (ohne `Content-Length`), 413 (zu groß), 415 (falscher
`Content-Type`), 422 (vom Kern abgelehnt), 500 (interner Fehler), 502 (Modell oder Literatur-API nicht erreichbar), 504 (Zeitlimit eines Aufrufs überschritten); eine
andere Methode als GET und POST beantwortet die Standardbibliothek mit 501.

## Hooks (Claude Code)

Als Plugin kommen die Hooks aus `hooks/hooks.json` des Plugins; im Projekt ist nichts einzutragen. Alle
laufen über `uv run --script`, `python3` braucht es nicht:

| Ereignis | Aufruf (Plugin) | Wirkung |
|---|---|---|
| SessionStart | `uv run --no-config --quiet --script "${CLAUDE_PLUGIN_ROOT}/skills/okfw/hooks/okf_hook.py" session-start` → `okfw.py status --hook` | Stand des Wikis und geschützte Pfade als Kontext |
| PreToolUse `Write\|Edit\|MultiEdit\|NotebookEdit` | `… okf_hook.py guard` | blockiert Ereignisprotokoll, `raw/`, Exporte, Dashboards (mit `[visibility]` auch das öffentliche), `index.md`/`log.md`, `.papierkorb` und `.auftraege` im Bundle, `[paths] protected`; auf Wiki-Seiten Edit, MultiEdit und Write, die `status`, `verified` oder `generated.modelle` ändern oder deren Frontmatter nicht in einfacher Form steht; bei kaputter `wiki.toml` jeden Schreibzugriff im Projekt außer auf sie selbst |
| PostToolUse `Write\|Edit\|MultiEdit` | `… okf_hook.py post-edit` | hat das Werkzeug den Körper einer Seite im Bundle geändert (Vergleich mit dem Stand davor aus der Antwort von Claude Code; fehlt er, gilt der Körper als geändert): `okfw.py body <seite> --nachziehen` zieht `generated` nach, stable wie bei `body` (neue Seite: `--neu`, Ereignis `create`), und bittet Claude, die Datei vor dem nächsten Edit neu zu lesen; sonst Ereignis `write`/`edit` |
| Stop | `… okf_hook.py stop` → `okfw.py rebuild --hook --befunde` | Projektionen nachziehen; Lint-Fehler gehen an die Sitzung zurück (`decision: block`, je Seite eine Zeile mit Regelname, höchstens 20), höchstens zwei Runden je Sitzung, danach eine Zeile |

Nebenfall **Skill-Ordner** (`~/.claude/skills/okfw`, ohne Plugin): `hooks/settings.example.json` in die
`.claude/settings.json` des Projekts übernehmen. Alle vier Hooks rufen dort wie im Plugin `okf_hook.py` über
`uv run --script` auf, nur mit `$HOME/.claude/skills/okfw` statt `${CLAUDE_PLUGIN_ROOT}/skills/okfw`. Nicht beide
Wege zugleich, sonst laufen die Hooks doppelt.

SessionStart und Stop suchen die `wiki.toml` aufwärts ab dem Projektordner (`$CLAUDE_PROJECT_DIR`), ein Wiki in
einem übergeordneten Ordner gilt also auch für eine Sitzung in einem Unterordner. Der Guard sucht die `wiki.toml`
aufwärts ab der Zieldatei (rund 30 ms, nur Standardbibliothek,
Python ≥ 3.11 wegen `tomllib`, das uv bereitstellt). Er schützt dieselben Pfade, die `status --hook` als
„Nicht direkt bearbeiten“ nennt (`protected_paths` im Kern). Für die Vertrauensfelder vergleicht er die
Schlüssel `status`, `verified` und `generated.modelle` im Frontmatter vor und nach der Änderung als Text (Edit und MultiEdit
werden auf die Datei angewandt, Write bringt den ganzen Inhalt); jede Abweichung wird abgelehnt, nur eine neue Seite darf `status: draft` tragen.
Schreibzugriffe über Bash erfasst er nicht, auch `okfw.py verify` nicht; das bleibt Regel, und dafür gibt es
die Signatur. Bedienfehler (kaputte `wiki.toml`, `requires_core` neuer als der Kern, fehlendes `uv`) melden die Hooks außerhalb des Guard als eine
Zeile (`systemMessage`), ebenso eine Zeitüberschreitung beim Sitzungsstart, beim
Nachziehen und beim Stop. Ohne `wiki.toml` bleiben sie still.

Der Stop-Hook zählt seine Runden je `session_id` in einer Datei im temporären Ordner (`$TMPDIR`), nie im Wiki; ein
fehlerfreier Lint setzt den Zähler zurück. Er blockiert nie bei `stop_hook_active: true`, also höchstens einmal je
Abschluss; die zweite Runde kommt erst beim Abschluss nach dem nächsten Auftrag. Ebenso wenig blockiert er ohne
`session_id`, wenn der Zähler nicht lesbar oder schreibbar ist, und bei Bedienfehlern. Das gilt auch für `claude -p`.
`okfw.py rebuild --hook` allein, wie in der alten Vorlage, blockiert nie.

Ohne `uv` läuft der Guard nicht: Schreibschutz und Schutz der Vertrauensfelder sind dann aus. Er meldet das bewusst
nicht selbst, denn eine Rückfallmeldung im Aufruf (`|| echo …`) käme bei jedem Edit in jedem Projekt, auch ohne Wiki;
den Hinweis auf das fehlende `uv` gibt einmal je Sitzung der SessionStart.
