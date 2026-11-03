# Änderungen

Je Version steht hier nur das Ergebnis der Prüfungen. Protokolle und Berichte hängen als Anhang am Release, die aus der Zeit vor 0.6.0 gesammelt an 0.6.0.

## 0.7.2 · 3.11.2026

Rasterbilder als WebP, auf Wunsch von einem Bildmodell erzeugt, auch aus dem Dashboard; Klammerbelege als Kurzzitate nach APA 7; das Repo ist für Coding-Agenten vorbereitet. Hooks und Profile bleiben wie in 0.7.1.

Geprüft: Nachbau mit echter Claude-Sitzung, alle Testsuiten, Diff seit 0.7.1 mit zwei Korrekturrunden, Screenshots im Container ohne Netz, die Prüfungen vor dem Export. Nicht geprüft im Release-Lauf: ein Aufruf an einen echten Bilddienst (der Dialog lief bis vor das Senden), echtes Safari und Telefon.

### Rasterbilder als WebP, erzeugt mit der Rolle `bild`

- **WebP neben SVG unter `abbildungen/`.** `abbildung <name> --file x.webp` prüft RIFF-Kopf, Länge und Bildblock, lehnt Animation, PNG und JPEG ab und entfernt EXIF, XMP und ICC, nur mit der Standardbibliothek. Das Dashboard bettet das bereinigte WebP als `data:image/webp` ein; jedes andere Bild bleibt Text. Ein Name gilt für genau eine Datei. R11 in `docs/richtung.md` nennt WebP jetzt mit.
- **Befehl `bild`.** Erzeugt über `POST /images/generations` eines OpenAI-kompatiblen Anbieters ein WebP (`output_format` `webp`, rund 100 KB statt 3 MB als PNG). Wünsche in Worten (`--wunsch "breit, Entwurf, ohne Menschen, drei Varianten"`) werden zu Format, Tempo, Negativ-Prompt mit Guidance 4 (dann immer Tempo fein: Guidance mit verkürzten Schritten zerstörte im Test das Bild), Varianten mit Seed, Seed+1 …; krumme Formate werden auf Vielfache von 16 gerundet. Vor dem Senden: Erklärungen, Hinweise zum Prompt und „Was gesendet wird“ ohne Key, mit `--trocken` nur das. Gesendet werden nur zehn geprüfte Felder. Eigene Stile in `[bild.stile]`.
- **Rolle `bild` in `rollen.py`:** nur Harness `direkt` mit OpenAI-Form, ab Werk ohne Modell. `rollen.py modelle <anbieter> --bild` und „Modelle laden“ in den Einstellungen zeigen nur Modelle mit `modality: image`, wo der Anbieter das Feld nennt.
- **Nachweis und Lint.** Das Ereignis eines erzeugten Bildes trägt die ganze Anfrage mit Seed, Modell, Anbieter und Dauer, nie den Key. `figure_caption` verlangt bei erzeugten Bildern keine Fußnote; `figure_generated` verlangt weiter „KI“ oder „generiert“ in der Unterschrift.
- **Dialog „Bild erzeugen“ in `serve`.** Worte in Schalter übernehmen, jede Einstellung änderbar, Erklärungen und „Was gesendet wird“ vor dem Senden (`/api/bild/vorschau`), Varianten als Auftrag im Hintergrund nebeneinander, Annehmen legt nur die gewählte ab und fügt sie mit KI-Unterschrift ein. Dafür `rollen.py bild` und `abbildung --anfrage`.
- Nicht enthalten: Bild-zu-Bild (`/images/edits`).

### Klammerbelege als Kurzzitate

- **Klammerbelege sind Kurzzitate nach APA 7.** Bisher stand bei Gesetzen und Quellen ohne Autor der ganze Titel im Text, bei Organisationen der volle Name, etwa
  „(Einkommensteuergesetz (EStG), § 33b Pauschbeträge für Menschen mit Behinderungen, Hinterbliebene und pflegende Personen, 2009)“. Jetzt steht dort „(§ 33b EStG, 2009)“,
  „(BIH, 2026a)“ oder „(Verordnung (EU) 2024/1689, 2024)“. `csl_item` setzt dafür `title-short`, wenn der `csl`-Block keinen hat; damit trennt citeproc gleiche Kurztitel mit
  a, b. Das Literaturverzeichnis bleibt vollständig.

### Für Coding-Agenten und Beitragende

- **`.claude/CLAUDE.md`, die Anleitung für Coding-Agenten.** Claude Code lädt sie als Projektanleitung; an der Wurzel meldete `claude plugin validate --strict`, dass ein Plugin sie dort nicht lädt. Sie nennt Zweck, Zweige (Feature-Zweige von `main`, PRs gegen `main`, Merge durch die Person oder auf ihre Ansage), den Prüfbefehl, wo Code, Skills, Hooks, Tests und Doku liegen, was ein Agent nie tut, und die Commit-Zeilen für beteiligte Modelle.
- **`docs/richtung.md` mit nummerierten Klauseln R1 bis R14:** was okfw ist und was nicht, je Klausel mit der Stelle, an der es im Repo schon festgelegt ist.
- **`.github/pull_request_template.md`:** Plan, Änderungen, Abweichungen vom Plan, Prüfergebnis, beteiligte Modelle.
- **`tests/pruefen.sh` ist der einzige Prüfeinstieg.** Er führt aus, was die CI bisher in zwei Schritten ausführte, läuft beide Schritte immer zu Ende und endet bei jedem Fehler und ohne `uv` mit Exit ≠ 0. `.github/workflows/tests.yml` ruft nur noch ihn auf.
- **`tests/test_abdeckung.py` prüft die Anleitung:** Jeder Pfad und Befehl, den `.claude/CLAUDE.md` nennt, existiert; der Prüfeinstieg ist ausführbar und scheitert ohne `uv`; die CI ruft nur ihn auf; die Klauseln in `docs/richtung.md` sind lückenlos nummeriert und verweisen auf bestehende Dateien; die PR-Vorlage hat ihre Abschnitte.
- **Beiträge in `docs/entwicklung.md`:** Pull Requests gehen gegen `main` dieses Repos; sie werden ins Arbeitsrepo übernommen und kommen mit der nächsten Version zurück, mit Autor und Nennung im Release.

## 0.7.1 · 2.11.2026

Parlamentsmaterial in `recherche`: die DIP-API des Bundestags, nur lesend. Kern, Hooks und Profile bleiben wie in 0.7.0, im Kern ändert sich nur die Versionsnummer.

Geprüft: Nachbau mit echter Claude-Sitzung, alle Testsuiten, Diff seit 0.7.0, die Prüfungen vor dem Export. Gegen den echten Dienst liefen Aktivitäten, Plenarprotokolle und deren Text. Nicht geprüft: `drucksache-text` und `vorgang` gegen den echten Dienst, `dip-person --name` gegen den echten Dienst, das Verhalten bei 429. Der Bericht hängt am Release.

- **DIP-API des Bundestags in `api.py`, nur lesend.** `suche dip '<endpunkt>?<filter>'`, `dip-person` (Aktivitäten einer Person im Zeitraum) und `dip-dokument` (Protokoll oder
  Drucksache als Text), jeder Abruf mit derselben Protokollzeile. Der Key kommt aus `DIP_API_KEY` oder dem Schlüsselbund (`dip-api-key`) und geht nur im Kopf an den Dienst. Ein Name, der mehrere
  Personen trifft, führt zu keiner Auswahl. Ein Protokolltext, der sich als vorläufig ankündigt, wird benannt. `archivieren` nimmt `nachschlagen/` mit. `api.py` prüft TLS unter macOS zusätzlich
  gegen die Wurzeln des Systems, weil dem Python von `uv` sonst die Wurzel des DIP-Hosts fehlt.

## 0.7.0 · 2.11.2026

