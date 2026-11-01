"""Schaubilder: SVG unter <bundle>/abbildungen/, bereinigt im Dashboard, Lint figure_*, Befehl abbildung, Design.

Ohne Netz. Das Dashboard wird mit dem Kern gebaut und aus der eingebetteten JSON-Datei (oder reader_html) gelesen.
"""

import json
import re

from .hilfe import WikiTest

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><title>Prüfung</title><defs>'
       '<linearGradient id="g"><stop offset="0" stop-color="#c00"/></linearGradient></defs>'
       '<rect id="a" width="10" height="10" fill="url(#g)"/><use href="#a" x="20"/>'
       '<text x="1" y="2">Hallo MARKE</text></svg>')
BOESE = ('<?xml version="1.0" encoding="UTF-8"?>'
         '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
         'xmlns:ink="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 100 50" onload="alert(\'PWNED-onload\')" '
         'class="x">'
         '<script>alert("PWNED-script")</script><ink:namedview id="v"/>'
         '<style>rect { fill: url(http://evil.example/x) }</style>'
         '<a xlink:href="javascript:PWNED-link"><rect width="5" height="5"/></a>'
         '<foreignObject><div>PWNED-fremd</div></foreignObject><image href="http://evil.example/i.png"/>'
         '<rect width="5" height="5" onclick="PWNED-click" style="fill:url(http://evil.example/y)" '
         'fill="url(#ok)" stroke="red"/>'
         '<use xlink:href="http://evil.example/z#a"/><use xlink:href="#ok"/>'
         '<text x="1" y="2">sichtbar <tspan>auch</tspan> &lt;script&gt;</text></svg>')
SEITE = ("Satz mit Beleg.[^mueller]\n\n![Ablauf](/abbildungen/{name}.svg)\n"
         "*Abb.: Der Ablauf. Eigene Darstellung nach Müller[^mueller].*\n\n[^mueller]: [Quelle](/sources/mueller.md), S. 4\n")


def daten(html_datei: str) -> dict:
    return json.loads(re.search(r'<script type="application/json" id="okf-data">(.*?)</script>', html_datei, re.S).group(1))


