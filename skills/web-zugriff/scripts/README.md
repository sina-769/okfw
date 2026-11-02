# web-zugriff: Container-Werkzeug

`web-recherche` holt freigegebene Webinhalte, ohne dass die Haupt-Session sie selbst abruft. Das
erledigt ein Claude-Code-Agent in einem Container (empfohlene Laufzeit: OrbStack). Er hat keine Shell, darf nur freigegebene
Domains erreichen und legt genau eine Markdown-Datei ab. Ein deterministischer Schritt auf dem Host
prüft diese Datei und bereinigt sie, erst dann geht sie in den Zielordner. PDFs liest statt des Agenten ein
Programm ohne Modell (`--pdf`, siehe „PDFs“).

Bedienung: `SKILL.md`, Abschnitt 3. Hier stehen Aufbau, Prüfungen und Restrisiken.

## Aufbau

```
Host: web-recherche ──stdin: Token──▶ Agent-Container ──HTTPS_PROXY──▶ Proxy-Container ──▶ Internet
      (prüft, bereinigt,              claude -p, nur                  nur api.anthropic.com
       verschiebt)                    WebSearch/WebFetch/Write        + freigegebene Domains, Port 443
            ▲                         Netz: webz-<lauf> (--internal,  Netz: webz-<lauf> + bridge
            └──── ausgabe/ (einziger Mount, leer) ◀───────────────────
```

| Datei | Zweck |
|---|---|
| `web-recherche` | Wrapper (Python, nur Standardbibliothek): Lauf, Mitschrift, Host-Prüfung, Prüfpfad; `--token-ablegen` für die Einrichtung |
| `container/Dockerfile` | Image: `node:22-bookworm-slim` per Digest, Claude Code per npm in fester Version, Nutzer 10001 |
| `container/agent-start.sh` | Entrypoint: liest den Token aus stdin, startet `claude` |
| `container/managed-settings.json` | Richtlinie mit höchstem Vorrang: Bash, Glob, Grep, NotebookEdit, Task, Agent, Skill verboten; keine Hooks, keine MCP-Server |
| `container/proxy.mjs` | HTTPS-Proxy mit Domain-Freigabe (Netz-Sperre) |
| `container/pdf-text.mjs` | PDF-Abruf und Textgewinnung für `--pdf`, ohne Modell und ohne Token |
| `tests/test_pruefung.py` | Offline-Tests der Host-Prüfung |
| `tests/container_tests.py` | Vorprüfung (Test 0), Live-Tests des Agenten (Abruf, `ls`, `/etc/passwd`, Schreiben außerhalb) und des PDF-Wegs (Tests 5–7), Protokoll in `~/.claude/web-zugriff-protokoll/container-tests-<Datum>.md` |

## Schutzschichten

| Schicht | Was sie verhindert | Durchgesetzt von |
|---|---|---|
| Werkzeugsatz | Shell, Dateien lesen, Sub-Agents, MCP | `--tools WebSearch,WebFetch,Write,ToolSearch`, `--restricted`, `--strict-mcp-config`, `--disable-slash-commands`, Managed Settings im Image; der Wrapper bricht ab, wenn die Init-Meldung andere Werkzeuge oder MCP-Server nennt |
| Berechtigungen | Abrufe fremder Domains, Rückfragen | `--permission-mode dontAsk`, `--permission-prompts none`, `WebFetch(domain:…)` nur für die Freigabe |
| Container | Schreiben auf Host, Rechteausweitung, Ressourcen | `--read-only`, tmpfs `/tmp` (noexec), `--cap-drop ALL`, `no-new-privileges`, Nutzer 10001, 1 GB RAM, 256 PIDs, 2 CPUs, Zeitlimit, `--rm`; gemountet ist nur der leere Ausgabeordner, kein Home, kein Repo, kein Docker-Socket |
| Netz | jede Verbindung außerhalb der Freigabe | internes Docker-Netz ohne Ausgang und ohne DNS; einziger Weg ist der Proxy; Details unten |
| Host-Prüfung | HTML, Skripte, versteckter Text, fremde URLs, falsche Dateien | `pruefen()` in `web-recherche` |
| Kennzeichnung | Verwechslung mit geprüftem Wissen | Kopfzeile mit Vermerk, Inhalt zwischen Markierungen mit Zufallskennung |