Im Dashboard arbeiten: Quellen hineinkippen, Pfade und Vortrag, Modellaufträge im Hintergrund, Schaubilder, Ablösen und Löschen, neue Gestaltung.

Geprüft: Nachbau mit echter Claude-Sitzung, volle Prüfung des Kerns, Selbstreview über 0.7.0, Diff seit 0.6.1. Nicht geprüft: echtes Safari und Telefon, Ziehen aus dem Finder von Hand, der
Druckdialog des Systems, Linux mit `bwrap`. Der Bericht hängt am Release.

- **Quellen hineinkippen in `serve`.** PDF oder Markdown auf „Neue Karte“ ziehen oder eine DOI oder einen Link eingeben. Die Rechte werden wie in `/lesen` geklärt; ohne erlaubte
  KI-Auswertung bleibt „Nur Karte ohne Modell“. PDFs liest `pdftotext` in der Sandbox des Systems ohne Netz und ohne Benutzerordner, ohne Sandbox wird nichts gelesen. Das Modell schlägt Quellenkarte und
  Konzeptkarten mit Fundstellen und Ankern vor; der Kern übernimmt nur bekannte Felder als reinen Text und prüft jeden Anker am Original. Angenommenes landet als Entwurf. Neu: Auftragstyp `quelle`,
  Änderungsarten `quelle` und `seite`, Routen `/api/quelle/datei`, `/api/quelle/klaeren`, `/api/quelle/karte`.
- **In den Testordnern liegen keine Berichte mehr:** Ein Test lehnt in `tests/` jede `.md` außer `angreifer.md` und `selbstreview.md` ab und unter `web-zugriff/scripts/tests/` jede, auch git-ignorierte.
- **Die README ist kurz.** Sie zeigt drei Bilder, Installation und den Einstieg. Alle Funktionen mit Bildern stehen in `docs/rundgang.md`, Voraussetzungen, Tests, Nachbau und Release in `docs/entwicklung.md`.
- **Das Dashboard hat eine neue Grundgestaltung, „Streckennetz“.** Der Kopf ist ein Schild, Pfade sind Linien mit Haltestellen, im Vortrag
  wird die Akzentfarbe zum Plakat, und eine Karte liest sich in einer Antiqua. Die Farben bleiben, neu sind die Schriften Bricolage Grotesque,
  Instrument Sans und Newsreader statt IBM Plex. Hell, dunkel und am Telefon; alles bleibt über `design.css` und den Reiter Design änderbar.
  Wer in einer eigenen `design.css` auf IBM Plex gesetzt hat, bekommt die Ersatzschrift und bettet die Schrift dort selbst ein.
- **Bewegung nur mit CSS.** Übergänge zwischen Stationen und sanfte Einblendungen; `prefers-reduced-motion` schaltet sie ab, auch das Aufblitzen
  beim Sprung zu einer offenen Stelle, das diese Einstellung bisher überging.
- **Automatische Lage folgt der Reihenfolge der Station.** Text rutscht nicht mehr unter die Karten, Karten mit Titel und Beschreibung lassen im
  Vortrag keine leere Fläche, und eine Karte wiederholt den Titel ihrer Station nicht.
- **`design.css` darf `scroll-behavior` und `overscroll-behavior` enthalten.** Die Prüfung lehnte sie irrtümlich wie `behavior:` ab.
- **Schaubild aus einer Beschreibung.** Der Dialog „Abbildung einfügen“ lässt ein Modell ein SVG entwerfen. Hinaus geht nur die Beschreibung, das SVG durchläuft dieselbe Bereinigung wie ein
  hochgeladenes, abgelegt wird es erst beim Annehmen, und ein vorhandenes Bild ersetzt dieser Weg nie. Das Ereignis nennt das Modell (`abbildung --rolle R --modell M`), die Bildunterschrift sagt
  „Mit KI erzeugt“. Neuer Lint-Code `figure_generated`, wenn sie das nicht sagt.
- **Modellaufrufe laufen in `serve` als Aufträge im Hintergrund.** „Vorschlag holen“ blockiert die Seite nicht mehr, übersteht ein Neuladen und lässt sich abbrechen, samt
  Kindprozessen; je Rolle läuft ein Auftrag zugleich. Der Vorschlag liegt als Datei unter `<bundle>/.auftraege/`, außerhalb von Commit, Lint und Export, und behält seine
  Modellherkunft über einen Neustart. „Annehmen“ speichert jetzt direkt, „Ins Textfeld“ tut, was „Annehmen“ bisher tat. Unter „Aufträge“ stehen alle offenen. `POST /api/vorschlag`
  entfällt; an seine Stelle treten `/api/auftrag`, `/api/auftrag/abbrechen`, `/api/auftraege`, `/api/vorschlag/annehmen` und `/api/vorschlag/verwerfen`.
- **Pfade ersetzen den Lernpfad im Dashboard.** Ein Pfad (`type: pfad` in `pfade/`, in jedem Wiki) hat Stationen mit mehreren Karten und eigenem Text auf einem Raster mit
  12 Spalten. Das Dashboard hat nur noch den Abschnitt „Pfade“: „Lesen“ untereinander, „Vortrag starten“ im festen Bild 16:9 mit Vollbild, Tastatur, Sprechernotizen und
  Druck je Station; beides folgt dem Umschalter „Umfang“. Der Abschnitt „Lernpfad“, „Lernpfad starten“ und „Lernpfad · Schritt N von M“ in der Leseansicht entfallen. `serve`
  bearbeitet Stationen, Lage und Text, auch mit der Tastatur, mit Hash und Ereignis. `pfad export` schreibt eine Datei mit nur den Karten des Pfads; `--oeffentlich` stoppt bei
  fehlenden, abgelösten und internen Karten, `--notizen` nie zusammen damit. Karten werden nur verwiesen und zeigen ihren eigenen Stand; eigener Text ist immer als solcher
  gekennzeichnet. Neue Lint-Codes `pfad_*`. Karten mit Schlagwort und `order` liefern weiter „Zuerst lesen“ im Root-Index und erscheinen als Pfad „Zuerst lesen“; erst
  „Bearbeiten“ oder `pfad uebernehmen` legt die Datei an.
- **`tags` heißen in `serve` „Suchbegriffe“:** Das Feld bleibt im OKF-Format, wirkt in okfw aber nur als Marke für „Zuerst lesen“ im Root-Index und in der Suche. Die
  Eigenschaften zeigen es als Eingabe mit Kommas; `order` steht nur noch in der Textansicht des Frontmatters.
- **SVGs lassen sich im Dashboard wirklich einfügen:** Der Standardkopf `<!DOCTYPE svg PUBLIC …>` aus Illustrator oder Graphviz wird übergangen, `width` und
  `height` mit absoluter Einheit (`10mm`, `72pt`) ergeben das `viewBox`. Einfache `<style>`-Regeln samt `class` wandern in `style`-Attribute, bisher blieb so
  eine Datei schwarz. `<switch>` aus draw.io behält den Ersatztext. Der Dialog prüft zuerst, zeigt die bereinigte Fassung und was entfällt, und fragt bei einem
  vorhandenen Namen: ersetzen (mit den Seiten, die sie zeigen) oder unter einem freien Namen ablegen. Eine Datei, die nicht UTF-8 ist, meldet er, statt
  Umlaute still zu ersetzen.
- **Freigeben ohne Gegenprüfung im Dashboard:** Fehlt die Gegenprüfung zum jetzigen Inhalt, fragt das Dashboard je Seite nach, statt
  abzulehnen; „Trotzdem freigeben“ gibt frei wie `verify --force`. Ohne diese Antwort gibt der Server nichts frei. Das Ereignis vermerkt
  „freigegeben ohne Gegenprüfung“, die Karte trägt eine Marke, der Lint meldet `human_without_check`. Die Meldungen nennen jetzt, was fehlt
  und was sich tun lässt. Das Feld „Bearbeiten“ in der Seitenleiste ist weg, der Knopf an der Karte bleibt.