class Grundlage(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("mueller")

    def abbildung_ablegen(self, name: str, svg: str) -> None:
        ziel = self.root / "wiki" / "abbildungen" / f"{name}.svg"
        ziel.parent.mkdir(exist_ok=True)
        ziel.write_text(svg, encoding="utf-8")

    def seite(self, slug: str, body: str, *extra: str) -> None:
        self.core("new", "concept", slug, "--title", slug, "--description", "D", "--sources", "mueller", *extra)
        self.core("body", f"concepts/{slug}", "-", stdin=body)

    def html(self, slug: str) -> str:
        return self.py(f"result = wiki.reader_html()['concepts/{slug}']['html']")


class AbbildungBefehl(Grundlage):
    def test_ablegen_bereinigt_und_protokolliert(self):
        datei = self.datei("boese.svg", BOESE)
        done = self.core("abbildung", "ablauf", "--file", str(datei))
        text = (self.root / "wiki" / "abbildungen" / "ablauf.svg").read_text(encoding="utf-8")
        for verboten in ("PWNED", "onload", "<script", "<style", "evil.example", "foreignObject", "inkscape", "class="):
            self.assertNotIn(verboten, text)
        self.assertIn("sichtbar", text)
        self.assertIn('role="img"', text)
        self.assertIn("entfernt", done.stderr)
        self.assertIn("abgelegt: wiki/abbildungen/ablauf.svg", done.stdout)
        ereignisse = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"page": "abbildungen/ablauf"', ereignisse)

    def test_vorhandene_nur_mit_ersetzen(self):
        datei = self.datei("a.svg", SVG)
        self.core("abbildung", "ablauf", "--file", str(datei))
        done = self.core("abbildung", "ablauf", "--file", str(datei), ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--ersetzen", done.stderr)
        self.core("abbildung", "ablauf", "--file", str(datei), "--ersetzen")

    def test_ueber_200_kb_exit_2(self):
        gross = SVG.replace("</svg>", "<!-- " + "x" * 210_000 + " --></svg>")
        done = self.core("abbildung", "gross", "--file", str(self.datei("gross.svg", gross)), ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("200 KB", done.stderr)
        self.assertFalse((self.root / "wiki" / "abbildungen" / "gross.svg").exists())

    def test_doctype_entity_kein_viewbox_kein_svg_werden_abgelehnt(self):
        faelle = {
            "doctype": '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            "entity": '<!ENTITY x "y"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            "ohne-viewbox": '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>',
            "html": '<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
            "kaputt": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><g></svg>',
        }
        for name, inhalt in faelle.items():
            with self.subTest(name):
                done = self.core("abbildung", name, "--file", str(self.datei(f"{name}.svg", inhalt)), ok=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertFalse((self.root / "wiki" / "abbildungen" / f"{name}.svg").exists())

    def test_ungueltiger_name_und_fehlende_datei(self):
        self.assertEqual(self.core("abbildung", "Ablauf Neu", "--file", str(self.datei("n.svg", SVG)), ok=False).returncode, 2)
        self.assertEqual(self.core("abbildung", "nirgends", "--file", str(self.base / "gibt-es-nicht.svg"), ok=False).returncode, 2)

    def test_ohne_xmlns_und_mit_width_height(self):
        self.core("abbildung", "schlicht", "--file", str(self.datei(
            "s.svg", '<svg width="120px" height="60"><rect width="5" height="5"/></svg>')))
        text = (self.root / "wiki" / "abbildungen" / "schlicht.svg").read_text(encoding="utf-8")
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', text)
        self.assertIn('viewBox="0 0 120 60"', text)

    def test_guard_erlaubt_schreiben_in_abbildungen(self):
        for name in ("abbildungen/x.svg", "abbildungen/notiz.md"):
            done = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                                       "tool_input": {"file_path": str(self.root / "wiki" / name), "content": SVG}})
            self.assertNotIn("deny", done.stdout, name)


class Bereinigung(Grundlage):
    def probe(self, svg: str, prefix: str = "p1-") -> dict:
        return self.py(f"""
try:
    sauber, entfernt = okfw.clean_svg({svg!r}, prefix={prefix!r}, label='Titel')
    result = {{'svg': sauber, 'entfernt': entfernt}}
except okfw.FigureError as exc:
    result = {{'fehler': str(exc)}}""")

    def test_allowlist(self):
        r = self.probe(BOESE)
        for verboten in ("PWNED", "onload", "script", "<style", "evil.example", "foreignObject", "image", "class="):
            self.assertNotIn(verboten, r["svg"].replace("&lt;script&gt;", ""))
        self.assertIn("Element script", r["entfernt"])
        self.assertIn("Attribut onload", r["entfernt"])
        self.assertIn("Element namedview (fremder Namensraum)", r["entfernt"])
        self.assertIn('<use href="#p1-ok" />', r["svg"])
        self.assertIn('fill="url(#p1-ok)"', r["svg"])
        self.assertIn("&lt;script&gt;", r["svg"])      # Text bleibt Text
        self.assertNotIn('<use href="http', r["svg"])

    def test_ids_und_verweise_bekommen_das_praefix(self):
        r = self.probe(SVG)
        self.assertIn('id="p1-g"', r["svg"])
        self.assertIn('id="p1-a"', r["svg"])
        self.assertIn('fill="url(#p1-g)"', r["svg"])
        self.assertIn('href="#p1-a"', r["svg"])
        self.assertIn('aria-label="Titel"', r["svg"])
        self.assertTrue(r["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"'))

    def test_abgelehnt(self):
        self.assertIn("DOCTYPE", self.probe('<!DOCTYPE svg [<!ENTITY a "b">]><svg viewBox="0 0 1 1"/>')["fehler"])
        self.assertIn("viewBox", self.probe('<svg xmlns="http://www.w3.org/2000/svg"/>')["fehler"])
        self.assertIn("Elemente", self.probe('<svg viewBox="0 0 1 1">' + "<g/>" * 5001 + "</svg>")["fehler"])
        self.assertIn("tief", self.probe('<svg viewBox="0 0 1 1">' + "<g>" * 70 + "</g>" * 70 + "</svg>")["fehler"])

    def test_style_nur_ohne_fremde_adressen(self):
        r = self.probe('<svg viewBox="0 0 1 1"><rect style="fill:red;stroke:url(#a)" width="1" height="1"/>'
                       '<rect style="fill:url(//evil.example/a)" width="1" height="1"/>'
                       '<rect style="behavior:url(#x);color:\\72ed" width="1" height="1"/></svg>')
        self.assertIn('style="fill:red;stroke:url(#p1-a)"', r["svg"])
        self.assertNotIn("evil", r["svg"])
        self.assertNotIn("behavior", r["svg"])


class Dashboard(Grundlage):
    def test_einbettung_mit_unterschrift_und_beleg(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", SEITE.format(name="ablauf"))
        html = self.html("k1")
        self.assertIn('<figure class="abbildung">', html)
        self.assertIn("<svg ", html)
        self.assertIn("MARKE", html)
        self.assertRegex(html, r"<figcaption>Abb\.: Der Ablauf\. Eigene Darstellung nach Müller .*cite-ref.*</figcaption>")
        self.assertNotIn("<img", html)
        self.assertNotIn("/abbildungen/", html)

    def test_unterschrift_auch_nach_leerzeile_und_ohne(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", "![Ablauf](/abbildungen/ablauf.svg)\n\n*Abb.: Später.[^mueller]*\n\nText.[^mueller]\n\n"
                         "[^mueller]: [Quelle](/sources/mueller.md)\n")
        self.assertRegex(self.html("k1"), r"<figcaption>Abb\.: Später <span class=\"cite\">")
        self.seite("k2", "![Ohne](/abbildungen/ablauf.svg)\n\nText.[^mueller]\n\n[^mueller]: [Quelle](/sources/mueller.md)\n")
        html = self.html("k2")
        self.assertIn('<figure class="abbildung">', html)
        self.assertNotIn("<figcaption>", html)

    def test_dashboard_datei_ohne_skript_und_onload(self):
        self.abbildung_ablegen("boese", BOESE)
        self.seite("k1", SEITE.format(name="boese"))
        ziel = self.base / "dash.html"
        self.core("dashboard", "--out", str(ziel))
        datei = ziel.read_text(encoding="utf-8")
        for verboten in ("PWNED", "onload", "evil.example"):
            self.assertNotIn(verboten, datei)
        html = next(n["html"] for n in daten(datei)["nodes"] if n["id"] == "concepts/k1")
        self.assertIn("<figure", html)
        self.assertNotIn("<script", html.replace("&lt;script&gt;", ""))
        self.assertIn("sichtbar", html)

    def test_ids_mehrerer_abbildungen_stoeren_sich_nicht(self):
        self.abbildung_ablegen("eins", SVG)
        self.abbildung_ablegen("zwei", SVG)
        self.seite("k1", SEITE.format(name="eins") + "\n" + SEITE.format(name="zwei").split("[^mueller]:")[0])
        ids = re.findall(r'\bid="([^"]+)"', self.html("k1"))
        figure_ids = [i for i in ids if i.startswith("abb")]
        self.assertEqual(len(figure_ids), len(set(figure_ids)))
        self.assertEqual(len(figure_ids), 4)

    def test_fehlend_und_abgelehnt_ersetzt_ein_platzhalter(self):
        self.abbildung_ablegen("kaputt", '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>')
        self.seite("k1", "![A](/abbildungen/fehlt.svg)\n*Abb.: Weg.[^mueller]*\n\n![B](/abbildungen/kaputt.svg)\n"
                         "*Abb.: Kaputt.[^mueller]*\n\n[^mueller]: [Quelle](/sources/mueller.md)\n")
        html = self.html("k1")
        self.assertIn("Abbildung nicht eingebunden: fehlt", html)
        self.assertIn("Abbildung nicht eingebunden: kaputt", html)
        self.assertNotIn("<svg", html)

    def test_andere_bilder_werden_nie_geladen(self):
        self.seite("k1", "![Netz](https://example.org/a.png) und ![Lokal](/abbildungen/x.png) und "
                         "![Andere](../abbildungen/y.svg)\n\nText.[^mueller]\n\n[^mueller]: [Quelle](/sources/mueller.md)\n")
        html = self.html("k1")
        self.assertNotIn("<img", html)
        self.assertIn("Bild nicht eingebunden: Netz", html)
        self.assertIn("Bild nicht eingebunden: Lokal", html)
        self.assertIn("Bild nicht eingebunden: Andere", html)

    def test_bilder_im_code_bleiben_code(self):
        self.seite("k1", "Text.[^mueller]\n\n```\n![A](/abbildungen/x.svg)\n```\n\n[^mueller]: [Quelle](/sources/mueller.md)\n")
        self.assertNotIn("Abbildung nicht eingebunden", self.html("k1"))
        self.assertNotIn("figure_missing", self.lint_codes("concepts/k1"))

    def test_oeffentlich_ohne_abbildungen_interner_seiten(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        self.abbildung_ablegen("offen", SVG.replace("MARKE", "OFFENE-ABB"))
        self.abbildung_ablegen("intern", SVG.replace("MARKE", "INTERNE-ABB"))
        self.seite("k1", SEITE.format(name="offen"))
        self.seite("k2", SEITE.format(name="intern"), "--set", "sichtbarkeit=intern")
        normal, oeffentlich = self.base / "normal.html", self.base / "oeffentlich.html"
        self.core("dashboard", "--out", str(normal))
        self.core("dashboard", "--out", str(oeffentlich), "--oeffentlich")
        text = normal.read_text(encoding="utf-8")
        self.assertIn("OFFENE-ABB", text)
        self.assertIn("INTERNE-ABB", text)
        text = oeffentlich.read_text(encoding="utf-8")
        self.assertIn("OFFENE-ABB", text)
        self.assertNotIn("INTERNE-ABB", text)
        self.assertNotIn("concepts/k2", text)

    def test_oeffentlich_beleg_der_unterschrift_auf_interne_quelle_entfaellt(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        self.quelle("geheim", extra=("--set", "sichtbarkeit=intern"))
        self.abbildung_ablegen("offen", SVG)
        self.core("new", "concept", "k1", "--title", "K1", "--description", "D", "--sources", "mueller,geheim")
        self.core("body", "concepts/k1", "-", stdin="Satz.[^mueller]\n\n![A](/abbildungen/offen.svg)\n"
                  "*Abb.: Aus Quelle.[^geheim]*\n\n[^mueller]: [M](/sources/mueller.md)\n[^geheim]: [G](/sources/geheim.md)\n")
        ziel = self.base / "oeffentlich.html"
        self.core("dashboard", "--out", str(ziel), "--oeffentlich")
        html = next(n["html"] for n in daten(ziel.read_text(encoding="utf-8"))["nodes"] if n["id"] == "concepts/k1")
        self.assertIn("<figcaption>", html)
        self.assertNotIn("geheim", html.lower().replace("figcaption", ""))


class Lint(Grundlage):
    def test_ohne_unterschrift(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", "Text.[^mueller]\n\n![A](/abbildungen/ablauf.svg)\n\nWeiter.\n\n[^mueller]: [M](/sources/mueller.md)\n")
        self.assertIn("figure_caption", self.lint_codes("concepts/k1"))

    def test_unterschrift_ohne_fussnote(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", "Text.[^mueller]\n\n![A](/abbildungen/ablauf.svg)\n*Abb.: Ohne Beleg.*\n\n"
                         "[^mueller]: [M](/sources/mueller.md)\n")
        self.assertIn("figure_caption", self.lint_codes("concepts/k1"))

    def test_vollstaendig_ist_ruhig(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", SEITE.format(name="ablauf"))
        codes = self.lint_codes("concepts/k1")
        self.assertFalse([c for c in codes if c.startswith("figure_")], codes)

    def test_fehlende_und_abgelehnte_datei(self):
        self.abbildung_ablegen("kaputt", "<svg")
        self.seite("k1", SEITE.format(name="fehlt") + "\n![B](/abbildungen/kaputt.svg)\n*Abb.: B.[^mueller]*\n")
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        meldungen = [f["message"] for f in out["findings"] if f["code"] == "figure_missing"]
        self.assertEqual(len(meldungen), 2, meldungen)
        self.assertTrue(any("fehlt" in m for m in meldungen))
        self.assertTrue(any("kein gültiges XML" in m for m in meldungen))
        self.assertEqual({f["severity"] for f in out["findings"] if f["code"] == "figure_missing"}, {"error"})

    def test_rechte_uebernommen(self):
        self.core("lizenz", "sources/mueller", "--name", "Alle Rechte vorbehalten")
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", SEITE.format(name="ablauf").replace("Eigene Darstellung nach", "Abbildung übernommen aus"))
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        rechte = [f for f in out["findings"] if f["code"] == "figure_rights"]
        self.assertEqual(len(rechte), 1)
        self.assertEqual(rechte[0]["severity"], "warn")
        self.assertIn("erst fragen", rechte[0]["message"])
        self.assertIn("Genehmigung", rechte[0]["message"])

    def test_rechte_erlaubt_oder_nur_eigene_darstellung_ist_ruhig(self):
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", SEITE.format(name="ablauf"))      # „Eigene Darstellung“, Quelle ohne Lizenz: kein Befund
        self.assertNotIn("figure_rights", self.lint_codes("concepts/k1"))
        self.core("lizenz", "sources/mueller", "--name", "CC0 1.0")
        self.core("body", "concepts/k1", "-", stdin=SEITE.format(name="ablauf").replace("Eigene Darstellung nach", "übernommen aus"))
        self.assertNotIn("figure_rights", self.lint_codes("concepts/k1"))

    def test_rechte_je_quelle_ueberschreibt(self):
        self.core("lizenz", "sources/mueller", "--name", "Alle Rechte vorbehalten")
        self.py("wiki._write_frontmatter(wiki.page_path('sources/mueller'), "
                "{'rights': {'abbildung': 'ja: Abdruckgenehmigung vom 2026-10-20'}})")
        self.abbildung_ablegen("ablauf", SVG)
        self.seite("k1", SEITE.format(name="ablauf").replace("Eigene Darstellung nach", "übernommen aus"))
        self.assertNotIn("figure_rights", self.lint_codes("concepts/k1"))


class Bundle(Grundlage):
    def test_abbildungen_sind_keine_seiten_und_kein_bereich(self):
        self.abbildung_ablegen("ablauf", SVG)
        (self.root / "wiki" / "abbildungen" / "notiz.md").write_text("---\ntype: concept\n---\nx\n", encoding="utf-8")
        seiten = self.py("result = [p.id for p in wiki.pages()]")
        self.assertFalse([s for s in seiten if s.startswith("abbildungen")], seiten)
        self.core("rebuild")
        index = (self.root / "wiki" / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("abbildungen", index)
        self.assertFalse((self.root / "wiki" / "abbildungen" / "index.md").exists())
        self.assertEqual(self.core("rebuild", "--check").returncode, 0)
        self.assertEqual(self.core("lint", "--json", ok=None).stdout.count("abbildungen/notiz"), 0)


class Design(Grundlage):
    def design(self, css: str, pfad: str = "design.css") -> None:
        (self.root / pfad).write_text(css, encoding="utf-8")
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write(f'\n[dashboard]\ndesign = "{pfad}"\n')

    def test_wird_nach_der_vorlage_eingebettet(self):
        self.design(":root { --accent: #123456; }\n/* __DATA__ bleibt Text */\n")
        ziel = self.base / "dash.html"
        self.core("dashboard", "--out", str(ziel))
        text = ziel.read_text(encoding="utf-8")
        self.assertIn("--accent: #123456;", text)
        self.assertGreater(text.index("--accent: #123456;"), text.index("--accent: #FCE53A"))
        self.assertLess(text.index("--accent: #123456;"), text.index("</style>"))
        self.assertIn("/* __DATA__ bleibt Text */", text)
        self.assertEqual(self.lint_codes().count("design_unsafe"), 0)

    def test_unsicheres_design_bricht_den_bau_und_der_lint_meldet(self):
        faelle = {"import": '@import "https://evil.example/a.css";', "import-url": "@IMPORT url(a.css);",
                  "url": "body { background: url(https://evil.example/a.png); }",
                  "url-relativ": "body { background: url('bild.png'); }",
                  "style": "a { color: red } </style><script>PWNED</script>",
                  "maskiert": "@\\69mport 'x.css';", "image-set": "a { background: image-set('http://evil.example/a.png' 1x); }"}
        for name, css in faelle.items():
            with self.subTest(name):
                self.design(css)
                done = self.core("dashboard", "--out", str(self.base / "d.html"), ok=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("design", done.stderr)
                self.assertIn("design_unsafe", self.lint_codes())
                self.assertFalse((self.base / "d.html").exists())
                toml = (self.root / "wiki.toml").read_text(encoding="utf-8")
                (self.root / "wiki.toml").write_text(toml.split("\n[dashboard]\ndesign")[0] + "\n", encoding="utf-8")

    def test_erlaubt_sind_data_und_fragment_urls_und_kommentare(self):
        self.design("/* @import 'x'; url(http://x) */ a { background: url(data:image/png;base64,AAAA); mask: url(#m); }")
        self.core("dashboard", "--out", str(self.base / "d.html"))
        self.assertNotIn("design_unsafe", self.lint_codes())

    def test_fehlende_datei_und_pfad_ausserhalb(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[dashboard]\ndesign = "fehlt.css"\n')
        self.assertEqual(self.core("dashboard", "--out", str(self.base / "d.html"), ok=False).returncode, 2)
        self.assertIn("design_unsafe", self.lint_codes())
        toml = (self.root / "wiki.toml").read_text(encoding="utf-8").replace("fehlt.css", "../draussen.css")
        (self.root / "wiki.toml").write_text(toml, encoding="utf-8")
        (self.base / "draussen.css").write_text("a { color: red }", encoding="utf-8")
        done = self.core("dashboard", "--out", str(self.base / "d.html"), ok=False)
        self.assertIn("außerhalb", done.stderr)

    def test_oeffentliche_ausgabe_traegt_das_design_ebenfalls(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        self.design(":root { --accent: #654321; }")
        ziel = self.base / "o.html"
        self.core("dashboard", "--out", str(ziel), "--oeffentlich")
        self.assertIn("--accent: #654321;", ziel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest
    unittest.main()
