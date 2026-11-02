# Fremde Bestandteile und Vorbilder

## Mitgeliefert (Lizenztexte liegen bei)

| Bestandteil | Datei | Herkunft | Lizenz |
|---|---|---|---|
| Bricolage Grotesque, Latin-Teilmenge | `skills/okfw/templates/fonts/bricolagegrotesque-latin-var.woff2` | npm `@fontsource-variable/bricolage-grotesque@5.3.0`, Copyright 2022 The Bricolage Grotesque Project Authors | SIL Open Font License 1.1 (`OFL-Bricolage-Grotesque.txt`) |
| Instrument Sans, Latin-Teilmenge | `skills/okfw/templates/fonts/instrumentsans-latin-var.woff2` | npm `@fontsource-variable/instrument-sans@5.3.0`, Copyright 2022 The Instrument Sans Project Authors | SIL Open Font License 1.1 (`OFL-Instrument-Sans.txt`) |
| Newsreader und Newsreader Italic, Latin-Teilmengen | `skills/okfw/templates/fonts/newsreader-*.woff2` | npm `@fontsource-variable/newsreader@5.3.0`, Copyright 2020 The Newsreader Project Authors | SIL Open Font License 1.1 (`OFL-Newsreader.txt`) |
| JetBrains Mono Regular | `skills/okfw/templates/fonts/jetbrainsmono-regular.woff2` | https://github.com/JetBrains/JetBrainsMono, Copyright 2020 The JetBrains Mono Project Authors | SIL Open Font License 1.1 (`OFL-JetBrains-Mono.txt`) |
| CSL-Stil „APA Style 7th edition“ | `skills/okfw/templates/apa.csl` | citation-style-language/styles @ 3c6698d791a83016d699f50cbbea857f6ee3d2f8, unverändert; Autorinnen und Autoren im Stil unter `<author>`/`<contributor>` | CC BY-SA 3.0 (im Stil unter `<rights>`) |

JetBrains Mono wird unverändert weitergegeben. Die übrigen Schriften sind Latin-Teilmengen von Fontsource und damit
veränderte Fassungen im Sinn der OFL; ihre Lizenzen nennen keinen Reserved Font Name. Alle werden von `okfw.py dashboard` in
die erzeugte HTML-Datei eingebettet, mit Copyright-Vermerk und Lizenzhinweis als Kommentar davor; beides erlaubt die OFL. Einzelheiten:
`skills/okfw/templates/fonts/README.md`.

Marken: JetBrains Mono ist eine Marke der JetBrains s.r.o. (so vermerkt
in den Schriftdateien). Die Namen stehen hier nur, um die Schriften zu bezeichnen; die OFL räumt keine
Markenrechte ein.

## Zur Laufzeit geholt (nicht mitgeliefert)

| Paket | Wozu | Lizenz |
|---|---|---|
| [PyYAML](https://pyyaml.org) ≥ 6.0 | Frontmatter lesen und schreiben | MIT |
| [Python-Markdown](https://python-markdown.github.io) ≥ 3.5 | Leseansicht im Dashboard | BSD-3-Clause |

uv holt beide beim ersten Start des Kerns aus PyPI (Skriptkopf nach PEP 723 in `okfw.py`).

## Vorbilder (kein Code übernommen)

- **pi-llm-wiki** (Zosma AI, https://github.com/zosmaai/pi-llm-wiki, MIT): LLM-Wiki-Muster
  (Recall, Capture, Ingest, Retro, Lint), Schichten aus Rohquellen, Seiten, Ereignissen und Projektionen,
  Aufbau des Claude-Code-Plugins mit SessionStart-Hinweis und Schreibschutz. okfw setzt das neu in
  Python um; Guard und Ereignisprotokoll entsprechen in der Wirkung den Hooks von pi-llm-wiki.
- **Open Knowledge Format v0.2** (GoogleCloudPlatform/knowledge-catalog, `okf/SPEC.md` @
  a8291a1a656669b8766fd4ff0f985505fb955e94, Apache-2.0): das Format der Wikis (Bundle, Frontmatter,
  `generated`/`verified`, Index- und Log-Dateien, Akteurskonvention). okfw implementiert die
  Spezifikation; ihr Text ist nicht enthalten, außer in den Bildern (unten).
- **LLM-Wiki-Muster** von Andrej Karpathy, auf das sich pi-llm-wiki beruft.

## Bilder und Testprotokolle

Die Bilder unter `docs/` zeigen ein Beispiel-Wiki mit Auszügen aus amtlichen Texten der EU: den Verordnungen (EU) 2024/1689,
(EU) 2026/1744 und (EU) 2024/2847 und der Richtlinie (EU) 2024/2853, abgerufen über die Cellar-API des Amts für
Veröffentlichungen am 1.11.2026. Die Protokolle unter `tests/` zeigen ein Beispiel-Wiki mit Auszügen aus den beiden
Quellen oben: der OKF-Spezifikation (Apache-2.0) und dem README von pi-llm-wiki (MIT).