- **Karten ablösen und löschen:** `deprecate` setzt `status: deprecated` und auf Wunsch `superseded_by`; `delete` zeigt die Folgen (Belege, Fußnoten,
  Links, Rohquelle) und legt die abgelöste Karte erst mit `--yes --by human:<id>` in den Papierkorb `<bundle>/.papierkorb/`; `restore` holt sie zurück.
  Belege sperren bis `--belege-brechen`, die Rohquelle bleibt ohne `--mit-rohquelle` liegen. Dasselbe im Dashboard mit Rückfrage und Reiter „Papierkorb“; abgelöste Karten lassen sich dort nicht freigeben.
  Neue Lint-Regel `superseded_missing` (Warnung). Der Guard-Hook sperrt den Papierkorb für Edit und Write.
- **Modellherkunft bleibt am Text:** `generated.modelle` im Frontmatter nennt je Modell Harness, Anbieter, Modell, Rolle, Art, den
  Menschen oder Akteur, der den Text eingebracht hat, Zeitpunkt und Prüfwert. Der Block überlebt Übernehmen, Freigeben, Export und
  späteres Bearbeiten und wird nur länger. `generated.by` und `verified` sagen weiter, wer zuletzt geändert und wer freigegeben
  hat. Bisher stand nach dem Übernehmen eines Vorschlags nur `human:<kennung>` im Frontmatter, das Modell nur im
  Ereignisprotokoll. Das Dashboard zeigt die Herkunft sichtbar (Marke, Zeile über dem Text, Kopfzeile) und führt sie in seinen
  Daten, die öffentliche Ausgabe ohne Kennung einer Person. Ob das eine gesetzliche Kennzeichnungspflicht erfüllt, ist nicht geprüft.
- **`body --rolle <rolle> [--modell M]`:** vermerkt, dass der Text auf dem Entwurf einer Rolle beruht; `/lesen` nutzt das.
- **Standard-Akteur nennt kein erfundenes Modell mehr:** `init` schreibt `claude-code/unknown` statt `claude-code/claude-opus-5-5`.
  In Claude Code liest der Hook das Modell aus dem Transkript der Sitzung, für Subagenten aus deren Transkript; das gilt auch für
  einen fest eingetragenen `claude-code/…` oder `human:…`, denn diesen Schreibzugriff hat die Sitzung gemacht. Ein gesetztes `WIKI_ACTOR` und andere Akteure bleiben. Bestehende `wiki.toml` ändern
  sich nicht.
- **Neue Lint-Warnungen:** `modell_unbekannt`, `modelle_invalid`, `modell_herkunft_fehlt` (im Ereignisprotokoll vermerkt, im Frontmatter
  entfernt). Der Guard lehnt Write und Edit ab, die `generated.modelle` ändern.
- **Kern zerlegt, ohne neues Verhalten:** `okfw.py` bleibt Eingang und Namensraum (Aufruf, Inline-Metadaten, `okfw.<name>`), der Code liegt
  nach Aufgaben in `okfw_kern/`. SVG und CSS (`bereinigung`), YAML und Frontmatter (`frontmatter`) und die öffentliche Ansicht
  (`wiki_sicht`) lassen sich einzeln prüfen. Die Klasse `Wiki` setzt sich aus Teilen zusammen; alle Methoden bleiben an ihr.

## 0.6.1 · 2.11.2026

Aufräumen, Kern, Hooks und Profile bleiben wie in 0.6.0.

Geprüft: alle Testsuiten und die Prüfungen vor dem Export; kein neuer Nachbau, weil Kern, Hooks und Profile unverändert sind. Die Container-Tests liefen für diese Änderung nicht erneut.

- **Berichte am Release statt im Repo:** Nachbau-Protokolle, Reviews und Selbstreviews liegen nicht mehr unter `tests/`, sondern als Anhang am Release 0.6.0. README und CHANGELOG verweisen entsprechend. Die Container-Tests von `web-zugriff` schreiben ihr Protokoll nach `~/.claude/web-zugriff-protokoll/`, nicht mehr neben das Skript.
- **Drei Hinweise ergänzt:** Die Beschreibungen von `okfw` und `recherche` nennen, wofür sie nicht gedacht sind; `konfiguration.md` sagt, dass eine allein auf § 44b UrhG gestützte Rohquelle zu löschen ist, wenn sie für das Text und Data Mining nicht mehr erforderlich ist.

## 0.6.0 · 1.11.2026

Ein Plugin, Bearbeiten im Dashboard, Modellrollen, Schaubilder, Design.

0.5.0 wurde nie veröffentlicht; sein Inhalt steht unten und kommt mit dieser Version.

Geprüft: Nachbau 46 von 46 mit echter Claude-Sitzung, volle Prüfung des Kerns, Selbstreviews über 0.5.0 und 0.6.0.

Das README nennt die Zweckbestimmung: okfw ist ein allgemeines Werkzeug und kein Medizinprodukt.

Brüche im Sinne der festen Schnittstelle: drei neue Lint-Regeln der Schwere `error` (`figure_missing`, `rollen_unknown`,
`design_unsafe`); `new source --doi/--pmid` endet bei Rückzug oder Bedenken mit Exit 3 und ohne Karte.

Für bestehende Wikis (gegenüber 0.4.0) ändert sich Folgendes:

- `{: …}` in Seiten wirkt nicht mehr. Das Dashboard zeigt die Zeichen als Text, Attribute entstehen daraus nicht.
- Eine `design.css` im Projektordner wird jetzt von selbst eingebettet. Enthält eine vorhandene Datei Fremdes, etwa
  `url(http…)` oder `@import`, endet der Bau mit Exit 2, und Lint meldet `design_unsafe`: die Datei bereinigen oder umbenennen.
- Pfade der `wiki.toml`, in die der Kern schreibt, müssen im Projektordner liegen, auch über Symlinks; sonst Exit 2. Eine
  Seitendatei, die ein Symlink aus dem Bundle hinaus ist, gilt nicht mehr als Seite (Lint `link_leaves_bundle`).
- Ein `order` auf einer Einstiegsseite, das keine Zahl ist (`order: "2"`, `order: abc`), meldet Lint jetzt als Warnung `order_invalid`;
  der Editor im Dashboard schreibt `order` als Zahl.

Zuletzt dazugekommen:

- **web-zugriff liest PDFs:** `web-recherche --pdf <URL>` holt ein freigegebenes PDF im gehärteten Container hinter
  demselben Proxy und liefert den Text je Seite samt SHA-256, ohne Modell und ohne Token. Bisher sah der Agent bei
  PDFs nur Binärdaten. Die Host-Prüfung bleibt unverändert und prüft zusätzlich die Seitenmarken. Das Image enthält
  dafür pdf.js und baut sich beim nächsten Lauf neu.
- **Lernpfad folgt dem Umschalter „Umfang“:** Kacheln, „Lernpfad starten“, Schrittzählung und weiter/zurück in der Leseansicht
  gelten für die Seiten des gewählten Umfangs, durchnummeriert ab 1; die Kopfzeile nennt die Gesamtzahl. Bisher zeigte der
  Lernpfad bei jeder Stellung alle Seiten, während Glossar, offene Stellen und Tabelle schon filterten. Wikis ohne `[scope]`
  sehen keinen Unterschied.
- **Abgebrochene API-Antworten:** `http_lesen` versucht eine abgebrochene Verbindung bis zu dreimal.

Ein Plugin:

- **Recherche, Lesen, Webabruf kommen mit:** Skills `recherche` (mit dem früheren `/quelle` als Schritt „Aufnehmen“), `lesen` (mit
  dem früheren `/pruefen` als Pflichtschritt, einzeln `/lesen --pruefen`) und `web-zugriff`, Agenten `fund-api`, `fund-tooluniverse`
  und `beleg-pruefer`. **`/quelle` und `/pruefen` entfallen.** Der Container ist optional und wählt seine Laufzeit selbst.