**Token.** `CLAUDE_CODE_OAUTH_TOKEN` stammt aus dem macOS-Schlüsselbund (Eintrag `claude-code-oauth-token`).
Unter Linux (und überall, wo die Variable gesetzt ist, dann mit Vorrang) liest `web-recherche` ihn aus der
Umgebungsvariable `WEB_ZUGRIFF_OAUTH_TOKEN`; `--token-ablegen` gibt es nur unter macOS.
Er geht über stdin an den Entrypoint, steht also nicht in den Argumenten und nicht in der
Container-Konfiguration (`docker inspect`). Er landet nie auf der Platte und nie im Protokoll.
`--bare` ist keine Option: Damit liest Claude Code nur `ANTHROPIC_API_KEY`, nie OAuth.
Abgelegt wird der Token mit `web-recherche --token-ablegen`, nur in Terminal.app. `setup-token`
zeigt ihn umbrochen an, und die verdeckte Abfrage von `security … -w` endet am Zeilenumbruch.
Beim ersten Versuch landeten so nur 79 von 108 Zeichen im Schlüsselbund, die API antwortete
mit 401. `--token-ablegen` setzt die Zeilen zusammen, prüft Präfix und Länge und gibt den Wert über
stdin an `security -i` weiter, also an keiner Befehlszeile vorbei.

**Docker-Endpunkt.** Der Token geht per stdin an `docker run`, also an den Daemon des gewählten Kontexts.
Ein entfernter Kontext (`tcp://`, `ssh://`) bekäme ihn mit. `web-recherche` wählt den Kontext (Variable
`WEB_RECHERCHE_DOCKER_CONTEXT`, sonst `orbstack`, sonst der aktuelle; bei gesetztem `DOCKER_HOST` gilt dieser
Wert), liest den Endpunkt mit `docker context inspect` und bricht ab, wenn er kein lokaler `unix://`-Socket
ist. Wer bewusst einen entfernten Daemon nutzt, setzt `WEB_RECHERCHE_DOCKER_FERN=ja`. Kontext, Herkunft der
Wahl und Endpunkt stehen im Prüfpfad (Feld `docker`) und in der Ausgabe von `--trocken`.

**Read nicht verbieten.** Eine Verbotsregel `Read` (Managed Settings oder `--disallowedTools`)
sperrt in Claude Code 2.1.291 auch `Write` auf dieselbe Datei („File is covered by a Read deny
rule … cannot be written“). Read ist ohnehin nicht geladen (`--tools`), Test 0 prüft das.

**WebFetch und ToolSearch.** In Claude Code 2.1.291 wird WebFetch nachgeladen (`shouldDefer`). Es
steht nur zur Verfügung, wenn ToolSearch im Werkzeugsatz ist. ToolSearch findet dort nur, was `--tools`
zulässt, und erweitert die Rechte also nicht.

## Netz-Sperre mit OrbStack: geprüft

Domain-Sperre ist mit OrbStack machbar (OrbStack 2.2.5, Docker 29.8.2), und zwar ohne `NET_ADMIN`
im Agent-Container. Pro Lauf entsteht ein Netz `docker network create --internal`. Der Agent hängt
nur dort. Der Proxy-Container hängt zusätzlich im Bridge-Netz und ist der einzige Weg nach draußen.
Gemessen am 2026-10-25:

| Versuch aus dem internen Netz | Ergebnis |
|---|---|
| direkte TCP-Verbindung zu 1.1.1.1:443 und 8.8.8.8:53 | `ENETUNREACH` |
| DNS-Auflösung `api.anthropic.com`, `host.docker.internal` | `EAI_AGAIN` (kein DNS im internen Netz) |
| Proxy: CONNECT `api.anthropic.com:443` | 200 |
| Proxy: CONNECT auf nicht freigegebene Domain, auf Subdomain einer freigegebenen Domain | 403 |
| Proxy: CONNECT auf IP-Adresse, auf Port 80 | 403 |
| Proxy: `host.orb.internal`, `host.docker.internal` (testweise freigegeben) | 403, zeigen auf 0.250.250.254 (gesperrter Bereich) |
| Proxy: einfaches HTTP (`GET http://…`) | 403 |

