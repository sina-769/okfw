# Entwicklung

Voraussetzungen im Einzelnen, Installation ohne Plugin, Tests und der Weg zu einem Release. Kurzfassung: [README](../README.md).

## Voraussetzungen

| Was | Version im Nachbau-Test | Wozu |
|---|---|---|
| [Claude Code](https://code.claude.com/docs) | 2.1.291 | Plugin, Skill, Hooks |
| [uv](https://docs.astral.sh/uv/) | 0.12.7 | startet Kern und Hooks; holt Python ≥ 3.11 und die Pakete aus dem Skriptkopf (PEP 723) |
| Python | ≥ 3.11 (im Test 3.14.7, von uv geholt) | Kern und Hooks; der Kern setzt uv voraus, uv holt ein passendes Python selbst, falls keins da ist |
| PyYAML ≥ 6.0, Markdown ≥ 3.5 | von uv beim ersten Start geholt | Frontmatter, Leseansicht |
| [OpenCode](https://opencode.ai), Docker-Laufzeit | optional | Harness `opencode`; Webabruf im Container |
| [Poppler](https://poppler.freedesktop.org) (`pdftotext`), unter Linux [bubblewrap](https://github.com/containers/bubblewrap) | optional | PDFs in `okfw serve` abgeschottet auslesen; ohne sie bleibt die Karte ohne Modell |

Netz braucht der Kern beim ersten Start (PyPI, ggf. Python-Download durch uv) und sonst nur in Befehlen,
die dafür da sind: `new source --doi/--pmid/--arxiv`, `lizenz --fetch` und die Skripte von `recherche` (Crossref, PubMed,
Europe PMC, DataCite, OpenAlex, Semantic Scholar, arXiv, EU-Cellar); Modellaufrufe gehen nur an die Anbieter deiner Rollen.
Die Tests laufen unter Linux (GitHub Actions) und macOS, der Nachbau-Test in einem Debian-12-Container.

## Installation ohne Marketplace

Statt `sina-769/okfw` geht auch ein lokaler Ordner: ein Klon des Repos oder ein Ordner mit
`.claude-plugin/marketplace.json`. Danach Claude Code neu starten (oder `/reload-plugins`).

Ohne Plugin geht es auch als Skill-Ordner: die Ordner unter `skills/` nach `~/.claude/skills/` kopieren, `agents/` nach
`~/.claude/agents/` und `skills/okfw/hooks/settings.example.json` in die `.claude/settings.json` des Projekts übernehmen.
Nicht beides zugleich, sonst laufen die Hooks doppelt.

Was nur für eine Installation gilt (Projekte, Begleit-Skills), steht in einer `local.md` neben
`skills/okfw/SKILL.md`; Vorlage ist [skills/okfw/local.example.md](../skills/okfw/local.example.md).

## Tests

Kern und Hooks haben Python-Tests (unittest, ohne weitere Abhängigkeiten). Sie rufen beides so auf wie
Claude Code, über `uv run --no-config --script`, je in einem frischen Wiki unter `$TMPDIR`. `tests/test_abdeckung.py`
hält Doku und Code zusammen: Jeder Befehl, jede Lint-Regel, jeder Schlüssel der `wiki.toml` und jeder Hook steht in der
Doku und wird von einem Test berührt, und was die Doku nennt, gibt es im Code.

Der eine Prüfbefehl, gleich für Menschen, Agenten und CI:

```bash
tests/pruefen.sh
```

Er führt die Python-Tests und die Prüfung von `web-zugriff` aus (ohne Docker) und endet bei jedem Fehler mit Exit ≠ 0; die Tests binden
lokale Ports. [.github/workflows/tests.yml](../.github/workflows/tests.yml) ruft nur dieses Skript auf. Einzelne Tests gehen weiter so:

```bash
uv run --with pyyaml --with markdown python -m unittest tests.test_abdeckung   # im Plugin-Ordner
```

Die Arbeitsanleitung für Agenten steht in [.claude/CLAUDE.md](../.claude/CLAUDE.md), was okfw ist und nicht ist in [richtung.md](richtung.md).

## Nachbau-Test

[tests/nachbau.sh](../tests/nachbau.sh) installiert das Plugin in einer frischen Umgebung aus einem
Marketplace, legt ein Demo-Wiki aus zwei frei lizenzierten Quellen an (OKF-Spezifikation, Apache-2.0;
README von pi-llm-wiki, MIT; beide über die GitHub-API auf einen Commit gepinnt und per SHA-256
geprüft), erfasst sie, schreibt Karten, führt `rebuild`, `lint` und `dashboard` aus und ruft die Hooks
mit genau den Befehlen aus `hooks/hooks.json` auf. Dazu kommen der Inhalt des Plugins, `rollen.py` und `api.py` ohne
Einrichtung, die Bereinigung einer SVG-Abbildung, das Dashboard einer Seite mit `{: …}` (kein `onclick`, CSP mit Nonce) und
`okfw serve` (Pfadpräfix, Token, Cookie, fremde Pfade 404). Jeder Schritt landet mit Befehl, Ausgabe und Exit-Code in einem
Protokoll.

```bash
docker build -t okfw-nachbau -f tests/Dockerfile tests
docker run --rm -v "$PWD:/src:ro" -v "$PWD/.nachbau:/out" okfw-nachbau /src /out/lauf-$(date +%H%M%S)
```

Mit `-e CLAUDE_CODE_OAUTH_TOKEN` (Token aus `claude setup-token`) läuft zusätzlich eine echte
Claude-Sitzung, die an den Schreibschutz stößt. Für einen Release gehört sie dazu: Ohne Token steht der Schritt als übersprungen
im Protokoll, und das Protokoll trägt den Vermerk „Unvollständig“.

Die Protokolle hängen an den Releases.

## Beiträge

Pull Requests gehen gegen `main` des öffentlichen Repos; `tests/pruefen.sh` sollte vorher grün sein. Dort liegt nur, was veröffentlicht ist:
Jede Version entsteht in einem Arbeitsrepo und kommt nach den Release-Schritten als ein Commit dorthin. Ein angenommener Beitrag wird darum
nicht dort gemergt, sondern ins Arbeitsrepo übernommen, mit dir als Autor, und kommt mit der nächsten Version zurück; deren Commit nennt dich
als Co-Autor. Fragen und Vorschläge ohne Code gehen als Issue.

## Release-Schritte

Die Tests laufen bei jedem Pull Request auf GitHub ([.github/workflows/tests.yml](../.github/workflows/tests.yml)). Vor
jeder Version:

1. **Prüfen:** `tests/pruefen.sh` und die Tests der Anwendungen auf der
   [festen Schnittstelle](../skills/okfw/docs/konfiguration.md#feste-schnittstelle). Weiter nur bei Grün.
2. **Diff prüfen:** Der Diff seit dem letzten Release wird in Pakete bis 60.000 Zeichen zerlegt, jedes mit dem
   [Angreifermodell](../tests/angreifer.md). Ein Modell, das nicht gebaut hat, liest jedes Paket einmal als Code-Review
   und Angriff, Ausgabe live ab Start, 15 Minuten Zeitlimit. Nach einer Zeitüberschreitung wird das Paket nicht
   wiederholt, sondern geteilt oder im Bericht als nicht geprüft geführt.
3. **Korrigieren:** Vor dem Release behoben wird, was im Angreifermodell mit einem Versuch belegt ist, und ein
   nachgestellter Datenverlust oder Bruch der festen Schnittstelle. Alles andere geht in die nächste Version.
   Höchstens zwei Korrekturrunden; jede prüft nur ihren eigenen Diff, kein weiteres
   Modell liest denselben Stand. Bleibt danach ein Befund der ersten Art, entscheidet die Person, ob das Release
   wartet. Bericht als Anhang am Release: behoben, verworfen mit Grund, zurückgestellt, nicht geprüft.
4. **Screenshots:** Die Bilder in `docs/` entstehen neu, nur vor einem Release. Die Aufnahme läuft in einem Container ohne
   Netz, der nur das Plugin und ein Beispiel-Wiki aus öffentlichen Quellen sieht. Vor jedem Bild wird der Seitentext auf
   private Pfade und Namen geprüft, danach der Text im Bild per OCR; ein Treffer bricht den Lauf ab. Die Bilder tragen
   keine Metadaten, und eine Person sieht jedes an, bevor es veröffentlicht wird.
5. **Nachbau:** frische Installation im Container ([Nachbau-Test](#nachbau-test)), Protokoll als
   Anhang am Release.
6. **Export:** Version in `.claude-plugin/plugin.json` und `.claude-plugin/marketplace.json` setzen, bei geändertem Kern
   auch `VERSION` in `skills/okfw/okfw_kern/basis.py`, `CHANGELOG.md` abschließen. Eine feste Prüfung auf genau diesen Stand samt Commit-Nachricht hinterlegt bei Grün einen Vermerk; ohne
   ihn bricht der Export ab. Eine ausgefallene Prüfung gilt als nicht geprüft, nie als grün. Dann die Version
   veröffentlichen und mit einem Tag markieren.

Eine volle Prüfung des ganzen Kerns samt [Selbstreview](../tests/selbstreview.md) ist eine eigene Aufgabe, kein
Release-Schritt. Anlass sind ein neuer Angreifer oder Eingabeweg, ein Umbau des Kerns oder ein Befund, der eine neue
Art von Angriff zeigt. Letzte: 0.7.0, der Bericht hängt am Release.