- `recherche/scripts/api.py`: PubMed, Europe PMC, Crossref, OpenAlex, Semantic Scholar, arXiv und der EU-Cellar (SPARQL) mit
  Protokoll je Abruf, Paging bis 2000 Treffer, gzip, klare Abbrüche, optionale Keys (`NCBI_API_KEY`, `S2_API_KEY` auch als
  `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_API_KEY`), `abstract --doi|--pmid`. Keys erscheinen in keinem Protokoll.

Bearbeiten im Dashboard:

- **`serve [--port N] [--no-open]`:** dasselbe Dashboard als lokale Seite, nur auf diesem Rechner und mit Token: Inhalt und
  Eigenschaften bearbeiten, Vorschau, Vorschlag eines Modells per Prompt mit Vergleich, Freigabe, neue Karten, Abbildungen,
  Einstellungen (Rollen, Anbieter, Person, Design). Karten, Freigaben und Abbildungen werden nur über den Kern geschrieben,
  also mit Lint, Ereignis und `generated`; Einstellungen und Design schreibt der Server selbst. Die Datei von `dashboard` zum
  Weitergeben bleibt eine Leseansicht ohne diesen Code.

Aufnahme (vorher Wünsche im Skill `recherche`, jetzt deterministisch im Kern):

- **Rückzugsprüfung vor dem Schreiben:** `new source --doi` und `--pmid` fragen Crossref (`updated-by`, `update-to`; die
  Antwort des Metadatenabrufs wird wiederverwendet) und PubMed (Publikationstyp „Retracted Publication“, Verweise
  `RetractionIn`, `ExpressionOfConcernIn`, `ErratumIn`, `CorrectedandRepublishedIn`). Bei Rückzug oder Bedenken Exit 3, keine
  Karte, die Meldung nennt Art, Hinweis-DOI und bei PubMed die Hinweis-PMID; `--trotz-rueckzug` legt sie mit Vermerk an. Sonst
  trägt die Karte `rueckzug: {geprueft, via, ergebnis}` mit `ergebnis` `keiner`, `korrektur`, `nicht_geprueft`, mit
  `--trotz-rueckzug` auch `bedenken`, `zurueckgezogen` und, wenn die DOI selbst der Hinweis auf eine andere Arbeit ist
  (`update-to`), `rueckzugsnotiz` oder `bedenkennotiz`; bei Netzfehler Warnung statt Abbruch. Neuer Schlüssel
  `[sources] retraction_field`. Karten mit PubMed-Hinweis oder als Hinweis selbst tragen eine ⚠-Zeile unter dem APA-Block wie
  bei Crossref.
- **`new source --aus-lauf LAUF --kandidat N`** und **`fundweg <seite> --aus-lauf LAUF --kandidat N [--add]`** lesen
  `kandidaten.json` der Recherche und setzen je Fund einen Fundweg mit echtem Zeitpunkt, `db`, `query` und `by`. Vorher
  vergleicht der Kern die Kennungen von Kandidat und Karte: eine andere Arbeit ist Exit 2, ohne gemeinsame Kennung steht ein
  Hinweis „Zuordnung nicht geprüft“. Ein Fund, dessen `weg` selbst eine Fundweg-Art ist, behält sie (`zitatverfolgung` nur mit
  `von`); `--add` hängt Gleiches nicht doppelt an.
  Neu `Wiki.set_fundwege` (ein Schreibvorgang für mehrere Einträge) und `Wiki.fundwege_aus_lauf`; `set_fundweg` bleibt.
- **`new source --fundweg … --at … --by …`** für den echten Suchzeitpunkt.
- `body` zieht `generated.at` nach (war schon so, jetzt durch Tests belegt).

Modellrollen:

- **`rollen.py`:** je Arbeit (`lesen`, `gegenpruefen`, `bearbeiten`, `webabruf`) Harness, Anbieter und Modell. Harnesses `sitzung`,
  `claude`, `opencode`, `direkt`, `container`; ab Werk Anthropic, OpenAI, OpenRouter und Ollama, dazu eigene Endpunkte mit OpenAI- oder
  Anthropic-kompatibler Schnittstelle. Worker haben keine Werkzeuge. Keys nur aus Umgebung oder Schlüsselbund, nie in Dateien oder
  in der Oberfläche. Ohne Einrichtung läuft alles über die Claude-Code-Anmeldung. Ab Werk liest `sonnet` und prüft `opus`
  gegen, damit der Werk-Standard seine eigene Warnung nicht auslöst. Der Webabruf läuft fest über Anthropic.
  Befehle `anbieter`, Option `--wiki`, `--json` bei `zeige`, `modelle` und `auftrag`.
- `[rollen] daten` und `sperre` sind bekannte Schlüssel der `wiki.toml`; die Tabellen je Rolle (`[rollen.lesen]` …) bleiben
  frei. Lint `rollen_unknown` (Fehler) bei einer Datenklasse außerhalb von `oeffentlich`, `intern`, `personenbezogen` oder
  einer `sperre`, die keine Liste ist. Doku „Modellrollen“ in `docs/konfiguration.md`.

Schaubilder:

- **SVG unter `<bundle>/abbildungen/<name>.svg`**, eingebunden als `![Titel](/abbildungen/name.svg)` mit der Zeile
  `*Abb.: …[^id].*` darunter. Das Dashboard bettet sie bereinigt inline ein (`<figure>`, `<figcaption>`, Allowlist für
  Elemente und Attribute, nur Standardbibliothek, ids je Abbildung mit Präfix); fehlend oder abgelehnt: „Abbildung nicht
  eingebunden: <name>“. Andere Bilder werden nie geladen, sie erscheinen als Text. Die öffentliche Ausgabe bettet nur Abbildungen
  öffentlicher Seiten ein.
- Befehl **`abbildung <name> --file x.svg [--ersetzen]`**; über 200 KB, nicht UTF-8, `<!DOCTYPE`/`<!ENTITY`, ungültiges XML
  oder weder `viewBox` noch `width` und `height` als Zahlen ergeben Exit 2 (aus `width` und `height` wird das `viewBox` gebildet).
- Lint **`figure_caption`** (Warnung), **`figure_missing`** (Fehler), **`figure_rights`** (Warnung: „übernommen“ in der
  Unterschrift, Recht „Abbildung“ der belegten Quelle nicht „erlaubt“, also mit Auflage oder erst fragen). Der Ordner
  `abbildungen/` zählt nicht als Seiten und nicht als Bereich im Root-Index; hat ein Typ ihn als `folder`, bleiben dessen Seiten
  Seiten.

Design:

- **`design.css` im Projektordner** (oder die Datei aus `[dashboard] design`) wird nach der Vorlage eingebettet. `@import`, `url(` auf anderes als
  `#…` oder `data:`, die Ladefunktionen `image-set(`, `src(`, `image(`, `cross-fade(`, `element(` und `paint(`, `expression(`,
  `-moz-binding`, `behavior:`, `</style` und eine Datei, die nicht UTF-8 ist, ergeben Exit 2 beim Bau und den Lint
  `design_unsafe` (Fehler). Die `:root`-Variablen mit Bedeutung stehen in `docs/konfiguration.md`.

Sicherheit (eine Prüfung hat den Stand vor dem Release angegriffen; je Punkt: was passieren konnte, was jetzt gilt):

Dashboard und Seiten:

- Eine fremde Karte konnte über Attributlisten (`{: …}`) oder eingebettetes HTML Attribute und damit Skript in die Leseansicht
  bringen. Attributlisten und Markdown in HTML sind abgeschaltet; `{: …}` bleibt Text.