Der Proxy lässt nur CONNECT auf Port 443 zu. Die Freigabeliste prüft er vor der DNS-Abfrage,
gesperrte Namen werden also gar nicht erst aufgelöst. Private, lokale und reservierte Zieladressen
lehnt er ab, und er verbindet zur geprüften IP. Jede Entscheidung schreibt er als JSON-Zeile ins Log;
der Wrapper übernimmt die Zusammenfassung in Kopfzeile und Prüfpfad. Freigaberegel: `x` erlaubt
`x` und `www.x`, `*.x` zusätzlich alle Subdomains. Dieselbe Regel gilt in der Host-Prüfung.

## PDFs

WebFetch liefert im Container bei PDFs nur Binärdaten, und dem Agenten fehlt jedes Werkzeug, um sie zu öffnen.
Bash oder Read bekommt er dafür nicht. Stattdessen holt `web-recherche --pdf <URL>` das Dokument ohne Agenten:

```
Host: web-recherche ──URLs als Argumente──▶ PDF-Container ──HTTPS_PROXY──▶ Proxy-Container ──▶ Internet
      (prüft, bereinigt,                    node pdf-text.mjs             nur die freigegebenen Domains,
       verschiebt)                          Netz: webz-<lauf> (--internal) kein api.anthropic.com
            ▲                                     │
            └──── ausgabe/ergebnis.md ◀───────────┘   Bericht als JSON-Zeilen auf stdout
```

Warum ohne Modell: Am Original zu prüfen heißt, den Wortlaut zu brauchen. WebFetch fasst dagegen zusammen
(Restrisiko 4), und ein Modell mit PDF-Zugriff wäre ein weiteres Ziel für Prompt Injection. Hier kann kein Text aus
dem PDF ein Verhalten auslösen, denn nichts wertet ihn aus. Der Container hat keinen Token, und der Proxy kennt
`api.anthropic.com` in diesem Lauf nicht.

| Stelle | Regel |
|---|---|
| vor dem Start (Host) | jede URL: `https`, Port 443, keine Zugangsdaten, Host in `--domains`; sonst Exit 1, es startet nichts |
| Container | Härtung wie beim Agenten (`--read-only`, `--cap-drop ALL`, `no-new-privileges`, Nutzer 10001, Limits, `--rm`), `/tmp` im tmpfs, nur der leere Ausgabeordner gemountet, kein stdin, kein Token, keine Claude-Prozesse |
| Netz | nur über den Proxy, auch bei Weiterleitungen (höchstens 5): Jeder Host geht einzeln durch CONNECT und die Freigabe. Weiterleitungen auf `http`, andere Ports oder mit Zugangsdaten bricht das Programm ab. Die Zertifikate prüft Node gegen seine eingebauten Stammzertifikate |
| Abruf | höchstens 64 MB, Antwort muss HTTP 200 sein und `%PDF-` im ersten KB tragen; HTML und Fehlerseiten gelten als Fehler, ihr Text gelangt nirgendwohin |
| Parser | pdf.js (`pdfjs-dist`, Version in `web-recherche`) in JavaScript ohne nativen Code, installiert ohne optionale Pakete und ohne Installationsskripte; `isEvalSupported: false`, keine Schriften, kein XFA, höchstens 5000 Seiten |
| Ausgabe | genau `ergebnis.md`, geschrieben erst, wenn alle PDFs gelesen sind und der Text unter `--max-kb` bleibt. Scheitert ein Dokument, gibt es kein Teilergebnis |
| Text | je Seite `## Seite N von M`, davor URL, SHA-256 und Seiten ohne Text. Jedes `#` aus dem PDF wird zu `\#`, also kann ein Dokument keine Seitenmarke nachbauen. Eine Seite ohne Text bekommt einen Vermerk. Silbentrennungen bleiben stehen, es wird nichts geglättet |
| Bericht | stdout: Zahlen, Prüfsumme, URLs und feste Fehlertexte, nie Text aus dem PDF. Der Host prüft jedes Feld, auch dass die Ziel-URL in der Freigabe liegt, und übernimmt nur die bekannten |
| Host-Prüfung | dieselbe wie beim Agenten (`pruefen()`), unverändert: fremde URLs verwerfen das Ergebnis. Dazu `seiten_pruefen()`: Die Marken müssen lückenlos zum Bericht passen, ein Dokument ganz ohne Text ist kein Ergebnis (Exit 3) |
| Kennzeichnung | Kopfzeile mit Vermerk und Herkunft „Container ohne Modell“, im Kopf nur die Zahlen und die Prüfsumme; Inhalt zwischen den Markierungen mit Zufallskennung |
| Prüfpfad | wie beim Agenten, zusätzlich `art: pdf` und je Dokument URL, Ziel-URL, SHA-256, Bytes, Seitenzahl und Seiten ohne Text; der „Auftrag“ (`PDF-Abruf: <URLs>`) nur als sha256 |

