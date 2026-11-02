# Schriften für das Dashboard

Eingebettet von `okfw.py dashboard`. Alle stehen unter der SIL Open Font License 1.1, die Weitergabe und Einbettung
erlaubt; der Lizenztext liegt je Schrift bei (`OFL-*.txt`, Text unverändert aus dem jeweiligen Paket oder Repositorium).

| Datei | Schrift | Glyphen | Original | SHA-256 |
|---|---|---|---|---|
| `bricolagegrotesque-latin-var.woff2` | Bricolage Grotesque 1.001, Achsen `opsz`, `wght` | 268 | npm `@fontsource-variable/bricolage-grotesque@5.3.0`, `files/bricolage-grotesque-latin-opsz-normal.woff2` | `a79fdb52d4a5c76552452f69202add96e287401fff03d3e8c0e38b4dcb5a99cd` |
| `instrumentsans-latin-var.woff2` | Instrument Sans 1.000, Achse `wght` | 244 | npm `@fontsource-variable/instrument-sans@5.3.0`, `files/instrument-sans-latin-wght-normal.woff2` | `2ee17598a98d8a59e4df8152d015bec9ab8e4d5672cc0ab42bef806b568e3971` |
| `newsreader-latin-var.woff2` | Newsreader 1.003, Achsen `wght`, `opsz` | 262 | npm `@fontsource-variable/newsreader@5.3.0`, `files/newsreader-latin-opsz-normal.woff2` | `6e4f2958c3a7c4a80acde4e5a679abe7e01bc1e30b92be3c7a8b696ef401d101` |
| `newsreader-italic-latin-var.woff2` | Newsreader Italic 1.003, Achse `wght` | 262 | npm `@fontsource-variable/newsreader@5.3.0`, `files/newsreader-latin-wght-italic.woff2` | `48bc8861b9b2ca9300747cad4fd6a3b4ac3028d364df00bd1b72097baa75e509` |
| `jetbrainsmono-regular.woff2` | JetBrains Mono Regular 2.305 | 1754 | JetBrains/JetBrainsMono `fonts/webfonts/JetBrainsMono-Regular.woff2` | `f1a7a03672cdd494ce0d5543fac6e4360fe22403c6de297fdc2e55a815f7baff` |

**Bricolage Grotesque, Instrument Sans und Newsreader** sind die Latin-Teilmengen von Fontsource, bytegleich mit den
Paketdateien; nur die Dateinamen sind geändert. Teilmengen sind veränderte Fassungen (Modified Version) im Sinn der OFL.
Das ist hier erlaubt: Keine der drei Lizenzen nennt einen Reserved Font Name, die Schriften dürfen also unter ihrem Namen
bleiben. Lizenztexte: je `LICENSE` aus dem Paket. Zeichen außerhalb der Teilmenge, etwa Pfeile oder ł, kommen aus der
Ersatzschrift des Systems; Instrument Sans hat dazu kein `²`. Glyphenzahl gelesen mit fontTools.

**JetBrains Mono** ist bytegleich mit dem Original (Git-Blob-Hash verglichen am 2026-10-25,
https://github.com/JetBrains/JetBrainsMono @ 19371302b95d218af43299bce79ddbddd0bc364d), nur der Dateiname ist
kleingeschrieben. Lizenztext: `OFL.txt` an demselben Commit.

`okfw.py dashboard` schreibt vor die eingebetteten Schriften einen CSS-Kommentar mit dem
Copyright-Vermerk (erste Zeile des jeweiligen Lizenztexts) und dem Lizenzhinweis (Name, Link), damit beides
in der erzeugten Datei im Klartext steht; den Lizenzhinweis mit Link tragen auch die Schriftdateien selbst
in ihren Metadaten. Eine Schrift ohne ihren Lizenztext in diesem Ordner wird nicht eingebettet.

# Zitierstil

`../apa.csl` ist der CSL-Stil „APA Style 7th edition“ (http://www.zotero.org/styles/apa), unverändert
(SHA-256 gleich mit citation-style-language/styles `apa.csl` @ 3c6698d791a83016d699f50cbbea857f6ee3d2f8),
Lizenz CC BY-SA 3.0 (steht im Stil unter `<rights>`, Autorinnen und Autoren unter `<author>`). Standard für
Literaturangaben im Dashboard, wenn `wiki.toml` unter `[export.csl] style` keinen eigenen Stil nennt.