- Links in Seiten, Literaturangaben und Lizenz-URLs konnten Schemata wie `javascript:` oder `data:` tragen. Ein Link bleibt nur
  mit `http`, `https` oder `mailto` (auch `HTTP://…` und `<name@host>`); sonst erscheint er als Text. Eine Lizenz-URL ohne oder
  mit einem anderen Schema wird nicht verlinkt, der Lizenzname bleibt.
- Die Dashboard-Datei zum Weitergeben hatte keinen Schutz im Browser. Sie trägt jetzt eine Content-Security-Policy ohne
  Fremdquellen, Skripte nur mit einem Nonce, der bei jedem Bau neu ist, auch bei `--oeffentlich`.
- Tief verschachteltes YAML oder Markdown, eine Seite, die nicht UTF-8 ist, und kaputte `index.md` oder `log.md` konnten Lint
  oder Bau abbrechen. Es sind jetzt Lint-Befunde (`frontmatter_invalid`, `projection_stale`), der Bau läuft weiter.
- `new … --set status=stable --set 'verified=[…]'` konnte eine Karte anlegen, die als durch einen Menschen freigegeben galt,
  ohne dass jemand sie gesehen hat. `--set` nimmt `status` und `verified` nicht mehr an (Exit 2, keine Karte); beide setzt
  nur die Freigabe (`verify`).
- `[trust] check_command` aus einer womöglich fremden `wiki.toml` stand als fertige Zeile zum Kopieren in „Zur Freigabe“; eine
  Shell-Zeile wie `curl … | sh` hätte sich so in die Freigabe schleichen lassen. Das Dashboard übernimmt nur Slash-Befehle
  (`/befehl [--option] …`), jeder andere Wert wird ignoriert.

Design und Schaubilder:

- Ein Design konnte Fremdes über CSS-Ladefunktionen, Kommentarzeichen in Zeichenketten oder CSS-Maskierungen nachladen, und
  `<script` darin die Daten-Marke der Seite stören. `design_unsafe` liest CSS jetzt wie der Browser und lehnt `src(`, `image(`,
  `cross-fade(`, `element(`, `paint(`, `url(` in Zeichenketten und ohne Ende, `expression(`, `-moz-binding`, `behavior:` und
  Dateien ab, die nicht UTF-8 sind; `<script` wird beim Einbetten maskiert.