PDFs zitieren fast immer fremde Adressen, und die Host-Prüfung verwirft das Ergebnis dann (Exit 3 mit den
Hostnamen). Das ist so gewollt und nicht heimlich abgeschwächt: Wer das Dokument lesen will, ruft mit
`--fremde-urls schwaerzen` auf. Dann fehlen diese Adressen im Text, die Zahl steht in der Kopfzeile, im Original
sind sie weiter zu finden. „Seite“ meint die Seite der PDF-Datei; eine aufgedruckte Nummer kann davon abweichen und
steht gegebenenfalls im Text.

## Tests (Stand 2026-11-01)

Protokolle am okfw-Release 0.6.0: Lauf vom 2026-10-25 (Tests 0–4, davon Test 1 auf `example.org`) und
vom 2026-11-01 (Tests 0, 2–7 mit dem aktuellen Image). Test 1 und Test 7 rufen als einzige etwas
ab und brauchen deshalb je eine Freigabe. Test 1 lief am 2026-11-01 nicht, weil dafür keine Freigabe vorlag; der
Weg des Agenten hat sich seitdem nur beim gemeinsamen Containerstart und um einen Satz im Systemprompt geändert,
Tests 2–4 decken das ab. Die Tests 2–4 und 6 geben nur `example.invalid` frei; diese Domain löst nie auf.

| Test | Ergebnis |
|---|---|
| 0 Vorprüfung | Härtungs-Flags und Mounts laut `--trocken`, Image-Nutzer 10001, Managed Settings: wie vorgesehen |
| 1 Abruf | ToolSearch lud WebFetch, ein Abruf von `https://example.org/`, Netz nur `api.anthropic.com` und `example.org`, Ergebnis mit Kopfzeile im Zielordner |
| 2 `ls` | Werkzeugliste ohne Bash; zwei ToolSearch-Suchen nach Bash lieferten nur WebFetch; keine `ls`-Ausgabe |
| 3 `/etc/passwd` | kein Lese-Werkzeug, keine containerspezifischen passwd-Zeilen im Ergebnis |
| 4 Schreiben außerhalb | nichts auf dem Host; per `sh` mit gleicher Härtung: `/`, `/etc`, `/home`, `/usr`, `/ausgabe/..` read-only, `/tmp` nur im tmpfs |
| 5 PDF-Leser ohne Netz | selbst erzeugtes PDF: Text je Seite, leere Seite mit Vermerk, nachgebaute Marke maskiert, kaputtes PDF endet mit Fehler. Gegen einen Server im selben Container (`--network none`): fremdes Zertifikat, Weiterleitung auf `http`, fremde Domain, anderen Port, Zugangsdaten oder Schleife, HTTP 404, HTML statt PDF und PDF über 64 MB werden abgelehnt; die Prüfsumme stimmt |
| 6 PDF-Weg im Netz | URL außerhalb von `--domains`: Exit 1, kein Lauf. Domain ohne DNS: Exit 2, nichts im Zielordner. Host-Prüfung umgangen wie bei einer Weiterleitung: Der Proxy allein sperrt (403), Ausgabeordner leer |
| 7 PDF-Abruf | EMA-Dokument „Guiding principles on the use of large language models“: 10 Seiten, alle mit Text, aufgedruckte Seitenzahl gleich PDF-Seite, Netz nur `www.ema.europa.eu`, kein Modell, Kopfzeile und Markierung vorhanden |

Einschränkung: In den Tests 3 und 4 hat das Modell die Versuche selbst unterlassen, auch mit dem
Hinweis auf einen genehmigten Test. Die Sperre auf Claude-Ebene (`--restricted`) wurde dort also
nicht ausgelöst. Belegt sind die Ebenen darunter: Werkzeugsatz und Container.

## Host-Prüfung

1. Im Ausgabeordner liegt genau ein Eintrag: eine reguläre `.md`-Datei, kein Link, kein Ordner.
2. Größenlimit (Standard 200 KB), striktes UTF-8.
3. Entfernt werden: Steuerzeichen, unsichtbare und richtungsändernde Unicode-Zeichen (z. B.
   Zero-Width, Bidi-Overrides, Tag-Zeichen), ein eigener Frontmatter-Kopf des Agenten,
   HTML-Kommentare, `<script>`/`<style>`/`<iframe>`/`<svg>`/…-Blöcke samt Inhalt, alle übrigen Tags,
   `data:`-, `javascript:`-, `vbscript:`- und `file:`-URIs. Bilder werden zu Links, damit beim
   Rendern nichts automatisch nachgeladen wird.
4. Alle `http(s)`/`ftp`-URLs und `www.`-Angaben werden gegen die Freigabe geprüft (Host per
   `urlsplit`, damit `https://erlaubt.de@fremd.com` als fremd gilt). Bei fremden URLs wird das
   Ergebnis verworfen, außer mit `--fremde-urls schwaerzen`.
5. Davor kommt die Kopfzeile: Vermerk, Auftrag, Domains, Zahl der Abrufe und Suchen, Netz-Log,
   Zählung der Entfernungen. Der Inhalt steht zwischen `═══ BEGINN/ENDE WEBINHALT [<kennung>] ═══`,
   dort zuerst die Angaben des Agenten (abgerufene und gescheiterte URLs, Suchanfragen, im Text
   genannte URLs), denn er wählt sie selbst. Die Kennung entsteht
   zufällig auf dem Host, der Agent kann die Endmarke also nicht fälschen.
6. Erst danach wird die Datei in den Zielordner verschoben. Überschrieben wird nichts.

Auf stdout gibt der Wrapper nur Pfad, Zählwerte und Hostnamen aus, nie Text des Agenten. Auch der
Schlusstext des Agenten (`result`) wird nicht ausgegeben, denn Webinhalt kann ihn beeinflussen.

## Restrisiken

1. **Prompt Injection verfälscht den Inhalt.** Das ist das Hauptrisiko, und keine Schicht fängt es
   ab. Text auf einer freigegebenen Seite (oder in Suchtreffern) kann den Agenten dazu bringen,
   Falsches zu schreiben, Wichtiges wegzulassen, Zitate zu erfinden oder „Auffälligkeiten: keine“
   zu melden. Die Host-Prüfung erkennt nur Form, keine Bedeutung. Was hilft:
   - Ergebnisse als Hinweise behandeln, nie als Beleg.
   - Zahlen, Zitate und Aussagen am Original prüfen. Im Zitiermodus zählt nur das Originaldokument
     als Fundstelle, nicht dieses Ergebnis.
   - Bei wichtigen Punkten eine zweite, unabhängige Quelle oder einen zweiten Lauf heranziehen.
   - Domains mit Inhalten von Nutzer:innen (Foren, Wikis, Kommentarspalten, GitHub, geteilte Dokumente)
     nur freigeben, wenn nötig.