- Ein Schaubild konnte über Werte mit `\` oder `/*`, über `style` (etwa eine Position außerhalb der Zeichnung) oder über
  `transform` am Wurzelelement die Seite überdecken, über verschachtelte `<use>` den Speicher füllen. Solche Werte verwerfen das
  Attribut, `style` bleibt auf Zeichen-Eigenschaften gekürzt (die Meldung nennt `Stil <name>`), `transform` an der Wurzel entfällt,
  die Zahl der Elemente zählt nach dem Auflösen aller `<use>`, und es gilt nur UTF-8 (mit oder ohne BOM).

Dateien und Pfade:

- Eine Seitendatei, ein Ereignisstrom, ein Schaubild oder ein Pfad aus der `wiki.toml` konnte über einen Symlink oder `..`
  außerhalb des Wikis lesen und schreiben lassen. Symlink-Seiten, die nicht ins Bundle führen, sind keine Seiten mehr
  (`link_leaves_bundle`; `body`, `verify` und Verwandte enden mit Exit 2). `abbildung` und `new` lehnen Symlink-Ziele und
  Ordner außerhalb ab, ein Ereignisstrom als Symlink aus dem Projekt ist Exit 2, und Pfade der `wiki.toml` müssen im
  Projektordner liegen (`--out` bleibt frei). `rebuild` und `dashboard` ersetzen einen Symlink am Ziel, statt durch ihn zu
  schreiben.
- Eine fremde `wiki.toml` konnte über `[types.X] template` mit `..` oder einem absoluten Pfad eine Datei von außerhalb in neue
  Karten holen. Eine Vorlage muss jetzt neben der `wiki.toml` oder im Skill liegen, sonst Exit 2.
- Ein feindliches `manifest.json` einer Rohquelle konnte `lint` abbrechen (ungültiges JSON, Eintrag ohne `path`) oder den Kern
  Dateien außerhalb des Projekts lesen und hashen lassen (`path: ../../x`, ebenso `raw_packet` und `fulltext.path`). Der Lint
  meldet solche Pakete jetzt als `raw_missing`, `raw_tampered` oder `fulltext_absent`, ohne etwas außerhalb zu öffnen; ein
  gültiges Paket bleibt befundfrei, und das Dashboard baut auch mit kaputtem Manifest.
- Ein Seitenname, der mit `-` beginnt, galt beim Nachziehen nach einem Write als Option des Kerns; der Aufruf endete mit
  Bedienfehler oder wirkte auf etwas anderes als die gemeinte Seite. Der Hook übergibt Seiten erst hinter `--` an den Kern.

- Eine `uv.toml` oder ein `[tool.uv]` im geklonten Wiki konnte den Paket-Index umlenken, und so lief fremder Code schon
  beim Sitzungsstart. Hooks, Kern, `api.py` und alle Befehle in der Doku rufen `uv run --no-config` auf.
- Bei kaputter oder ungültiger `wiki.toml`, oder wenn der Guard selbst ausfiel, galt kein Schutz mehr, und `[paths]
  events` konnte an jede Datei anhängen. Der Guard lehnt jetzt das Schreiben im Projekt ab, außer auf die `wiki.toml`
  selbst; Pfade müssen im Projekt liegen, Schreibpfade nicht in `.git` oder `.claude`.
- `status` und `verified` ließen sich am Guard vorbei setzen: in Anführungszeichen, in Flussform, eingerückt, doppelt in
  einem mehrzeiligen Wert, mit `\r\n`, über einen Edit, der die Seite anlegt, oder über eine andere Groß- und
  Kleinschreibung des Pfads. Solches Frontmatter wird abgelehnt, Pfade werden ohne Groß- und Kleinschreibung verglichen.
- Eine Karte als Symlink auf eine andere bekam deren Vermerk und Text; `verify`, `body` und `serve` lehnen sie ab.
  Ereigniszeilen, die keine Objekte sind, brachen `status`, `rebuild` und den Stop-Hook ab und werden nun übergangen;
  in den Sitzungskontext geht nur noch ein gültiger Zeitstempel.
- Eine fremde `kandidaten.json` konnte einen zweiten Befehl in die kopierbare `fundweg`-Zeile bringen, Symlinks im
  Lauf-Ordner das Schreiben aus ihm hinauslenken. Fremdwerte werden immer maskiert, Fehlformen enden mit Text, und
  Symlinks werden nicht beschrieben. Ein Kandidat ohne `nur_websuche` gilt als nur aus der Websuche.
- Ein Anhang (`rollen.py --anhang`) als Symlink, etwa auf einen Schlüssel, ging an den Modellanbieter. Symlinks und
  Dateien über 16 MB werden nicht gesendet.
- `web-recherche` schrieb URLs und Suchanfragen des Agenten in den Kopf, also außerhalb des als unvertrauenswürdig
  markierten Bereichs. Sie stehen jetzt hinter der Marke, bereinigt. `//host` zählt als URL, nachgebaute Marken fallen
  weg, und eine ungültige Abruf-URL bricht den Prüfpfad nicht mehr ab.

`serve`:

- Das Cookie ging an jeden Prozess auf derselben Adresse, gleich welcher Port. Die Start-URL ist jetzt
  `http://127.0.0.1:<port>/<präfix>/?t=<token>`, alle Routen liegen unter dem zufälligen Präfix, das Cookie
  (`okfw_sitzung_<hex>`) gilt nur für diesen Pfad, jeder fremde Pfad ist 404.
- `/api/design` lehnt ein verlinktes Design mit 422 ab; Nutzerdatei und Design werden ersetzt, nicht durchgeschrieben. Scheitert
  der Bau des Dashboards, kommt eine lesbare Fehlerseite statt JSON; im Klartext stehen dort nur Meldungen des Kerns und von `serve`,
  jede andere Ausnahme nennt nur ihren Typnamen und „Einzelheiten stehen im Terminal“ (stderr bleibt vollständig).
- Bei einem unerwarteten Fehler oder einem Zeitlimit beim Modellaufruf ging `str()` der Ausnahme an den Browser und konnte
  Befehlszeile, Pfade oder Schlüssel zeigen. Der Browser sieht nur „Interner Fehler: <Typname>“, beim Zeitlimit 504 mit einem
  klaren Satz; die Einzelheiten bleiben im Terminal. `[dashboard] design` in einem fehlenden Ordner ist 422 mit Hinweis auf
  den Ordner statt 500.
- Ein Frontmatter mit doppeltem Schlüssel ließ sich speichern; bei der nächsten Textänderung wurde der Text geschrieben,
  das Zurücksetzen des Status scheiterte aber, und eine freigegebene Karte blieb mit neuem Text `stable`. Doppelte und
  Merge-Schlüssel gelten jetzt als ungültig, Text und Frontmatter bleiben bei einem Fehlschlag zusammen, und das Dashboard
  speichert kein Frontmatter, dessen geschützte Felder der Kern nicht mehr nachziehen könnte.
- `url(data:…)` im Design ließ jeden Medientyp zu, auch `text/html` und SVG, das so an `clean_svg` vorbeikam. Erlaubt sind
  nur noch Schriften (`font/woff`, `woff2`, `ttf`, `otf`, `sfnt`, `collection`, auch als `application/(x-)font-…`) und
  Rasterbilder (`png`, `jpeg`, `gif`, `webp`, `avif`).
- Ein `use` behielt nach dem Entfernen eines fremden Verweises sein Element, und `href` nahm jeden Text nach `#`. Ein `use`
  ohne Verweis entfällt jetzt, `href` zeigt nur auf eine gültige id.
- Textfelder eines Anbieters durften Steuerzeichen und Zeilenumbrüche enthalten, die in Terminal und Oberfläche landeten;
  sie werden abgelehnt. Von mehreren Host-Köpfen galt der erste; jetzt ist genau einer erlaubt.
- Titel, Lizenzen und andere Kartenfelder aus einem fremden Wiki konnten mit ESC-Sequenzen oder OSC-8-Links ungefiltert ins
  Terminal gehen. Der Kern ersetzt Steuerzeichen (außer Tab und Zeilenumbruch) in seiner Ausgabe durch „�“.
- Eine Karte, deren Datei ein Symlink auf eine andere Datei im Bundle ist, wurde beim Speichern beschrieben und änderte so
  die andere Karte, während das Ereignis die falsche nannte. `serve` lehnt das Schreiben über einen Symlink mit 422 ab.

Modellrollen:

- `auftrag` ohne `wiki.toml` prüfte die Sperren des Wikis nicht. Jetzt Exit 2, außer mit `--wiki PFAD` (fehlende Datei: Exit 2)
  oder ausdrücklich `--ohne-wiki` (dann gilt `personenbezogen`); `zeige` und `status` nennen eine fehlende `wiki.toml`.
- Eine Sitzung über einen eigenen Endpunkt galt als Anthropic. Ein unbekannter `ANTHROPIC_BASE_URL` und Schalter wie
  `CLAUDE_CODE_USE_BEDROCK` oder `CLAUDE_CODE_USE_VERTEX` gelten als `sitzung-eigener-endpunkt` und bekommen keine Daten der Klasse
  `personenbezogen`; ein als Anbieter eingetragener `ANTHROPIC_BASE_URL` gilt mit dessen `daten`. Harness `sitzung` mit einem anderen Anbieter
  als dem der Sitzung ist gesperrt (`setze` zeigt ⚠). `zeige --json` hat die Felder `hinweis` und `wirksam`, `status --json`
  das Feld `sitzung`, und `zeige` nennt die Herkunft jedes Felds.
- Eine handgeschriebene Nutzerdatei konnte mit einem falschen Typ eine Sperre aushebeln oder über `vorbefehl` ein Programm
  starten. Beim Laden sind falsche Typen, unbekannte Felder und ungültige Kennungen (ohne Punkt) ein Fehler.
- Ein Anbieter konnte jede Umgebungsvariable als Key an seinen Host schicken. Über die Oberfläche und `rollen.py anbieter` gehen
  nur Namen auf `_KEY` oder `_API_KEY` und `-api-key`, jeder für höchstens einen Anbieter; Namen mit `SECRET`, `PRIVATE`,
  `ACCESS`, `SSH` und Ähnlichem sind abgelehnt. Anbieter ab Werk oder aus einer lokalen Datei lassen sich so nicht überschreiben,
  nur von Hand. Ein Anbieter braucht eine Adresse; `daten = []` heißt nichts erlaubt, und ein fehlendes `daten` beim erneuten
  Speichern lässt den alten Wert stehen. Das Neuschreiben der Nutzerdatei erhält fremde Tabellen; Kommentare gehen verloren.
- Worker bekommen nur eine feste Auswahl an Umgebungsvariablen und laufen je Aufruf in einem eigenen, danach gelöschten
  Ordner. Modellnamen mit `-` vorn sind abgelehnt, gleich benannte Anhänge heißen `Elternordner/Name`. Der Webabruf (`container`)
  läuft fest über Anthropic. `CLAUDE_CONFIG_DIR` wird durchgereicht, eine `settings.json` dort kann den Endpunkt eines `claude`
  mit Anmeldung ändern, ohne dass `rollen.py` es sieht.

Aufnahme und Recherche:

- `api.py` spricht nur Port 443, bricht Antworten über 64 MB ab und schickt Schlüsselköpfe nie an einen anderen Host; `--by`
  ist ohne Angabe `RECHERCHE_BY`, sonst `claude-code/recherche`. `web-recherche` nimmt einen entfernten Docker-Endpunkt nur mit
  `WEB_RECHERCHE_DOCKER_FERN=ja`, sonst nur `unix://`.
- `api.py eintrag` schrieb `--query`, `--url` und `--treffer` ungeschwärzt in die Protokolldatei; ein Schlüsselwert aus der
  Umgebung konnte so in die `.jsonl` gelangen. `eintrag` schwärzt jetzt wie `suche`.
- `api.py` kannte in URLs nur `api_key`, `apikey` und `key` als Schlüsselparameter, etwa `access_token`, `sig`,
  `signature`, `client_secret` oder `password` standen im Protokoll; die Liste der geheimen Parameter liegt jetzt
  an einer Stelle und deckt sie alle.
- Das Prüfpfad-Protokoll von `web-recherche` führte den Wortlaut des Auftrags. Dort steht jetzt nur sha256 und Zeichenzahl.
- `fundweg --aus-lauf` und `new --aus-lauf` hängten Funde einer anderen Arbeit an. Sie vergleichen jetzt die Kennungen.

### Aus 0.5.0

Umsetzung der Entscheidungen zum Selbstreview vom 29.10.2026, dazu feste Schnittstelle und parallele Zweige.

Neu:

- **Lint-Regel `config_unknown`** (Warnung): unbekannte Abschnitte und Schlüssel der `wiki.toml` und unbekannte
  Codes in `[lint.severity]`, mit dem ähnlichsten bekannten Namen. Freie Tabellen (`[enums]`, `[labels.*]`,
  `[rights.*]`, `[log] labels`, `[licenses.patterns]`, `defaults` und `todo` der Typen) bleiben ungeprüft.
- **Lint-Regel `visibility_values_missing`** (Fehler): `[visibility] field` ohne `[enums]` für das Feld und ohne
  `default`.
- `body --nachziehen --neu`: Ereignis `create` statt `update` (ruft der PostToolUse-Hook nach einem Write auf).
- **Feste Schnittstelle:** `docs/konfiguration.md` hält fest, worauf sich Anwendungen auf dem Kern verlassen dürfen und was
  als Bruch zählt; `tests/test_schnittstelle.py` prüft die Tabelle gegen den Code. Der Code ändert sich dadurch nicht.
- **Parallele Zweige scheitern nicht mehr an Ereignisstrom und Projektionen.** `init` trägt `merge=union` für
  `.events.jsonl`, jedes `index.md`, `log.md` und Exporte in `.gitattributes` ein; `gitattributes` tut das in einem
  bestehenden Wiki und ist wiederholbar. Nach dem Merge erzeugt `rebuild` die Projektionen neu.
- Der Kern liest Ereignisse nach Zeit und jede Zeile einmal, weil sie nach einem Merge weder geordnet noch einmalig
  in der Datei stehen. `log.md` hängt damit nicht mehr von der Merge-Richtung ab; `status` zählt doppelte Zeilen nicht
  mehr mit. Wikis, deren Datei schon ungeordnet ist, melden beim nächsten Lint `projection_stale`, bis `rebuild` lief.

Geändert:

- Bruch im Sinne der festen Schnittstelle: `visibility_values_missing` ist eine neue Lint-Regel der Schwere `error`.
- `[visibility]` braucht bekannte Werte: ohne sie gilt jede Seite mit dem Feld als verborgen und
  `dashboard --oeffentlich` endet mit Exit 2. Ein Tippfehler wie „intren“ veröffentlicht keine Seite mehr.
  „Verborgen“ wirkt dann überall: im internen Dashboard (Kennzeichnung), in der Zahl bei `status` und in
  `visibility_leak`.
- `citation_with_claims` meldet bei `access: citation` jede Zeile im Kernaussagen-Abschnitt außer Hinweiskästen
  (`> [!todo] …`) und Fußnotendefinitionen, nicht mehr nur Listenpunkte.
- `lizenz --fetch` und `lizenz --missing --fetch` überschreiben nur Lizenzen ohne Namen oder mit `unknown`; eine
  benannte, auch nicht eingeordnete Lizenz nur mit `--force`; `--missing` nennt am Ende, wie viele es übersprungen
  hat. Es zählt nur noch der Name: `lizenz <seite> --fetch` überschreibt jetzt auch eine Lizenz, die nur eine URL
  trägt, ohne `--force`.
- **Der Stop-Hook gibt Lint-Fehler an die Sitzung zurück** (`decision: block`, je Seite eine Zeile mit Regelname), statt
  nur zu melden: höchstens zwei Runden je `session_id` (Zähler in `$TMPDIR`, ein fehlerfreier Lint setzt ihn zurück), nie
  bei `stop_hook_active: true`, also einmal je Abschluss; danach eine Zeile wie bisher. Bedienfehler, Zeitüberschreitung
  und Aufrufe ohne `session_id` blockieren nie, `rebuild --hook` allein auch nicht (neu dazu `--befunde`).
- SessionStart und Stop suchen die `wiki.toml` aufwärts ab dem Projektordner, Guard und post-edit ab der Zieldatei.
- `settings.example.json` ruft SessionStart und Stop über `okf_hook.py` auf statt den Kern direkt (wie im Plugin,
  mit Meldung bei Zeitüberschreitung). Wer die Vorlage übernommen hat, ersetzt die beiden Einträge.
- Projektordner ist immer der Ordner der `wiki.toml`: `--config` und `--root` auf verschiedene Ordner sowie
  `init --config` sind Bedienfehler (Exit 2).
- Fehlt `license` und führt das Profil es als Pflicht- oder empfohlenes Feld, meldet nur `missing_required` bzw.
  `missing_recommended` (mit dem Hinweis auf `lizenz --fetch`), nicht zusätzlich `license_missing`.
- `new source` zeigt Crossref-Hinweise (Rückzug, Korrektur) unter dem APA-Block auch ohne eigene Vorlage.
- `[export.csl] path` (`references.json`) und `[export.bibliography]` enthalten jede Quellenkarte, auch ohne
  `csl`-Block, aus derselben Grundlage wie das Dashboard.
  `id` ist dort immer der Slug: ein eigenes `id` im `csl`-Block wird jetzt überschrieben.
- `fundweg` auf einer Seite, die keine Quellenkarte ist, ist ein Bedienfehler (Exit 2).
- Ein bekannter Abschnitt der `wiki.toml` mit anderem Typ als Tabelle (`taxonomy = "x"`) ist ein Bedienfehler (Exit 2)
  statt Stille oder Traceback.
- Der Schlüssel `label` unter `[taxonomy]` entfällt (wurde nie angezeigt); ein alter Eintrag meldet `config_unknown`.
- Das Log beschriftet die Ereignisart `deprecate` mit „Als überholt markiert“ statt „Als veraltet markiert“, wie die
  Doku; „veraltet“ bleibt für `stale_after` und Projektionen. Ein bestehendes `log.md` mit solchen Einträgen gilt bis
  zum nächsten `rebuild` als veraltet; `[log] labels` übersteuert die Beschriftung weiter.
- Doku: Ereignisprotokoll, Log, Typ, Ereignisart und verfallen einheitlich; Vertrauensstufen und Rechtewerte je
  einmal mit Feldwert und Anzeigewort; Guard ohne `uv` und `verify --status deprecated` für jeden Akteur beschrieben.

Behoben:

- Write einer neuen Seite schrieb `update` statt `create`; im Log stand „Aktualisiert“ statt „Erstellt“.
- Eine Sitzung in einem Unterordner des Wikis bekam keinen Status beim Start und keinen Rebuild beim Stop.

## 0.4.0 · 29.10.2026

Erster Selbstreview über 0.3.0: Doku, Code und Tests gegeneinander gelesen.

Neu:

- **Selbstreview als Release-Schritt.** `tests/selbstreview.py` baut aus einem Commit ein Wegwerf-Wiki über okfw
  selbst, je Befehl, Lint-Regel, `wiki.toml`-Schlüssel und Hook eine Karte mit Fundstellen; Ablauf in
  `tests/selbstreview.md`.
- **`tests/test_abdeckung.py`** prüft bei jedem Testlauf mit derselben Kartenliste: alles im Code steht in der
  Doku und wird von einem Test berührt, alles in der Doku gibt es im Code.
- **GitHub-Workflow** `.github/workflows/tests.yml`: Tests bei jedem Pull Request und auf `main`.
- Tests für jede Lint-Regel und jeden `wiki.toml`-Schlüssel, die bisher keiner berührte.
- Nachbau-Test 34 von 34 mit echter Claude-Sitzung: `claude -p` scheitert beim Überschreiben einer Rohquelle
  am Guard und schlägt `capture` oder eine `-v2`-Fassung vor.

Behoben:

- `rebuild --quiet` schwieg bei jedem Bedienfehler, jetzt nur ohne `wiki.toml`.
- `[lint.severity]` mit anderem Wert als `error`, `warn`, `info`, `off` und `requires_core` wie `"v0.3"` sind
  Bedienfehler statt Traceback.
- `source_undated` übersah `date` (und `created`, `published`) auf Karten mit `authors`, `doi` oder `year`.
- `lizenz --class` ohne `--name` endete auf einer Karte ohne Lizenznamen mit KeyError.
- Die Meldung zur Zitatverfolgung bei `new` nennt `--fundweg-von`.
- Doku an den Code angeglichen: Exit-Codes, vollständige Ereignisarten, `body --detail`, Lizenz- und
  Volltextbefunde, Vorrang von `license.class`, Verhalten der Hooks je nach Einbau, Standardwerte und mehr
  (48 Vermerke im Bericht).

Klarstellung zu 0.3.0: Verfällt eine Gegenprüfung durch Claude oder einen Prozess, meldet Lint
`check_outdated` (Hinweis); `verified_outdated` gilt nur der Freigabe durch einen Menschen.

## 0.3.0 · 28.10.2026

Die Freigabe ist jetzt an den Inhalt gebunden, den jemand geprüft hat.

Verhalten geändert:

- **Prüfwert in jedem Prüfvermerk.** `verify` schreibt in jeden `verified`-Eintrag `body_sha256`, den
  SHA-256 des Seitentexts ohne Frontmatter (eine ausgewiesene Erweiterung zu OKF §5.2). Ändert sich der
  Text danach, verfällt der Vermerk: Lint `verified_outdated` (Warnung, bei `status: stable` Fehler).
- **Rückfall auf draft.** `body` setzt eine stable-Seite auf `draft`, wenn ihre Freigabe nicht mehr zum
  neuen Text passt; `verified` bleibt stehen, das Ereignis nennt die verfallene Freigabe. Abschaltbar mit
  `[trust] edit_resets_stable = false`. Gleicher Text ändert nichts.
- **Freigabe nur nach Gegenprüfung.** `verify --by human:<id> --status stable` verlangt je Seite einen
  Vermerk von Claude oder einem Prozess mit Prüfwert zum selben Text; sonst bricht es ohne Änderung ab
  (`--force` gibt trotzdem frei und vermerkt das, Lint `human_without_check`). `verify` nimmt mehrere
  Seiten und eine gemeinsame `--note` (eine Charge, ein Ereignis je Seite).
- **`status` und `verified` nur über `verify`.** Der Guard blockiert Edit und Write, die diese Felder
  einer Wiki-Seite ändern; eine neue Seite darf `status: draft` tragen.
- **Edit und Write ziehen `generated` nach.** Hat ein Edit oder Write den Text einer Wiki-Seite
  geändert, setzt der PostToolUse-Hook `generated {by, at}` mit dem Akteur aus `[wiki] actor`
  (`body <seite> --nachziehen`); auf einer stable-Seite greift derselbe Rückfall wie bei `body`. Danach
  liest Claude die Datei vor dem nächsten Edit neu.
- **Vertrauensstufen nur aus gültigen Vermerken.** `status` zählt human-reviewed und machine-confirmed
  nur, wenn der Vermerk zum jetzigen Text passt; Seiten, deren Vermerke alle verfallen sind (auch die von
  Prozessen), stehen getrennt als „n verfallen“. Dazu „n zur Freigabe“ und die Zahl der Gegenprüfungen
  ohne Prüfwert.

Neu:

- Dashboard-Abschnitt **„Zur Freigabe“**: Seiten mit Gegenprüfung, aber ohne gültige Freigabe, je mit dem
  ganzen Text und hervorgehobenen Belegen (lange Seiten zuerst die belegten Aussagen, der ganze Text klappt
  auf, eingeklappt mit „n Absätze ohne Fußnote“), Notiz der Gegenprüfung und fertigem `verify`-Aufruf
  für die markierten Seiten zum Kopieren; darunter verfallene Freigaben und „Gegenprüfung ohne Prüfwert“
  mit einer Zeile, die eine neue Gegenprüfung anstößt.
- `[trust] human = "human:<id>"` belegt die Kennung in „Zur Freigabe“ vor; `[trust] check_command`
  (z. B. `/pruefen`) ist der Aufruf der Gegenprüfung in der Liste ohne Prüfwert.
- Lint-Regeln `verified_unhashed` (info: Gegenprüfung ohne Prüfwert) und `check_outdated` (info:
  verfallene Prüfung durch Claude oder einen Prozess); opt-in `[trust] signed_human = true` mit
  `human_unsigned` (warn: Freigabe nicht aus einem signierten Commit).
- Abschnitt „Freigabe über Claude“ in `skills/okfw/docs/konfiguration.md` mit Ablauf einer Charge und
  Einrichtung der Commit-Signatur.
- Nachbau-Test mit zwei Schritten zur Freigabe (Ablehnung ohne Gegenprüfung, Rückfall nach `body`):
  33 von 33 Pflichtschritten bestanden, der Schritt mit `claude -p` ohne Token übersprungen.

Migration: Ältere Vermerke ohne Prüfwert bleiben gültig, solange `generated.at` nicht jünger ist, zählen
aber nicht als Gegenprüfung für eine Freigabe; Lint `verified_unhashed` und „Gegenprüfung ohne Prüfwert“
im Dashboard zeigen, welche Seiten vor einer Freigabe neu gegengeprüft werden müssen.

## 0.2.0 · 28.10.2026

Verhalten geändert:

- **`verify --status stable` nur noch mit `human:`-Akteur.** Ein Prüfvermerk durch Claude oder ein
  anderes Werkzeug hält die Prüfung fest, hebt den Status aber nicht mehr; `verify --status stable`
  bricht mit anderem Akteur ab. Lint `stable_unverified` meldet jetzt auch `status: stable`, wenn nur
  ein maschineller Prüfvermerk da ist. Bestehende Karten: von einem Menschen prüfen lassen und
  `verify <seite> --by human:<id> --status stable` setzen, oder den Status auf `draft` zurücknehmen.
- **`capture --license` prüft die Rechte.** Erlaubt die Rechtematrix „Volltext lokal ablegen“ für die
  Lizenz nicht, bricht `capture` ab; „erst fragen“ und Auflagen kommen als Warnung.
- `capture` lehnt Symlinks als Datei ab.
- `body`, `lizenz` und `fundweg` ziehen `generated: {by, at}` nach wie `new`; `verify` nicht.
- Widerspruchs- und `[!todo]`-Marker in Code-Spannen und Codeblöcken zählen nicht mehr (`status`,
  Lint `contradiction_one_sided`, „Offene Stellen“ im Dashboard).

Neu:

- **Befehl `sources <seite> --add a,b | --remove c`**: Quellen einer Karte nachtragen oder entfernen,
  ohne den Rest der Datei anzufassen. `--add ID=SLUG` legt eine eigene Fußnote je Fundstelle auf
  dieselbe Quellenkarte an; `--remove` nur, wenn der Text `[^id]` nicht mehr zitiert.
- **`capture --file PFAD=REPO-PFAD`** trägt je Datei den Pfad im Quell-Repo ins Manifest;
  `--source-path` gilt für Dateien ohne eigenen. `capture --commit` warnt, wenn es kein voller SHA ist.
- `new source --set raw_packet=raw/<slug>` übernimmt die Lizenz aus `manifest.json` (via `capture`).
- **`skills/okfw/local.example.md`**: Vorlage für eine `local.md` je Installation (Projekte,
  Begleit-Skills, Altbestand). Ohne `local.md` gilt allein `SKILL.md`.
- `rebuild --hook` für den Stop-Hook: neu erzeugen ohne Ausgabe, Lint-Fehler als eine Zeile, ohne zu
  blockieren.
- Hooks melden Bedienfehler (kaputte `wiki.toml`, `requires_core`, fehlendes uv, Zeitüberschreitung) als
  eine Zeile statt still; ohne `wiki.toml` bleiben sie stumm.
- Profil `dokumentation` erlaubt `format: specification` und `dataset`.
- APA 7 auch im APA-Block neuer Karten und im Literaturverzeichnis (derselbe CSL-Stil wie im Dashboard);
  „Z.“ und „Zeilen“ gelten als Fundstellenkürzel.
- Das öffentliche Dashboard zeigt keine Ablageorte von Volltexten (`stored`) mehr; der Schreibschutz
  deckt auch das öffentliche Dashboard ab.
- Python-Tests unter `tests/` (`uv run python -m unittest`); Nachbau-Test 32 von 32 mit echter
  Claude-Sitzung.

## 0.1.0 · 25.10.2026

Erste Veröffentlichung.

Geprüft: Nachbau 31 von 31 Pflichtschritten, die echte Claude-Sitzung übersprungen.