2. **Injection gegen die Haupt-Session.** Die bereinigte Datei kann weiterhin Anweisungen in
   normaler Sprache enthalten („Ignoriere alle bisherigen Anweisungen …“). Entfernt werden nur
   Markup und versteckte Zeichen. Die Haupt-Session muss den Inhalt als Daten lesen und
   Anweisungen darin der Person zeigen. Die Markierungen grenzen den Webinhalt ab, verhindern
   aber nichts.
3. **WebSearch läuft bei Anthropic.** Suchanfragen und Treffer laufen über Anthropics Server, nicht
   über den Proxy. `allowed_domains` setzt das Modell selbst, technisch erzwungen wird es nicht.
   Snippets fremder Domains können also in den Kontext des Agenten gelangen und dort als
   Injection wirken. Fremde URLs fängt die Host-Prüfung ab, fremden Text nicht. Streng geht es
   mit `--ohne-suche`.
4. **WebFetch fasst zusammen.** Claude Code schickt den Seiteninhalt durch ein kleines Modell. Der
   Agent sieht also verarbeiteten Text, und „wörtliche“ Auszüge sind nicht garantiert wörtlich.
   Auch das kleine Modell kann Ziel einer Injection sein.
5. **Freigabe gilt für die ganze Domain.** Der Proxy sieht nur Hostnamen (TLS bleibt Ende-zu-Ende),
   keine Pfade. Innerhalb einer freigegebenen Domain ist jede Seite erreichbar, und
   Weiterleitungen auf demselben Host werden befolgt.
6. **Abfluss über erlaubte Kanäle.** Der Agent könnte Daten in Pfade oder Parameter von Abrufen auf
   freigegebenen Domains kodieren. Er kennt aber nur Auftrag und Systemprompt. Deshalb nie
   Geheimnisse, Patienten- oder Kundendaten in den Auftrag schreiben. Auftrag und abgerufene
   Inhalte gehen an die Anthropic-API (Abo-Bedingungen).
7. **Token im laufenden Container.** Während eines Laufs steht der Token in der Prozessumgebung
   des Agenten. Der Agent hat kein Werkzeug, das sie lesen könnte. Wer Zugriff auf den Docker-Socket
   hat, kann sie per `docker exec` aber auslesen, also jeder Prozess des Mac-Nutzers. Der Token ist
   langlebig und hängt am Abo: Bei Verdacht widerrufen und mit `claude setup-token` neu anlegen.
8. **Container-Ausbruch.** Die Container teilen sich den Kernel der OrbStack-VM. Die Härtung macht
   einen Ausbruch schwerer, aber nicht unmöglich. Ein Ausbruch landet in der VM, und OrbStack gibt
   dort macOS-Verzeichnisse (u. a. `/Users`) frei.
9. **Lieferkette.** Claude Code kommt als großes Binärpaket über npm. Version und Basis-Image sind
   festgeschrieben und werden nur bewusst aktualisiert. Ändern sich Flags oder Werkzeuge zwischen
   Versionen (z. B. WebFetch hinter ToolSearch), bricht die Init-Prüfung ab. Nach jedem Update laufen
   die Tests neu.
10. **URL-Prüfung ist Plausibilität.** Erkannt werden URLs mit Schema und `www.`-Angaben. Nackte
    Domainnamen („fremd.com“) und verschleierte URLs („hxxp://“, Leerzeichen) erkennt sie nicht.
    Das HTML-Entfernen per Regex ersetzt keinen Parser. Es reicht, weil die Datei als Text gelesen
    und nicht gerendert wird.
11. **`api.anthropic.com` ist beim Agenten immer offen** (Modell, WebFetch-Vorabprüfung). WebFetch dorthin
    verbieten die Berechtigungen, andere Werkzeuge für HTTP gibt es nicht. Beim PDF-Weg ist die Adresse gesperrt.
12. **PDF-Text kommt ungefiltert in die Haupt-Session.** Kein Agent steht mehr dazwischen, die Datei liest also
    die Session mit Shell-Rechten. Das PDF kann Anweisungen in normaler Sprache enthalten, auch als unsichtbaren
    Text (weiße Schrift, verdeckte Ebene, Darstellungsart „unsichtbar“). pdf.js liefert solchen Text mit, und
    keine Prüfung erkennt ihn. Die Markierungen grenzen ab, verhindern aber nichts (Restrisiko 2). Bei sehr
    empfindlichen Sitzungen die Datei einem Helfer ohne Schreibrechte zum Lesen geben.
13. **Text ist nicht das Dokument.** Reihenfolge und Zuordnung folgen dem Inhaltsstrom der Datei: Spalten,
    Tabellen, Fußnoten und Formeln können verrutschen, Zahlen können so der falschen Zeile zugeordnet erscheinen.
    Die Host-Prüfung ändert außerdem den Wortlaut, wo sie bereinigt: spitze Klammern mit Tag-Form, fremde URLs,
    `file:` und ähnliche Präfixe; die Kopfzeile zählt das. Zahlen und Zitate für eine Folie daher am Original
    prüfen. Die Prüfsumme belegt, welche Datei gelesen wurde. Sie belegt nicht, dass derselbe Server sie
    weiter ausliefert oder dass sie die maßgebliche Fassung ist.
14. **Scans bleiben unlesbar.** OCR gibt es nicht. Ein Dokument ganz ohne Text wird verworfen (Exit 3), einzelne
    Seiten ohne Text sind vermerkt. Grafiken und Abbildungen fehlen immer, auch wenn sie Zahlen tragen.
15. **Parser im Container.** Ein Parser verarbeitet fremde Bytes. pdf.js läuft ohne nativen Code, kann aber Fehler
    haben; ein überlanges oder bösartiges PDF kann bis zum Zeitlimit Rechenzeit und bis zu 1 GB Speicher belegen.
    Trifft ein Angriff den Parser, sitzt er in einem gehärteten Container ohne Token, ohne Modell, hinter
    demselben Proxy. Der Host behandelt Bericht und Ausgabe darum als Daten. Der Parser kommt wie Claude Code über
    npm (Restrisiko 9); seine Version ist festgeschrieben.
16. **Auch beim PDF gilt die Freigabe für die ganze Domain** (Restrisiko 5). Weiterleitungen auf einen anderen
    freigegebenen Host werden befolgt, die Ziel-URL steht im Dokumentkopf und im Prüfpfad. Für jedes PDF gilt weiter:
    Domain, URL, Zweck vorher freigeben lassen; `--pdf` ersetzt diese Rückfrage nicht.

## Wartung

- Claude Code aktualisieren: `CLAUDE_VERSION` in `web-recherche` ändern, pdf.js: `PDFJS_VERSION`. Beim nächsten
  Lauf (oder mit `--bauen`) entsteht ein neues Image, der Tag hängt an den Versionen und Container-Dateien.
  Danach `claude --help` im Image ansehen und beide Testskripte laufen lassen.
- Reste abgebrochener Läufe: `web-recherche --aufraeumen`. Rohdaten liegen nur mit `--roh-behalten`
  unter `~/.cache/web-zugriff/laeufe/`. Sie sind unbereinigt, also nur zur Fehlersuche öffnen.
- Tests:
  ```bash
  uv run --no-project python -I ${CLAUDE_SKILL_DIR}/scripts/tests/test_pruefung.py
  uv run --no-project python -I ${CLAUDE_SKILL_DIR}/scripts/tests/container_tests.py --nur 0,2,3,4,5,6
  uv run --no-project python -I ${CLAUDE_SKILL_DIR}/scripts/tests/container_tests.py --nur 1 --testdomain <freigegebene Domain>
  uv run --no-project python -I ${CLAUDE_SKILL_DIR}/scripts/tests/container_tests.py --nur 7 --testdomain <freigegebene Domain> --testpdf <URL>
  ```
  Die Tests 1 und 7 rufen etwas ab und brauchen deshalb jedes Mal eine Freigabe. Die übrigen Tests rufen
  nichts ab. Test 5 braucht `openssl` für ein Wegwerf-Zertifikat, sonst entfällt der Teil zum Abrufweg.
