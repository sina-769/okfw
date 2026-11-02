"""Was ein fremdes Wiki nicht darf: Skript ins Dashboard bringen, Fremdes nachladen, aus dem Bundle schreiben, den Bau abbrechen.

Ohne Netz. Die Nutzlasten stehen im Wiki (Seite, Karte, Dateiname, Design, SVG); geprüft wird am erzeugten HTML.
"""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from .hilfe import SKILL, WikiTest

MARKE = '<script type="application/json" id="okf-data">'
GUT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="1" height="1"/><text>MARKE</text></svg>'
SEITE = "---\ntype: concept\ntitle: T\ndescription: D\nstatus: draft\ngenerated: {by: 'human:t', at: '2026-01-01T00:00:00Z'}\n---\n"
KARTE = ("---\ntype: source\ntitle: T\ndescription: D\nstatus: draft\naccess: citation\n"
         "generated: {by: 'human:t', at: '2026-01-01T00:00:00Z'}\n")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags(fragment: str) -> list[tuple[str, dict]]:
    parser = Tags()
    parser.feed(fragment)
    return parser.tags


def hrefs(fragment: str) -> list[str]:
    return [a["href"] for tag, a in tags(fragment) if tag == "a" and "href" in a]


def daten(text: str) -> dict:
    return json.loads(re.search(re.escape(MARKE) + "(.*?)</script>", text, re.S).group(1))


class Grundlage(WikiTest):
    def seite(self, slug: str, body: str, ordner: str = "concepts", felder: str = "") -> None:
        ziel = self.root / "wiki" / ordner / f"{slug}.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(SEITE.removesuffix("---\n") + felder + "---\n" + body + "\n", encoding="utf-8")

    def karte(self, datei: str, felder: str) -> None:
        ziel = self.root / "wiki" / "sources" / f"{datei}.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(KARTE + felder + "---\nText\n", encoding="utf-8")

    def html(self, slug: str, ordner: str = "concepts") -> str:
        return self.py(f"result = wiki.reader_html()['{ordner}/{slug}']['html']")

    def bau(self, *optionen: str) -> str:
        ziel = self.base / "bau.html"
        self.core("dashboard", "--out", str(ziel), *optionen)
        return ziel.read_text(encoding="utf-8")

    def draussen(self, name: str, inhalt: str = "UNVERAENDERT\n"):
        ziel = self.base / "draussen" / name
        ziel.parent.mkdir(exist_ok=True)
        ziel.write_text(inhalt, encoding="utf-8")
        return ziel


class AttributlistenUndHtml(Grundlage):
    def test_attributliste_bleibt_text(self):
        self.seite("k1", 'Absatz {: onanimationstart="alert(1)" style="animation:x" }\n\n'
                         '## Titel {: onclick="alert(2)" }\n\n*betont*{: onmouseover="alert(3)" }\n\n'
                         '[link](https://example.org){: onfocus="alert(4)" autofocus }\n')
        html = self.html("k1")
        for tag, attrs in tags(html):
            self.assertFalse([k for k in attrs if k.startswith("on") or k in ("style", "autofocus")], (tag, attrs))
        self.assertIn("onanimationstart", html)   # als Text sichtbar

    def test_md_in_html_und_rohes_html_bleiben_text(self):
        self.seite("k1", '<div markdown="1" onclick="alert(1)">\n*x*\n</div>\n\nText <img src=x onerror=alert(2)>\n')
        html = self.html("k1")
        self.assertEqual([t for t, _ in tags(html) if t in ("div", "img")], [])
        self.assertIn("&lt;img", html)

    def test_extra_funktionen_ohne_attr_list_bleiben(self):
        self.seite("k1", "Satz.[^a]\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nBegriff\n: Erklärung\n\n*[HTML]: Hypertext\n\nHTML\n\n"
                         "```python\nx = 1\n```\n\n[^a]: Fußnote\n")
        html = self.html("k1")
        for teil in ("<table>", "<dl>", '<abbr title="Hypertext">', 'class="language-python"', 'class="footnote-ref"'):
            self.assertIn(teil, html)


class Links(Grundlage):
    def test_nur_http_https_mailto(self):
        boese = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "java&#115;cript:alert(1)", "javascript&colon;alert(1)",
                 "&#9;javascript:alert(1)", "java&Tab;script:alert(1)", "&#1;javascript:alert(1)", "data:text/html,x",
                 "vbscript:x", "file:///etc/hosts"]
        self.seite("k1", "\n\n".join(f"[Text{i}]({h})" for i, h in enumerate(boese)) + "\n\n[Marke]: javascript:alert(2)\n\n[Marke]\n")
        html = self.html("k1")
        for href in hrefs(html):
            self.assertRegex(href, r"^#", href)
        for i in range(len(boese)):
            self.assertIn(f"Text{i}", html)
        self.assertNotIn("javascript:", " ".join(hrefs(html)))

    def test_erlaubte_und_interne_bleiben(self):
        self.seite("k2", "Ziel\n")
        self.seite("k1", "[a](https://example.org/x) [b](HTTP://example.org/y) [c](mailto:a@example.org) <b@example.org> "
                         "[d](/concepts/k2.md) [e](#anker) https://example.org/nackt\n")
        html = self.html("k1")
        alle = tags(html)
        extern = {a["href"]: a for t, a in alle if t == "a" and a.get("target") == "_blank"}
        self.assertIn("https://example.org/x", extern)
        self.assertIn("HTTP://example.org/y", extern)
        self.assertIn("mailto:a@example.org", extern)
        self.assertIn("https://example.org/nackt", extern)
        self.assertTrue(any(h.startswith("mailto:b@") or "&#" in h for h in extern if "b" in h), extern)
        self.assertTrue([a for t, a in alle if a.get("data-page") == "concepts/k2"])
        self.assertIn("#anker", hrefs(html))


class Literatur(Grundlage):
    NUTZLAST = "<img src=x onerror=alert(1)>"

    def setUp(self):
        super().setUp()
        self.karte("q1", "resource: https://example.org/q1\ncsl:\n  type: report\n  title: 'T<b>x</b>'\n"
                         "  author: [{family: '<svg onload=alert(2)>'}]\n"
                         f"  issued: {{date-parts: [['{self.NUTZLAST}']]}}\n  URL: 'javascript:alert(3)'\n")
        self.seite("k1", "Satz.[^q1]\n\n[^q1]: [Q](/sources/q1.md)\n", felder="sources: [{id: q1, resource: /sources/q1.md}]\n")

    def eintrag(self, ohne_pandoc: bool) -> dict:
        kopf = "okfw.shutil.which = lambda name: None\n" if ohne_pandoc else ""
        return self.py(kopf + "n = wiki.reader_html()\nresult = {'apa': n['sources/q1']['apa'], "
                       "'html': n['concepts/k1']['html'], 'ref': n['concepts/k1']['refs'][0]['entry']}")

    def pruefe(self, r: dict) -> None:
        for teil in (r["apa"], r["html"], r["ref"]):
            for tag, attrs in tags(teil):
                self.assertNotIn(tag, ("img", "svg", "script"), teil)
                self.assertFalse([k for k in attrs if k.startswith("on")], teil)
            self.assertFalse([h for h in hrefs(teil) if h.startswith("javascript")], teil)

    def test_ersatzform_ohne_pandoc(self):
        r = self.eintrag(True)
        self.pruefe(r)
        self.assertIn("&lt;img", r["apa"])
        self.assertIn("javascript:alert(3)", r["apa"])   # als Text, nicht als Link

    def test_mit_pandoc(self):
        if not self.py("result = okfw.shutil.which('pandoc')"):
            self.skipTest("pandoc fehlt")
        self.pruefe(self.eintrag(False))

    def test_dateiname_bricht_nicht_aus_dem_href_aus(self):
        self.karte('q"onmouseover="alert(1)', "resource: https://example.org/q\n")
        self.seite("k2", "Satz.[^a]\n\n[^a]: Fundstelle\n",
                   felder="sources: [{id: a, resource: '/sources/q\"onmouseover=\"alert(1).md'}]\n")
        wiki_html = self.py("okfw.shutil.which = lambda name: None\nresult = wiki.reader_html()['concepts/k2']['html']")
        verweise = [a for t, a in tags(wiki_html) if a.get("class") == "cite-ref"]
        self.assertEqual(len(verweise), 1, wiki_html)
        self.assertNotIn("onmouseover", verweise[0])
        self.assertIn('onmouseover="', verweise[0]["href"])

    def test_pandoc_ausgabe_wird_bereinigt(self):
        r = self.py('result = okfw.clean_entry(\'<a href="javascript:x" onclick="y" class="uri">a</a> <em>b</em> '
                    '<a href="https://example.org/?a=1&amp;b=2">c</a><img src=x onerror=y><script>z</script> &lt;d&gt;\')')
        self.assertEqual(r, '<a class="uri">a</a> <em>b</em> <a href="https://example.org/?a=1&amp;b=2">c</a>z &lt;d&gt;')

    def test_unlesbare_csl_felder_brechen_den_bau_nicht_ab(self):
        for i, felder in enumerate(("issued: 2020\n  author: x\n", "issued: {date-parts: []}\n  author: [1]\n",
                                    "issued: {date-parts: 2020}\n  title: 5\n  URL: 7\n")):
            self.karte(f"w{i}", f"csl:\n  type: report\n  {felder}")
        self.assertIn(MARKE, self.bau())
        self.core("lint", ok=None)
        text = self.py("okfw.shutil.which = lambda name: None\nresult = wiki.apa_catalog()['w0']['entry']")
        self.assertIn("(o. D.)", text)


class Lizenz(Grundlage):
    def test_nur_erlaubte_schemata_kommen_an(self):
        self.karte("q1", "license: {name: L, url: 'javascript:alert(1)'}\n")
        self.karte("q2", "license: {name: L, url: ' JAVA&#83;CRIPT:alert(1)'}\n")
        self.karte("q3", "license: {name: L, url: 'https://example.org/lizenz'}\n")
        knoten = {n["id"]: n for n in daten(self.bau())["nodes"]}
        self.assertEqual(knoten["sources/q1"]["license"]["url"], "")
        self.assertEqual(knoten["sources/q2"]["license"]["url"], "")
        self.assertEqual(knoten["sources/q3"]["license"]["url"], "https://example.org/lizenz")

    def test_vorlage_prueft_auch_selbst(self):
        vorlage = (SKILL / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("safeHref(L.url) ? h(", vorlage)
        self.assertNotIn("const name = L.url ?", vorlage)


class Csp(Grundlage):
    def test_meta_und_nonce(self):
        text = self.bau()
        meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', text)
        self.assertTrue(meta)
        nonce = re.search(r"script-src 'nonce-([\w-]+)';", meta.group(1)).group(1)
        self.assertEqual(meta.group(1), f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                                        "img-src data: blob:; font-src data:; base-uri 'none'; form-action 'none'")
        self.assertLess(meta.start(), text.index("<script"))
        skripte = re.findall(r"<script\b[^>]*>", text)
        ausfuehrbar = [s for s in skripte if "application/json" not in s]
        self.assertEqual(len(ausfuehrbar), 2, skripte)
        for tag in ausfuehrbar:
            self.assertEqual(tag, f'<script nonce="{nonce}">')
        self.assertEqual(text.count(MARKE), 1)
        self.assertTrue(daten(text)["nodes"] is not None)
        nochmal = re.search(r"'nonce-([\w-]+)'", self.bau()).group(1)
        self.assertNotEqual(nonce, nochmal)

    def test_vorlage_braucht_keine_inline_handler_und_laedt_nichts_fremdes(self):
        vorlage = (SKILL / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertEqual([t for t in tags(vorlage.split("<script>")[0]) if any(k.startswith("on") for k in t[1])], [])
        self.assertIsNone(re.search(r"<(img|link|iframe|object|embed)\b", vorlage))

    def test_ohne_csp_fuer_serve(self):
        r = self.py("text = wiki.render_dashboard(csp=False)\nresult = ['Content-Security-Policy' in text, 'nonce' in "
                    "text.split('<body')[0], text.count('<script>')]")
        self.assertEqual(r, [False, False, 2])
        serve = (SKILL / "okfw_serve.py").read_text(encoding="utf-8")
        self.assertIn(".render_dashboard(csp=False)", serve)

    def test_oeffentliche_ausgabe_traegt_sie_auch(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        self.seite("k1", 'Text {: onanimationstart="alert(1)" }\n\n[x](javascript:alert(2))\n')
        text = self.bau("--oeffentlich")
        self.assertIn("Content-Security-Policy", text)
        html = {n["id"]: n for n in daten(text)["nodes"]}["concepts/k1"]["html"]
        self.assertFalse([k for _, a in tags(html) for k in a if k.startswith("on")])
        self.assertEqual(hrefs(html), [])


class CssPruefung(Grundlage):
    def problem(self, css: str):
        return self.py(f"result = okfw.css_problem({css!r})")

    def test_kommentarzeichen_in_zeichenketten_verstecken_nichts(self):
        faelle = ['html{--x:"/*"} header{background:url(http://x/a)} html{--y:"*/"}',
                  "html{--x:'/*'} header{background:url(http://x/a)} html{--y:'*/'}",
                  '@charset "/*"; @import url(http://x/a.css); a{content:"*/"}',
                  '@charset "/*"; @import "http://x/a.css"; a{content:"*/"}',
                  # Zeichenkette endet am Zeilenende: hier ist erst das dritte " wieder ein Anfang
                  'a{b:"x\n"/*" c{background:url(http://x/a)} "*/"}',
                  # url( ohne Anführungszeichen kennt keine Kommentare
                  "a{b:url(#a/*)} c{background:url(http://x/a)} d{e:url(#*/)}",
                  "a{b:\\75rl(#a/*)} c{background:url(http://x/a)} d{e:url(#*/)}",
                  "a{b:url(x\"\n/*)} c{background:url(http://x/a)} d{e:url(#*/)}",
                  "a{b:url((\"\n/*)} c{background:url(http://x/a)} d{e:url(#*/)}",
                  "a{background:src('http://x/a')}", "a{background:image('http://x/a')}",
                  "a{behavior:url(#a)}", "a{-ms-behavior:url(#a)}", "a{scroll-behavior:smooth;behavior:url(#a)}"]
        for css in faelle:
            with self.subTest(css):
                self.assertIsNotNone(self.problem(css))

    def test_harmloses_bleibt_erlaubt(self):
        faelle = ['a{content:"/*"} b{color:red}', "/* url(http://x) @import 'y'; */ a{color:red}",
                  'a{content:"\\"/*"} /* url(http://x) */ b{color:red}', "a{background:url(#a)} b{mask:url('#b')}",
                  "@font-face{font-family:x;src:url(data:font/woff2;base64,AA)}", ".w-1\\/2{color:red}",
                  "html{scroll-behavior:smooth}", "a{overscroll-behavior:contain}"]
        for css in faelle:
            with self.subTest(css):
                self.assertIsNone(self.problem(css))

    def design(self, css: str) -> None:
        (self.root / "design.css").write_text(css, encoding="utf-8")

    def test_daten_marke_im_design_bricht_serve_nicht(self):
        for css in (f"/* {MARKE} */ a{{color:red}}", 'a{content:"<script type=\\"application/json\\" id=\\"okf-data\\">"}',
                    "/* <SCRIPT> */ a{color:red}"):
            with self.subTest(css):
                self.design(css)
                self.assertEqual(self.py(f"result = wiki.render_dashboard(csp=False).count({MARKE!r})"), 1)
                self.assertEqual(self.bau().count(MARKE), 1)

    def test_platzhalter_im_design_werden_nicht_ersetzt(self):
        self.design("/* __DATA__ __TITLE__ /*__FONTS__*/ a{color:red}")
        text = self.bau()
        self.assertIn("/* __DATA__ __TITLE__ /*__FONTS__*/", text)
        self.assertEqual(text.count(MARKE), 1)
        self.assertTrue(daten(text)["nodes"] is not None)


class SvgBereinigung(Grundlage):
    def clean(self, svg, label: str = "") -> dict:
        return self.py(f"""
try:
    sauber, entfernt = okfw.clean_svg({svg!r}, label={label!r})
    result = {{'svg': sauber, 'entfernt': entfernt}}
except okfw.FigureError as exc:
    result = {{'fehler': str(exc)}}""")

    def test_svg_value_ok(self):
        boese = ["javascript:alert(1)", "JAVASCRIPT:x", "data:image/png;base64,AA", "vbscript:x", "expression(1)",
                 "behavior:url(#a)", "<x", "url(http://x)", "url(#a) url(//x)", "url(#a", "\\75rl(http://x)", "U\\52L(#a)",
                 "image-set('http://x' 1x)", "-webkit-image-set('http://x' 1x)", "src('http://x')", "image('http://x')",
                 "cross-fade(url(#a),url(#b))", "ur/**/l(http://x)"]
        gut = ["red", "#fff", "url(#a)", "url('#a')", "translate(1 2)", "metadata:x", "Helvetica, sans-serif", "var(--x)"]
        r = self.py(f"result = [[okfw.svg_value_ok(v) for v in {boese!r}], [okfw.svg_value_ok(v) for v in {gut!r}]]")
        self.assertEqual(dict(zip(boese, r[0], strict=True)), dict.fromkeys(boese, False))
        self.assertEqual(dict(zip(gut, r[1], strict=True)), dict.fromkeys(gut, True))

    def test_maskierung_und_ladefunktionen_in_jedem_attribut(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"'
        r = self.clean(kopf + '><rect fill="\\75rl(http://x/a#b)" clip-path="\\75 rl(http://x/b#b)" stroke="U\\52L(http://x/c)" '
                              'style="background:image-set(&quot;http://x/d&quot; 1x)"/>'
                              '<text font-family="image-set(\'http://x/e\' 1x)" fill="src(\'http://x/f\')">t</text></svg>')
        self.assertNotIn("http://x", r["svg"])
        self.assertNotIn("\\", r["svg"])

    def test_style_nur_erlaubte_eigenschaften(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"'
        r = self.clean(kopf + ' style="position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:99999;inset:0;'
                              'background:#fff;transform:scale(9);fill:red"><rect style="POSITION:absolute"/>'
                              '<text style="font-size:3px;text-anchor:middle;stroke-width:2;opacity:.5;'
                              'dominant-baseline:central;font-family:sans-serif;animation:x 1s;cursor:wait;--v:1">t</text></svg>')
        for verboten in ("position", "POSITION", "z-index", "inset", "width:100", "height", "top", "left", "transform",
                         "background", "animation", "cursor", "--v"):
            self.assertNotIn(verboten, r["svg"].replace("stroke-width", ""), verboten)
        self.assertIn('style="fill:red"', r["svg"])
        self.assertIn('style="font-size:3px;text-anchor:middle;stroke-width:2;opacity:.5;dominant-baseline:central;'
                      'font-family:sans-serif"', r["svg"])
        self.assertIn("Stil position", r["entfernt"])
        self.assertIn("<rect />", r["svg"])   # nichts übrig: das Attribut entfällt

    def test_transform_nur_innen(self):
        r = self.clean('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" transform="scale(50)">'
                       '<g transform="translate(1 2)"><rect/></g></svg>')
        self.assertNotIn("scale", r["svg"])
        self.assertIn('transform="translate(1 2)"', r["svg"])

    def test_nur_utf8(self):
        quelle = '<!DOCTYPE svg [<!ENTITY x "ENTITAET">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><text>&x;</text></svg>'
        gut = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><text>Größe</text></svg>'
        for name, daten_ in {"utf-16-le": quelle.encode("utf-16-le"), "utf-16-be": quelle.encode("utf-16-be"),
                             "utf-16 BOM": quelle.encode("utf-16"), "utf-32": quelle.encode("utf-32"),
                             "utf-16 ohne Doctype": gut.encode("utf-16-le"), "NUL": gut.encode() + b"\x00",
                             "latin-1": gut.encode("latin-1")}.items():
            with self.subTest(name):
                self.assertIn("UTF-8", self.clean(daten_).get("fehler", ""), name)
        self.assertIn("Größe", self.clean(gut.encode("utf-8-sig"))["svg"])
        # eine andere Angabe im Kopf ändert nichts: gelesen wird UTF-8
        kopf = '<?xml version="1.0" encoding="ISO-8859-1"?>'.encode() + gut.encode()
        self.assertIn("Größe", self.clean(kopf)["svg"])

    def test_nur_der_standardkopf_wird_uebergangen(self):
        kennung, adresse = "-//W3C//DTD SVG 1.1//EN", "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"
        kopf = f'<!DOCTYPE svg PUBLIC "{kennung}" "{adresse}">'
        wurzel = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><text>MARKE</text></svg>'
        for name, quelle in {"allein": kopf + wurzel, "einfache Anführungszeichen": kopf.replace('"', "'") + wurzel,
                             "mit Kopf und Kommentar": f'<?xml version="1.0"?>\n<!-- a -->\n<!-- b -->\n{kopf}\n{wurzel}',
                             "BOM": f"\ufeff{kopf}{wurzel}",
                             "SVG 1.0": '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.0//EN" "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd">' + wurzel}.items():
            with self.subTest(name):
                r = self.clean(quelle.encode())
                self.assertIn("MARKE", r.get("svg", ""), r)
                self.assertNotIn("DOCTYPE", r["svg"])
        for name, quelle in {"internes Subset": f'{kopf[:-1]} [<!ENTITY x "ENTITAET">]>' + wurzel.replace("MARKE", "&x;"),
                             "leeres Subset": f"{kopf[:-1]} []>" + wurzel, "ohne PUBLIC": "<!DOCTYPE svg>" + wurzel,
                             "SYSTEM": '<!DOCTYPE svg SYSTEM "file:///etc/passwd">' + wurzel,
                             "fremde Adresse": f'<!DOCTYPE svg PUBLIC "{kennung}" "http://evil.example/x.dtd">' + wurzel,
                             "falsches Paar": f'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.0//EN" "{adresse}">' + wurzel,
                             "fremde Kennung": f'<!DOCTYPE svg PUBLIC "-//evil//EN" "{adresse}">' + wurzel,
                             "anderes Wurzelelement": kopf.replace("DOCTYPE svg", "DOCTYPE html") + wurzel,
                             "kleingeschrieben": kopf.replace("DOCTYPE", "doctype") + wurzel,
                             "zweimal": kopf + kopf + wurzel, "dahinter ENTITY": kopf + '<!ENTITY x "y">' + wurzel,
                             "im Kommentar davor": f'<!-- <!ENTITY x "y"> -->{kopf}{wurzel}',
                             "im Kommentar dahinter": f"{kopf}<!-- {kopf} -->{wurzel}",
                             "im Dokument": wurzel.replace("MARKE", kopf),
                             "als CDATA": wurzel.replace("MARKE", f"<![CDATA[{kopf}]]>"),
                             "hinter Text": f"x{kopf}{wurzel}",
                             "Lücke im Schlüsselwort": f"<!DOC<!-- -->TYPE{kopf[9:]}{wurzel}"}.items():
            with self.subTest(name):
                r = self.clean(quelle.encode())
                self.assertIn("fehler", r, r)
                self.assertNotIn("ENTITAET", str(r))
        # hinter dem Kopf bleiben eigene Entities ungültiges XML
        self.assertIn("kein gültiges XML", self.clean(kopf + wurzel.replace("MARKE", "&nbsp;"))["fehler"])

    def test_kopf_pruefung_bleibt_bei_grosser_datei_schnell(self):
        for name, quelle in {"ein Kommentar": "<!--" + "x" * 190_000 + '--><svg viewBox="0 0 1 1"/>',
                             "viele Kommentare": "<!--a-->" * 24_000 + '<svg viewBox="0 0 1 1"/>',
                             "ohne Ende": "<!--" + "-" * 190_000}.items():
            with self.subTest(name):
                r = self.py(f"""
import time
t = time.monotonic()
okfw.svg_ohne_kopf({quelle!r})
result = time.monotonic() - t""")
                self.assertLess(r, 2)

    def test_style_regeln_lassen_nichts_durch(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        fuss = '<rect class="a" id="i"/><text class="a">t</text></svg>'
        for name, regel in {"url": ".a{fill:url(http://evil.example/x)}", "url ohne Schema": ".a{fill:url(//evil.example/x)}",
                            "maskiert": ".a{fill:\\75rl(http://evil.example/x)}", "Kommentar im url": ".a{fill:ur/**/l(http://evil.example/x)}",
                            "Kommentar im Namen": ".a{fi/**/ll:url(http://evil.example/x)}",
                            "image-set": ".a{fill:image-set('http://evil.example/x' 1x)}", "image": ".a{fill:image('http://evil.example/x')}",
                            "Font src": "@font-face{font-family:evil;src:url(http://evil.example/f.woff)}",
                            "import": "@import url(http://evil.example/a.css);", "import Text": '@import "http://evil.example/a.css";',
                            "Zeile vor url": ".a{fill:red;marker-start:url(http://evil.example/m)}",
                            "url an #i": "#i{clip-path:url(http://evil.example/c)}", "Stern": "*{font:url(http://evil.example/x)}",
                            "javascript": ".a{fill:javascript:evil.example}", "data": ".a{fill:data:image/png;base64,evil.example}",
                            "expression": ".a{fill:expression(evil.example)}", "Klammer ohne Ende": ".a{fill:url(http://evil.example/x",
                            "style-Ende als CDATA": "<![CDATA[</style><script>evil.example</script>.a{fill:<script>evil.example</script>}]]>"}.items():
            with self.subTest(name):
                r = self.clean(f"{kopf}<style>{regel}</style>{fuss}")
                self.assertNotIn("evil.example", r.get("svg", ""), r)
                self.assertNotIn("<style", r.get("svg", ""), r)
                self.assertNotIn("<script", r.get("svg", ""), r)
                self.assertNotIn("class=", r.get("svg", ""), r)

    def test_style_regeln_heben_nichts_aus_dem_rahmen(self):
        r = self.clean('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><style>'
                       'svg{position:fixed;inset:0;width:100vw;z-index:9;transform:scale(9);fill:red}'
                       '*{animation:x 1s;cursor:wait;--v:1;background:url(#a)}.a{transform:translate(1,1)}</style><rect class="a"/></svg>')
        for verboten in ("position", "inset", "100vw", "z-index", "transform", "animation", "cursor", "--v", "background"):
            self.assertNotIn(verboten, r["svg"], verboten)
        self.assertIn('style="fill:red"', r["svg"])
        self.assertIn("Stil position", r["entfernt"])

    def test_style_regeln_blaehen_nichts_auf(self):
        """Eine lange Regel auf vielen Elementen wüchse sonst auf Hunderte MB; und die Datei muss die Grenze beim Bau bestehen."""
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><style>'
        for name, quelle in {"lange Regel": kopf + ".a{font-family:" + "x" * 150_000 + "}</style>" + '<g class="a"/>' * 3000 + "</svg>",
                             "viele Regeln": kopf + ".a{fill:red}" * 8000 + "</style>" + '<g class="a"/>' * 4000 + "</svg>",
                             "dieselbe Klasse oft": kopf + ".a{fill:red}" * 15_000 + "</style>" + '<g class="' + "a " * 5000 + '"/>' * 200 + "</svg>"}.items():
            with self.subTest(name):
                r = self.py(f"""
import time
t = time.monotonic()
try:
    sauber = okfw.clean_svg({quelle!r})[0]
    groesse = len(sauber.encode())
except okfw.FigureError as exc:
    sauber, groesse = str(exc), 0
result = [time.monotonic() - t, groesse, sauber[:120]]""")
                self.assertLess(r[0], 10)
                self.assertLessEqual(r[1], 200 * 1024, r[2])

    def test_verschachteltes_use_waechst_nicht_exponentiell(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><defs><g id="g0"><rect width="1" height="1"/></g>'
        stufen = "".join(f'<g id="g{i}"><use href="#g{i - 1}"/><use href="#g{i - 1}"/></g>' for i in range(1, 25))
        r = self.clean(kopf + stufen + '</defs><use href="#g24"/></svg>')   # 2^24 Elemente beim Rendern, nur ~80 im Text
        self.assertIn("nach dem Auflösen von use", r.get("fehler", ""), r)

    def test_use_zyklus_wird_abgelehnt(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        for name, inhalt in {"auf sich selbst": '<use id="a" href="#a"/>',
                             "über eine Gruppe": '<g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g>',
                             "auf den Vorfahren": '<g id="a"><g><use href="#a"/></g></g>'}.items():
            with self.subTest(name):
                self.assertIn("Zyklus", self.clean(kopf + inhalt + "</svg>").get("fehler", ""), name)

    def test_normales_use_bleibt_gueltig(self):
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><defs><g id="k">'
        gruppe = '<rect width="1" height="1"/>' * 50
        r = self.clean(kopf + gruppe + '</g></defs>' + '<use href="#k"/>' * 100 + '</svg>')   # 5000 Elemente aufgelöst
        self.assertNotIn("fehler", r)
        self.assertEqual(r["svg"].count("<use"), 100)
        r = self.clean(kopf + gruppe + '</g></defs>' + '<use href="#k"/>' * 100 + '<use href="#fehlt"/></svg>')
        self.assertNotIn("fehler", r)   # Verweis ins Leere rendert nichts

    def test_groesse_in_clean_svg(self):
        r = self.py("""
try:
    okfw.clean_svg(b' ' * okfw.FIGURE_MAX_BYTES + b'<svg viewBox="0 0 1 1"/>')
    result = 'angenommen'
except okfw.FigureError as exc:
    result = str(exc)""")
        self.assertIn("200 KB", r)
        r = self.py("""
try:
    kopf = b'<svg viewBox="0 0 1 1"/>'
    okfw.clean_svg(kopf + b' ' * (okfw.FIGURE_MAX_BYTES - len(kopf)))
    result = 'angenommen'
except okfw.FigureError as exc:
    result = str(exc)""")
        self.assertEqual(r, "angenommen")

    def test_riesiges_svg_von_hand_abgelegt(self):
        ordner = self.root / "wiki" / "abbildungen"
        ordner.mkdir()
        (ordner / "riese.svg").write_text(GUT.replace("</svg>", " " * 210_000 + "</svg>"), encoding="utf-8")
        self.seite("k1", "![R](/abbildungen/riese.svg)\n")
        html = self.html("k1")
        self.assertIn("Abbildung nicht eingebunden: riese", html)
        self.assertNotIn("MARKE", html)
        self.assertIn("figure_missing", self.lint_codes("concepts/k1"))

    def test_symlink_aus_dem_bundle_wird_nicht_eingebettet(self):
        ordner = self.root / "wiki" / "abbildungen"
        ordner.mkdir()
        (ordner / "aussen.svg").symlink_to(self.draussen("x.svg", GUT))
        self.seite("k1", "![R](/abbildungen/aussen.svg)\n")
        html = self.html("k1")
        self.assertIn("Abbildung nicht eingebunden: aussen", html)
        self.assertNotIn("MARKE", html)
        befunde = json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
        self.assertTrue([f for f in befunde if f["code"] == "figure_missing" and "außerhalb" in f["message"]])

    def test_abbildungsordner_als_symlink(self):
        (self.root / "wiki" / "abbildungen").symlink_to(self.draussen("x.svg", GUT).parent)
        self.seite("k1", "![R](/abbildungen/x.svg)\n")
        self.assertNotIn("MARKE", self.html("k1"))


class SymlinksImWiki(Grundlage):
    def verknuepfen(self, ziel, name: str = "link") -> object:
        link = self.root / "wiki" / "concepts" / f"{name}.md"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(ziel)
        return link

    def test_datei_ausserhalb_ist_keine_seite(self):
        ziel = self.draussen("geheim.md", SEITE + "GEHEIMNIS\n")
        self.verknuepfen(ziel)
        self.assertNotIn("concepts/link", self.py("result = [p.id for p in wiki.pages()]"))
        self.assertNotIn("GEHEIMNIS", self.bau())
        befunde = json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
        self.assertTrue([f for f in befunde if f["code"] == "link_leaves_bundle" and "Symlink" in f["message"]], befunde)

    def test_schreiben_durch_den_symlink_scheitert(self):
        inhalt = SEITE + "GEHEIMNIS\n"
        ziel = self.draussen("geheim.md", inhalt)
        self.verknuepfen(ziel)
        for args, stdin in ((("body", "concepts/link", "-"), "UEBERSCHRIEBEN"),
                            (("verify", "concepts/link", "--by", "human:t"), None),
                            (("body", "concepts/link", "--nachziehen"), None)):
            with self.subTest(args):
                done = self.core(*args, stdin=stdin, ok=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(ziel.read_text(encoding="utf-8"), inhalt)

    def test_symlink_im_bundle_bleibt_erlaubt(self):
        self.seite("echt", "ECHT\n")
        self.verknuepfen(self.root / "wiki" / "concepts" / "echt.md")
        self.assertIn("concepts/link", self.py("result = [p.id for p in wiki.pages()]"))
        self.assertNotIn("link_leaves_bundle", self.lint_codes())

    def test_ins_leere_und_ordner_mit_md_brechen_nichts(self):
        self.verknuepfen(self.base / "fehlt.md")
        (self.root / "wiki" / "concepts" / "ordner.md").mkdir()
        self.assertEqual(self.py("result = [p.id for p in wiki.pages()]"), [])
        self.assertIn(MARKE, self.bau())
        self.assertIn("link_leaves_bundle", self.lint_codes())

    def test_new_nicht_durch_symlink_ins_leere(self):
        ziel = self.base / "draussen-neu.md"
        link = self.root / "wiki" / "sources" / "q1.md"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(ziel)
        done = self.core("new", "source", "q1", "--title", "T", "--access", "citation", ok=False)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(ziel.exists())

    def test_new_nicht_in_einen_ordner_ausserhalb(self):
        aussen = self.base / "aussen"
        aussen.mkdir()
        (self.root / "wiki" / "sources").rmdir()
        (self.root / "wiki" / "sources").symlink_to(aussen)
        self.assertEqual(self.core("new", "source", "q1", "--title", "T", "--access", "citation", ok=False).returncode, 2)
        self.assertEqual(list(aussen.iterdir()), [])

    def test_abbildung_nicht_durch_symlink(self):
        svg = self.datei("a.svg", GUT)
        ziel = self.base / "draussen-neu.svg"
        ordner = self.root / "wiki" / "abbildungen"
        ordner.mkdir()
        (ordner / "x.svg").symlink_to(ziel)
        for args in (("abbildung", "x", "--file", str(svg)), ("abbildung", "x", "--file", str(svg), "--ersetzen")):
            done = self.core(*args, ok=False)
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertIn("Symlink", done.stderr)
            self.assertFalse(ziel.exists())
        vorhanden = self.draussen("da.svg", "ALT")
        (ordner / "y.svg").symlink_to(vorhanden)
        self.core("abbildung", "y", "--file", str(svg), "--ersetzen", ok=False)
        self.assertEqual(vorhanden.read_text(encoding="utf-8"), "ALT")

    def test_abbildung_nicht_in_einen_ordner_ausserhalb(self):
        aussen = self.base / "aussen"
        aussen.mkdir()
        (self.root / "wiki" / "abbildungen").symlink_to(aussen)
        self.core("abbildung", "x", "--file", str(self.datei("a.svg", GUT)), ok=False)
        self.assertEqual(list(aussen.iterdir()), [])

    def test_ereignisstrom_nicht_durch_symlink(self):
        ziel = self.draussen("log.txt")
        ereignisse = self.root / "wiki" / ".events.jsonl"
        ereignisse.unlink(missing_ok=True)
        ereignisse.symlink_to(ziel)
        self.assertEqual(self.core("event", "note", "x", "d", ok=False).returncode, 2)
        self.assertEqual(ziel.read_text(encoding="utf-8"), "UNVERAENDERT\n")

    def test_projektion_ersetzt_den_symlink(self):
        self.seite("k1", "Text\n")
        self.core("rebuild")
        ziel = self.draussen("index.txt")
        index = self.root / "wiki" / "index.md"
        index.unlink()
        index.symlink_to(ziel)
        self.assertNotEqual(self.core("rebuild", "--check", ok=None).returncode, 0)
        self.core("rebuild")
        self.assertEqual(ziel.read_text(encoding="utf-8"), "UNVERAENDERT\n")
        self.assertFalse(index.is_symlink())
        self.assertIn("okf_version", index.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in (self.root / "wiki").iterdir() if p.name.startswith(".index.md.")], [])

    def test_dashboard_ersetzt_den_symlink_am_standardpfad(self):
        ziel = self.draussen("bericht.html")
        pfad = self.root / "build" / "wiki-dashboard.html"
        pfad.parent.mkdir()
        pfad.symlink_to(ziel)
        self.core("dashboard")
        self.assertEqual(ziel.read_text(encoding="utf-8"), "UNVERAENDERT\n")
        self.assertFalse(pfad.is_symlink())

    def test_zwischendatei_behaelt_rechte_und_raeumt_auf(self):
        ziel = self.base / "datei.txt"
        ziel.write_text("alt", encoding="utf-8")
        os.chmod(ziel, 0o640)
        r = self.py(f"""
import pathlib
ziel = pathlib.Path({str(ziel)!r})
okfw.replace_file(ziel, 'neu')
neu = ziel.with_name('frisch.txt')
okfw.replace_file(neu, 'x')
fehler = ''
real = okfw.os.replace
okfw.os.replace = lambda a, b: (_ for _ in ()).throw(OSError('geht nicht'))
try:
    okfw.replace_file(ziel, 'kaputt')
except OSError as exc:
    fehler = str(exc)
okfw.os.replace = real
result = [ziel.read_text(), oct(ziel.stat().st_mode & 0o777), oct(neu.stat().st_mode & 0o777), fehler,
          sorted(p.name for p in ziel.parent.iterdir() if p.name.startswith('.'))]""")
        self.assertEqual(r, ["neu", "0o640", "0o644", "geht nicht", []])


class FremdePfade(Grundlage):
    """Eine fremde wiki.toml darf den Kern nicht außerhalb des Projektordners schreiben lassen (Stop-Hook: rebuild)."""

    def toml(self, ersetze: dict | None = None, zusatz: str = "") -> None:
        pfad = self.root / "wiki.toml"
        text = pfad.read_text(encoding="utf-8")
        for alt, neu_ in (ersetze or {}).items():
            self.assertIn(alt, text)
            text = text.replace(alt, neu_, 1)
        pfad.write_text(text + "\n" + zusatz, encoding="utf-8")

    def verweigert(self, *args, ziel=None, inhalt: str = "UNVERAENDERT\n"):
        done = self.core(*args, ok=False)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("außerhalb des Projektordners", done.stderr)
        if ziel is not None:
            self.assertEqual(ziel.read_text(encoding="utf-8"), inhalt)
        return done

    def test_jeder_schreibpfad_wird_geprueft(self):
        aussen = self.draussen("ziel.txt")
        faelle = {
            "bundle": ({'bundle = "wiki"': 'bundle = "../draussen"'}, ""),
            "events": ({'events = "wiki/.events.jsonl"': 'events = "../draussen/ziel.txt"'}, ""),
            "raw": ({'raw = "raw"': f'raw = "{self.base / "draussen"}"'}, ""),
            "csl": (None, '[export.csl]\npath = "../draussen/ziel.txt"\n'),
            "bibliography": (None, '[export.bibliography]\npath = "/tmp/okfw-fremd.md"\n'),
            "dashboard": (None, '[dashboard]\npath = "../draussen/ziel.txt"\n'),
            "dashboard public": (None, '[dashboard]\npublic_path = "../draussen/ziel.txt"\n'),
        }
        for name, (ersetze, zusatz) in faelle.items():
            with self.subTest(name):
                vorher = (self.root / "wiki.toml").read_text(encoding="utf-8")
                self.toml(ersetze, zusatz)
                self.verweigert("rebuild", ziel=aussen)
                self.verweigert("status", ziel=aussen)
                (self.root / "wiki.toml").write_text(vorher, encoding="utf-8")

    def test_rebuild_schreibt_nicht_aus_dem_projekt(self):
        aussen = self.draussen("export.json")
        self.toml(zusatz='[export.csl]\npath = "../draussen/export.json"\n')
        self.verweigert("rebuild", ziel=aussen)
        done = self.core("rebuild", "--hook")   # der Stop-Hook meldet, bricht aber nicht ab
        self.assertIn("außerhalb des Projektordners", done.stdout)
        self.assertEqual(aussen.read_text(encoding="utf-8"), "UNVERAENDERT\n")

    def test_symlink_im_pfad_fuehrt_nicht_hinaus(self):
        aussen = self.draussen("export.json")
        (self.root / "ablage").symlink_to(aussen.parent, target_is_directory=True)
        self.toml(zusatz='[export.csl]\npath = "ablage/export.json"\n')
        self.verweigert("rebuild", ziel=aussen)

    def test_pfade_im_projekt_bleiben_gueltig(self):
        self.toml(zusatz='[export.csl]\npath = "export/csl.json"\n[dashboard]\npath = "build/../build/d.html"\n')
        self.core("rebuild")
        self.assertTrue((self.root / "export" / "csl.json").is_file())

    def test_out_auf_der_kommandozeile_bleibt_frei(self):
        ziel = self.base / "draussen" / "dash.html"
        ziel.parent.mkdir()
        self.core("dashboard", "--out", str(ziel))
        self.assertTrue(ziel.is_file())

    def test_vorlage_von_ausserhalb_wird_nicht_gelesen(self):
        aussen = self.draussen("geheim.md", "GEHEIM\n")
        for wert in ("../draussen/geheim.md", str(aussen)):
            with self.subTest(wert):
                vorher = (self.root / "wiki.toml").read_text(encoding="utf-8")
                self.toml({"[types.concept]": f'[types.concept]\ntemplate = "{wert}"'})
                done = self.core("new", "concept", "x", "--title", "X", "--description", "D", ok=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("außerhalb des Projektordners", done.stderr)
                self.assertFalse((self.root / "wiki" / "concepts" / "x.md").exists())
                (self.root / "wiki.toml").write_text(vorher, encoding="utf-8")


class FreigabeFelderBeiNew(Grundlage):
    """--set status=stable und --set verified=[…] erzeugten sonst eine Karte, die als freigegeben gilt."""

    def test_status_und_verified_ueber_set_sind_bedienfehler(self):
        for feld, wert in (("status", "stable"), ("verified", "[]")):
            with self.subTest(feld):
                done = self.core("new", "source", "q1", "--title", "T", "--access", "citation",
                                 "--set", f"{feld}={wert}", ok=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(feld, done.stderr)
                self.assertIn("verify", done.stderr)
                self.assertFalse((self.root / "wiki" / "sources" / "q1.md").exists())

    def test_andere_felder_bleiben_erlaubt(self):
        self.core("new", "source", "q1", "--title", "T", "--access", "citation",
                  "--set", "evidenz=peer-reviewed", "--set", "raw_packet=raw/q1")
        self.assertEqual(self.py("result = wiki.parse(wiki.page_path('sources/q1')).fm['evidenz']"), "peer-reviewed")


class RohquellenManifest(Grundlage):
    """Ein feindliches manifest.json durfte lint abbrechen oder den Kern außerhalb des Projekts lesen lassen."""

    def paket(self, slug: str = "q1", karte: bool = True) -> Path:
        ordner = self.root / "raw" / slug / "original"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "a.txt").write_text("INHALT\n", encoding="utf-8")
        manifest = self.root / "raw" / slug / "manifest.json"
        if karte:
            self.karte(slug, f"raw_packet: raw/{slug}\n"
                             "resource: https://example.org/q\nlicense: {name: L, url: https://example.org/l}\n"
                             "csl:\n  type: report\n  title: T\n  author: [{literal: A}]\n"
                             "  issued: {date-parts: [[2020]]}\n")
        return manifest

    def ordentlich(self, manifest: Path) -> None:
        import hashlib
        sha = hashlib.sha256(b"INHALT\n").hexdigest()
        manifest.write_text(json.dumps({"files": [{"path": "original/a.txt", "sha256": sha}],
                                        "license": {"name": "L", "url": "https://example.org/l"},
                                        "retrieved_at": "2026-01-02T03:04:05Z"}), encoding="utf-8")

    def befund(self, code: str, anteil: str = "") -> list[dict]:
        befunde = json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
        return [f for f in befunde if f["code"] == code and anteil in f["message"]]

    # ---------------------------------------------------------------- gültiges Paket

    def test_gueltiges_paket_bleibt_befundfrei(self):
        manifest = self.paket()
        self.ordentlich(manifest)
        for code in ("raw_missing", "raw_tampered", "raw_uningested", "fulltext_absent"):
            self.assertEqual(self.befund(code), [], code)

    # ---------------------------------------------------------------- raw_packet außerhalb

    def test_raw_packet_ausserhalb_wird_nicht_gelesen_oder_gehasht(self):
        ziel = self.draussen("geheim.txt", "AUSSEN\n")
        self.karte("q1", "raw_packet: ../draussen\n"
                         "resource: https://example.org/q\nlicense: {name: L, url: https://example.org/l}\n")
        r = self.py("""
import pathlib
from unittest import mock
echt = okfw.sha256_of
def wach(pfad):
    assert pathlib.Path(pfad).resolve().is_relative_to(wiki.root.resolve()), pfad
    return echt(pfad)
with mock.patch.object(okfw, 'sha256_of', side_effect=wach):
    befunde = wiki.lint()
result = [(f.code, f.message) for f in befunde if f.path == 'wiki/sources/q1.md']""")
        self.assertTrue([f for f in r if f[0] == "raw_missing" and "außerhalb" in f[1]], r)
        self.assertEqual(ziel.read_text(encoding="utf-8"), "AUSSEN\n")

    def test_manifest_datei_ausserhalb_wird_nicht_gelesen(self):
        self.draussen("geheim.txt", "AUSSEN\n")
        self.karte("q1", "raw_packet: ../draussen\n"
                         "resource: https://example.org/q\nlicense: {name: L, url: https://example.org/l}\n")
        r = self.py("lic = wiki.manifest_license('../draussen')\nresult = lic")
        self.assertIsNone(r)

    # ---------------------------------------------------------------- kaputtes Manifest

    def test_manifest_ungueltiges_json(self):
        manifest = self.paket()
        manifest.write_text("[", encoding="utf-8")
        self.assertTrue(self.befund("raw_tampered", "nicht lesbar"))
        self.assertIn(MARKE, self.bau())

    def test_manifest_keine_zuordnung_und_files_keine_liste(self):
        for name, inhalt in (("Zuordnung", '"x"'), ("Liste", '{"files": "x"}'), ("ohne files", "{}"),
                             ("Eintrag keine Zuordnung", '{"files": ["x"]}'),
                             ("Eintrag ohne path", '{"files": [{"sha256": "y"}]}'),
                             ("path kein Text", '{"files": [{"path": 5}]}')):
            with self.subTest(name):
                manifest = self.paket()
                manifest.write_text(inhalt, encoding="utf-8")
                self.assertTrue(self.befund("raw_tampered"), name)
                self.assertIn(MARKE, self.bau())

    def test_manifest_gross_und_tief_bricht_nichts(self):
        manifest = self.paket()
        manifest.write_text("x: " + "[" * 20000 + "]" * 20000, encoding="utf-8")
        self.assertTrue(self.befund("raw_tampered"))
        self.assertIn(MARKE, self.bau())

    # ---------------------------------------------------------------- Eintrag mit path außerhalb

    def test_eintrag_path_ausserhalb_wird_nicht_gelesen_oder_gehasht(self):
        ziel = self.draussen("geheim.txt", "AUSSEN\n")
        manifest = self.paket()
        manifest.write_text(json.dumps({"files": [
            {"path": "original/a.txt", "sha256": "0" * 64},
            {"path": "../../../draussen/geheim.txt", "sha256": "0" * 64},
            {"path": "../../../..", "sha256": "0" * 64}]}), encoding="utf-8")
        r = self.py("""
import pathlib
from unittest import mock
echt = okfw.sha256_of
def wach(pfad):
    assert pathlib.Path(pfad).resolve().is_relative_to(wiki.root.resolve()), pfad
    return echt(pfad)
with mock.patch.object(okfw, 'sha256_of', side_effect=wach):
    befunde = wiki.lint()
result = [(f.code, f.message) for f in befunde if f.path == 'wiki/sources/q1.md' and f.code.startswith('raw_')]""")
        self.assertTrue([f for f in r if f[0] == "raw_tampered" and "weicht" in f[1]], r)   # a.txt: falscher sha
        self.assertTrue([f for f in r if f[0] == "raw_tampered" and "außerhalb" in f[1]], r)
        self.assertEqual(ziel.read_text(encoding="utf-8"), "AUSSEN\n")

    def test_eintrag_path_ausserhalb_ohne_mock(self):
        ziel = self.draussen("geheim.txt", "AUSSEN\n")
        manifest = self.paket()
        manifest.write_text(json.dumps({"files": [
            {"path": "original/a.txt", "sha256": "0" * 64},
            {"path": "../../../draussen/geheim.txt", "sha256": "0" * 64}]}), encoding="utf-8")
        self.assertEqual(len(self.befund("raw_tampered")), 2)
        self.assertEqual(ziel.read_text(encoding="utf-8"), "AUSSEN\n")

    # ---------------------------------------------------------------- fulltext.path außerhalb

    def test_fulltext_ausserhalb_wird_nicht_gelesen(self):
        ziel = self.draussen("geheim.txt", "AUSSEN\n")
        self.karte("q1", "fulltext:\n  path: ../draussen/geheim.txt\n  sha256: " + "a" * 64 + "\n"
                         "resource: https://example.org/q\n"
                         "license: {name: L, url: https://example.org/l}\n")
        r = self.py("""
from unittest import mock
with mock.patch.object(okfw, 'sha256_of', side_effect=AssertionError('gehasht')):
    befunde = wiki.lint()
result = [(f.code, f.message) for f in befunde if f.path == 'wiki/sources/q1.md']""")
        self.assertTrue([f for f in r if f[0] == "fulltext_absent" and "außerhalb" in f[1]], r)
        self.assertFalse([f for f in r if f[0] == "fulltext_tampered"], r)
        self.assertEqual(ziel.read_text(encoding="utf-8"), "AUSSEN\n")

    def test_fulltext_ausserhalb_ohne_mock(self):
        ziel = self.draussen("geheim.txt", "AUSSEN\n")
        self.karte("q1", "fulltext:\n  path: ../draussen/geheim.txt\n  sha256: " + "a" * 64 + "\n"
                         "resource: https://example.org/q\n"
                         "license: {name: L, url: https://example.org/l}\n")
        self.assertTrue(self.befund("fulltext_absent", "außerhalb"))
        self.assertEqual(self.befund("fulltext_tampered"), [])
        self.assertEqual(ziel.read_text(encoding="utf-8"), "AUSSEN\n")

    # ---------------------------------------------------------------- retrieved_at aus dem Manifest

    def test_accessed_nur_aus_gueltigem_manifest(self):
        manifest = self.paket()
        self.karte("q1", "raw_packet: raw/q1\nresource: https://example.org/q\n")   # ohne csl-Block
        self.ordentlich(manifest)
        r = self.py("result = wiki.csl_item(next(p for p in wiki.pages() if p.id == 'sources/q1'))['accessed']")
        self.assertEqual(r, {"date-parts": [[2026, 1, 2]]})
        manifest.write_text(json.dumps({"files": [], "retrieved_at": 5}), encoding="utf-8")
        r = self.py("result = wiki.csl_item(next(p for p in wiki.pages() if p.id == 'sources/q1')).get('accessed')")
        self.assertIsNone(r)

    def test_retrieved_at_als_zahl_vertraegt_csl_date(self):
        self.assertEqual(self.py("result = okfw.csl_date(5)"), None)


class AusfallDurchInhalt(Grundlage):
    def test_platzhalter_ohne_beleg_bleibt_text(self):
        self.seite("k1", "Text ⟦c99⟧ und ⟦c0⟧\n\n> [!todo] Offen ⟦c7⟧\n\n**zu prüfen** ⟦c8⟧\n")
        r = self.py("n = wiki.reader_html()['concepts/k1']\nresult = [n['html'], [o['text'] for o in n['open']]]")
        self.assertIn("⟦c99⟧", r[0])
        self.assertIn("⟦c0⟧", r[0])
        self.assertIn("Offen ⟦c7⟧", r[1])
        self.assertIn(MARKE, self.bau())

    def test_tiefes_yaml_ist_ein_lint_fehler(self):
        tief = "x: " + "[" * 20000 + "]" * 20000 + "\n"
        pfad = self.root / "wiki" / "concepts" / "k1.md"
        pfad.parent.mkdir(exist_ok=True)
        pfad.write_text(f"---\n{tief}---\nText\n", encoding="utf-8")
        befunde = json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
        self.assertTrue([f for f in befunde if f["code"] == "frontmatter_invalid" and "tief" in f["message"]], befunde)
        self.assertIn(MARKE, self.bau())
        r = self.py(f"""
try:
    okfw.load_yaml({tief!r})
    result = 'gelesen'
except okfw.yaml.YAMLError as exc:
    result = str(exc)""")
        self.assertIn("tief", r)

    def test_set_mit_tiefem_yaml_ist_ein_bedienfehler(self):
        done = self.core("new", "concept", "k1", "--title", "T", "--set", "x=" + "[" * 5000, ok=False)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_ereigniszeile_zu_tief(self):
        self.seite("k1", "Text\n")
        (self.root / "wiki" / ".events.jsonl").write_text('[' * 200000 + "\n", encoding="utf-8")
        self.assertEqual(self.py("result = wiki.read_events()"), [])

    def test_nicht_utf8_ist_ein_lint_fehler(self):
        pfad = self.root / "wiki" / "concepts" / "k1.md"
        pfad.parent.mkdir(exist_ok=True)
        pfad.write_bytes(b"---\ntitle: \xff\n---\nText\n")
        self.assertIn("frontmatter_invalid", self.lint_codes("concepts/k1"))
        self.assertIn(MARKE, self.bau())

    def test_kaputte_projektionen_brechen_den_lint_nicht_ab(self):
        for name in ("index.md", "log.md"):
            ziel = self.root / "wiki" / name
            ziel.unlink(missing_ok=True)
            ziel.symlink_to(self.base / "fehlt.md")
        self.assertIn("projection_stale", self.lint_codes())
        for name in ("index.md", "log.md"):
            ziel = self.root / "wiki" / name
            ziel.unlink()
            ziel.write_bytes(b"\xff\xfe")
        self.assertIn("projection_stale", self.lint_codes())
        self.core("rebuild")

    def test_tiefe_verschachtelung_im_text(self):
        self.seite("k1", "> " * 3000 + "tief\n\n" + "".join("    " * i + "- x\n" for i in range(400)))
        self.assertIn(MARKE, self.bau())


class TypordnerAbbildungen(Grundlage):
    def typ(self) -> None:
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[types.figure]\nfolder = "abbildungen"\nlabel = "Abbildungen"\n')

    def test_seiten_bleiben_wenn_ein_typ_den_ordner_nutzt(self):
        self.typ()
        self.core("new", "figure", "f1", "--title", "Bild eins", "--description", "D")
        self.assertIn("abbildungen/f1", self.py("result = [p.id for p in wiki.pages()]"))
        self.core("rebuild")
        self.assertIn("Abbildungen", (self.root / "wiki" / "index.md").read_text(encoding="utf-8"))
        self.assertIn("Bild eins", (self.root / "wiki" / "abbildungen" / "index.md").read_text(encoding="utf-8"))
        self.assertIn("abbildungen/f1", {n["id"] for n in daten(self.bau())["nodes"]})

    def test_svg_daneben_bleibt_abbildung(self):
        self.typ()
        self.core("abbildung", "ablauf", "--file", str(self.datei("a.svg", GUT)))
        self.seite("k1", "![A](/abbildungen/ablauf.svg)\n")
        self.assertIn("MARKE", self.html("k1"))

    def test_ohne_typ_bleibt_der_ordner_ausgenommen(self):
        ordner = self.root / "wiki" / "abbildungen"
        ordner.mkdir()
        (ordner / "notiz.md").write_text(SEITE + "x\n", encoding="utf-8")
        self.assertEqual(self.py("result = [p.id for p in wiki.pages()]"), [])


# =====================================================================================================
# Regressionstests zu den Angriffen aus plugins/okfw/.nachbau/angriffe/ (okfw 0.6.0).
# ServeAngriffe: echter Server über okfw_serve.bauen auf 127.0.0.1 mit freiem Port (Stil von test_serve.ServeTest).
# RollenUndApi: Funktionen aus rollen.py und recherche/scripts/api.py, direkt importiert (Stil von test_rollen).
# SvgCssAngriffe: clean_svg und css_problem über Grundlage.py (Stil von SvgBereinigung und CssPruefung).
# Nur 127.0.0.1, Wegwerf-Ordner unter $TMPDIR, keine neuen Abhängigkeiten. Die alten Skripte rufen den Pfad
# /?t=… auf; heute ist die Start-URL http://127.0.0.1:<port>/<präfix>/?t=<token> (siehe test_serve.Praefix).
# =====================================================================================================

_IMPORT_FEHLER = ""
try:
    sys.path.insert(0, str(SKILL))
    try:
        import okfw as _okfw
        import okfw_serve as _serve
    finally:
        sys.path.remove(str(SKILL))
except (ImportError, unittest.SkipTest) as _exc:   # PyYAML/markdown fehlen: Serve-Tests überspringen, Rest läuft
    _okfw = _serve = None
    _IMPORT_FEHLER = str(_exc)


@unittest.skipIf(_serve is None, f"okfw lässt sich nicht importieren: {_IMPORT_FEHLER}")
class ServeAngriffe(unittest.TestCase):
    """Die Serve-Fälle aus p1.py, p2.py und p5.py."""

    TOKEN = "test-token-123"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="okfw-angriff-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "projekt"
        self.root.mkdir()
        self.einstellungen = self.base / "einstellungen.toml"
        self.base.joinpath("draussen").mkdir()
        patcher = mock.patch.dict(os.environ, {"OKFW_EINSTELLUNGEN": str(self.einstellungen),
                                               "OKFW_ROLLEN_PROTOKOLL": str(self.base / "rollen.jsonl")})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("WIKI_ACTOR", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        for name, wert in (("schluesselbund_da", False), ("schluesselbund_holen", "")):   # nie der echte Schlüsselbund
            patcher = mock.patch.object(_serve.rollen, name, return_value=wert)
            patcher.start()
            self.addCleanup(patcher.stop)
        _serve.rollen.setze_person("tester")
        _serve.rollen.setze_rolle("bearbeiten", "direkt", "openai", "test-modell")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for args in (("init",), ("new", "concept", "kern", "--title", "Kern", "--description", "Der Kern."),
                         ("new", "source", "q1", "--title", "Quelle 1", "--description", "Testquelle",
                          "--access", "citation", "--resource", "https://example.org/q1")):
                self.assertEqual(_okfw.main(["--root", str(self.root), *args]), 0, args)
        self.server = _serve.bauen(_okfw, self.root / "wiki.toml", 0, self.TOKEN)
        self.port = self.server.server_address[1]
        self.praefix = self.server.sitzung.praefix
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 5)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        conn.request("GET", f"/{self.praefix}/?t={self.TOKEN}")
        antwort = conn.getresponse()
        antwort.read()
        self.assertEqual(antwort.status, 302, antwort.getheader("Location"))
        self.cookie = antwort.getheader("Set-Cookie").split(";")[0]
        conn.close()

    # ---------------------------------------------------------------- Hilfen wie in test_serve

    def anfrage(self, methode, pfad, roh=None, *, host="auto", origin="auto", kopf=None, cookie=True):
        """Anfrage hinter dem Präfix dieses Serverstarts, mit Anmelde-Cookie. host="auto": der passende Port;
        host=None sendet keinen Host-Kopf; host als Liste sendet jeden Eintrag als eigenen Kopf (immer skip_host=True,
        sonst setzt putrequest selbst einen Host-Kopf, der eigene käme als zweiter und würde nicht gelesen)."""
        pfad = f"/{self.praefix}{pfad}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        k = {"Cookie": self.cookie} if cookie and self.cookie else {}
        if methode == "POST":
            k["Content-Type"] = "application/json"
            k["X-OKFW"] = "1"
            if origin == "auto":
                k["Origin"] = f"http://127.0.0.1:{self.port}"
            elif origin:
                k["Origin"] = origin
        elif origin not in ("auto", None):
            k["Origin"] = origin
        k.update(kopf or {})
        if host == "auto":
            conn.request(methode, pfad, body=roh, headers=k)
        else:
            koepfe = host if isinstance(host, list) else ([host] if host is not None else [])
            conn.putrequest(methode, pfad, skip_host=True)
            for wert in koepfe:
                conn.putheader("Host", wert)
            for name, wert in k.items():
                conn.putheader(name, wert)
            if roh is not None:
                conn.putheader("Content-Length", str(len(roh.encode())))
            conn.endheaders()
            if roh is not None:
                conn.send(roh.encode())
        antwort = conn.getresponse()
        text = antwort.read().decode("utf-8", "replace")
        conn.close()
        return antwort.status, text

    def post(self, pfad, körper, **kw):
        return self.anfrage("POST", pfad, json.dumps(körper), **kw)

    def json_(self, text):
        try:
            return json.loads(text)
        except ValueError:
            return {"roh": text}

    def seite(self, pid):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return next(p for p in _okfw.Wiki.load(config=self.root / "wiki.toml").pages() if p.id == pid)

    def datei(self, pid):
        return self.root / "wiki" / f"{pid}.md"

    # ---------------------------------------------------------------- 1) Host-Kopf

    def test_host_kopf(self):
        """p1.py: Der Host-Kopf muss 127.0.0.1 oder localhost mit genau diesem Port sein. Ohne Port, 0.0.0.0,
        [::1], LOCALHOST, fehlend, fremde Hosts und zwei Host-Köpfe in beiden Reihenfolgen sind DNS-Rebinding
        und werden abgelehnt (403)."""
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.anfrage("GET", "/", host=host)[0], 200)
        boese = {"ohne Port": "127.0.0.1", "0.0.0.0": f"0.0.0.0:{self.port}", "[::1]": f"[::1]:{self.port}",
                 "LOCALHOST": f"LOCALHOST:{self.port}", "evil.example": f"evil.example:{self.port}",
                 "evil ohne Port": "evil.example", "anderer Port": "127.0.0.1:1"}
        for name, host in boese.items():
            with self.subTest(name):
                self.assertEqual(self.anfrage("GET", "/", host=host)[0], 403)
        with self.subTest("Host-Kopf fehlt"):
            self.assertEqual(self.anfrage("GET", "/", host=None)[0], 403)
        for name, koepfe in {"zwei Host-Köpfe (richtig, evil)": [f"127.0.0.1:{self.port}", f"evil.example:{self.port}"],
                             "zwei Host-Köpfe (evil, richtig)": [f"evil.example:{self.port}", f"127.0.0.1:{self.port}"]}.items():
            with self.subTest(name):
                self.assertEqual(self.anfrage("GET", "/", host=koepfe)[0], 403)
        with self.subTest("POST mit falschem Host ändert nichts"):
            self.assertEqual(self.post("/api/einstellungen/person", {"kennung": "boese"},
                                       host=f"evil.example:{self.port}")[0], 403)
            self.assertEqual(_serve.rollen.person(), "tester")

    # ---------------------------------------------------------------- 2) Origin

    def test_post_origin(self):
        """p1.py: POST mit Origin null und mit Origin samt Pfad wird abgelehnt; Origin muss genau der Server sein."""
        boese = {"Origin null": "null", "Origin mit Pfad": f"http://127.0.0.1:{self.port}/",
                 "fremder Origin": "http://evil.example", "anderer Port": "http://127.0.0.1:1",
                 "https": f"https://127.0.0.1:{self.port}", "ohne Origin": None}
        for name, origin in boese.items():
            with self.subTest(name):
                self.assertEqual(self.post("/api/einstellungen/person", {"kennung": "boese"}, origin=origin)[0], 403)
        self.assertEqual(_serve.rollen.person(), "tester")

    # ---------------------------------------------------------------- 3) OPTIONS/HEAD

    def test_options_und_head(self):
        """p1.py: OPTIONS und HEAD gibt es nicht (501), auch nicht mit dem Anmelde-Cookie."""
        for methode, kopf in (("OPTIONS", {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}),
                              ("HEAD", None)):
            with self.subTest(methode):
                self.assertEqual(self.anfrage(methode, "/api/design", kopf=kopf)[0], 501)

    # ---------------------------------------------------------------- 4) Design-Größe

    def test_design_ueber_groessenlimit(self):
        """p1.py: Design über MAX_CSS (256 KB) und mit </style> wird abgelehnt; design.css bleibt weg."""
        for name, css in {"</style>": "a{}</style>", "zu groß": "a{} " * 70000}.items():
            with self.subTest(name):
                status, antwort = self.post("/api/design", {"css": css})
                self.assertEqual(status, 422, antwort)
        self.assertFalse((self.root / "design.css").exists())

    # ---------------------------------------------------------------- 5) Frontmatter

    def test_frontmatter_ausbrueche(self):
        """p1.py/p2.py: Merge-Schlüssel <<:, doppelte Schlüssel (auch in Anführungszeichen, auch als Fragezeichen-
        Schlüssel), Anker/Alias &a/*a, Zeile ---, ---\\r, Dokument-Ende ..., !!python-Tag: alles 422, die Karte
        bleibt unverändert (Anker und Aliase, doppelte Schlüssel, :python-Tag und --- verweigert der Kern)."""
        pfad = self.datei("concepts/kern")
        status, text = self.anfrage("GET", "/api/seite?id=concepts/kern")
        self.assertEqual(status, 200)
        d = self.json_(text)
        fm, hash_ = d["frontmatter"], d["hash"]
        self.assertIn("status: draft\n", fm)
        boese = {"Merge-Schlüssel <<:": fm + "<<: {status: stable}\n",
                 "Doppelter Schlüssel": fm + "status: draft\n",
                 "Doppelter Schlüssel in Anführungszeichen": fm + '"status": draft\n',
                 "Fragezeichen-Schlüssel": fm.replace("status: draft\n", "? status\n: draft\n"),
                 "Anker/Alias": fm + "x: &a 1\ny: *a\n",
                 "Zeile ---": fm + "---\nstatus: stable\n", "--- mit Wagenrücklauf": fm + "---\rstatus: stable\n",
                 "Dokument-Ende ...": fm + "...\nstatus: stable\n",
                 "python-Tag": fm + "x: !!python/object/apply:os.system ['id']\n"}
        for name, neu in boese.items():
            with self.subTest(name):
                vorher = pfad.read_bytes()
                status, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": hash_, "frontmatter": neu})
                self.assertEqual(status, 422, (name, antwort))
                self.assertEqual(pfad.read_bytes(), vorher)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")

    def test_frontmatter_status_in_anfuehrungszeichen(self):
        """p2.py: Ein Frontmatter mit "status": draft wird angenommen; die Karte bleibt draft, und der Kern findet
        den Schlüssel auch in Anführungszeichen, wenn er generated nachzieht."""
        pfad = self.datei("concepts/kern")
        status, text = self.anfrage("GET", "/api/seite?id=concepts/kern")
        self.assertEqual(status, 200)
        d = self.json_(text)
        fm, hash_ = d["frontmatter"], d["hash"]
        self.assertIn("status: draft\n", fm)
        neu = fm.replace("status: draft\n", '"status": draft\n')
        status, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": hash_, "frontmatter": neu})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")
        raw = self.seite("concepts/kern").raw_fm
        self.assertIn('"status": draft\n', raw)
        # der Kern kann die geschützte Zeile weiter nachziehen (Probe wie in frontmatter_pruefen)
        probe = raw
        for k in _serve.GESCHUETZT:
            probe = _okfw.set_frontmatter_key(probe, k, "okfw-probe")
        self.assertEqual(_okfw.load_yaml(probe).get("status"), "okfw-probe")

    # ---------------------------------------------------------------- 6) Schreiben über Symlink-Karte

    def test_schreiben_auf_symlink_karte(self):
        """p1.py/p2.py: Eine Karte, deren Datei ein Symlink aus dem Bundle ist, ist keine Seite (404); Schreiben über
        sie erreicht das Ziel außerhalb nicht."""
        ziel = self.base / "draussen" / "geheim.md"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(_okfw.main(["--root", str(self.root), "new", "concept", "echt", "--title", "E",
                                          "--description", "D"]), 0)
        inhalt = self.datei("concepts/echt").read_text(encoding="utf-8")
        ziel.write_text(inhalt, encoding="utf-8")
        self.datei("concepts/link").symlink_to(ziel)
        status, text = self.anfrage("GET", "/api/seite?id=concepts/link")
        self.assertEqual(status, 404, text)
        hash_ = "0" * 64   # auch ein passender Hash darf nichts durchlassen: als Seite existiert die Karte nicht
        for name, körper in (("body", {"id": "concepts/link", "hash": hash_, "body": "UEBERSCHRIEBEN"}),
                             ("frontmatter", {"id": "concepts/link", "hash": hash_,
                                             "frontmatter": inhalt.split("---\n")[1]})):
            with self.subTest(name):
                vorher = ziel.read_text(encoding="utf-8")
                self.assertEqual(self.post("/api/seite", körper)[0], 404)
                self.assertEqual(ziel.read_text(encoding="utf-8"), vorher)
        self.assertTrue(self.datei("concepts/link").is_symlink())

    def test_schreiben_auf_symlink_karte_im_bundle(self):
        """Eine Karte, die ein Symlink auf eine andere Karte im Bundle ist, ist eine Seite; Speichern über sie würde
        die andere ändern, während das Ereignis die falsche nennt. POST mit passendem Hash ergibt 422, kern.md bleibt
        byteweise gleich."""
        kern = self.datei("concepts/kern")
        alias = self.datei("concepts/alias")
        alias.symlink_to(kern)
        status, text = self.anfrage("GET", "/api/seite?id=concepts/alias")
        self.assertEqual(status, 200, text)
        hash_ = self.json_(text)["hash"]
        vorher = kern.read_bytes()
        for name, körper in (("body", {"id": "concepts/alias", "hash": hash_, "body": "UEBERSCHRIEBEN\n"}),
                             ("frontmatter", {"id": "concepts/alias", "hash": hash_,
                                              "frontmatter": self.json_(text)["frontmatter"] + "order: 2\n"})):
            with self.subTest(name):
                status, antwort = self.post("/api/seite", körper)
                self.assertEqual(status, 422, (name, antwort))
                self.assertIn("symbolischer Link", self.json_(antwort).get("fehler", antwort))
                self.assertEqual(kern.read_bytes(), vorher)
        self.assertTrue(alias.is_symlink())

    # ---------------------------------------------------------------- 6b) Textwechsel ohne Rücksetzung

    def test_textwechsel_ohne_statusruecksetzung_schreibt_nichts(self):
        """p2.py: Steht status als Fragezeichen-Schlüssel im Frontmatter, parst die Karte als stable, aber die Zeile
        ist nicht auffindbar. replace_body schreibt dann den neuen Text nicht, ohne den Status zurückzusetzen."""
        pfad = self.datei("concepts/kern")
        pfad.write_text(pfad.read_text(encoding="utf-8").replace("status: draft\n", "? status\n: stable\n"),
                        encoding="utf-8")
        vorher = pfad.read_bytes()
        self.assertEqual(self.seite("concepts/kern").fm["status"], "stable")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(_okfw.WikiError):
                _okfw.Wiki.load(config=self.root / "wiki.toml").replace_body("concepts/kern", "NEU")
        self.assertEqual(pfad.read_bytes(), vorher)

    # ---------------------------------------------------------------- 7) Anbieter-Name

    def test_anbieter_name(self):
        """p2.py: TOML-Ausbruch, Steuerzeichen (U+007F, NUL, U+2028) und ein Umbruch im Namen eines Anbieters werden
        abgelehnt (422), die Nutzerdatei bleibt unverändert oder ohne den Eintrag. toml_wert schreibt jeden Wert als
        eine Zeile, tomllib liest ihn zurück. Anführungszeichen im Namen sind erlaubt und kommen unverändert."""
        url = "https://a.example/v1"
        ausbruch = 'x"\nvorbefehl = ["okfwprobe"]\n[anbieter.y]\nz = "'
        faelle = {"TOML-Ausbruch": {"id": "f4", "openai": url, "name": ausbruch},
                  "U+007F": {"id": "f7f", "openai": url, "name": "N\u007fme"},
                  "NUL": {"id": "fnul", "openai": url, "name": "N\u0000me"},
                  "U+2028": {"id": "f2028", "openai": url, "name": "N\u2028me"},
                  "Umbruch": {"id": "fnl", "openai": url, "name": "N\nme"}}
        for name, eintrag in faelle.items():
            with self.subTest(name):
                vorher = self.einstellungen.read_text(encoding="utf-8") if self.einstellungen.is_file() else None
                status, antwort = self.post("/api/einstellungen/anbieter", eintrag)
                self.assertEqual(status, 422, (name, antwort))
                eintrag_gelesen = _serve.rollen.toml_lesen(self.einstellungen).get("anbieter", {}).get(eintrag["id"])
                self.assertFalse(eintrag_gelesen, (name, eintrag_gelesen))
                if vorher is not None:
                    self.assertEqual(self.einstellungen.read_text(encoding="utf-8"), vorher)
                for zeile in (self.einstellungen.read_text(encoding="utf-8").splitlines() if self.einstellungen.is_file() else []):
                    self.assertFalse(zeile.startswith("vorbefehl"), (name, zeile))
                    self.assertFalse(zeile.startswith("[anbieter."), (name, zeile))
        # das Schreiben maskiert jeden Wert zu einer Zeile, und tomllib liest genau ihn zurück
        for name, wert in (("Ausbruch", ausbruch), ("U+007F", "N\u007fme"), ("NUL", "N\u0000me"),
                           ("U+2028", "N\u2028me"), ("Umbruch", "N\nme"), ("Anführungszeichen", 'Mein "Anbieter"')):
            with self.subTest(f"toml_wert: {name}"):
                zeile = _serve.rollen.toml_wert(wert)
                self.assertNotIn("\n", zeile)
                self.assertEqual(tomllib.loads("name = " + zeile), {"name": wert})
        # ein Name mit Anführungszeichen wird angenommen, kommt unverändert zurück und schleust nichts ein
        status, antwort = self.post("/api/einstellungen/anbieter", {"id": "f5", "openai": url, "name": 'Mein "Anbieter"'})
        self.assertEqual(status, 200, antwort)
        gelesen = _serve.rollen.toml_lesen(self.einstellungen)
        self.assertEqual(gelesen["anbieter"]["f5"]["name"], 'Mein "Anbieter"')
        self.assertEqual(gelesen["anbieter"]["f5"]["openai"], url)
        text = self.einstellungen.read_text(encoding="utf-8")
        for zeile in text.splitlines():
            self.assertFalse(zeile.startswith("vorbefehl"), zeile)
        tabellen = [zeile.strip() for zeile in text.splitlines() if zeile.strip().startswith("[")]
        self.assertIn("[anbieter.f5]", tabellen)
        self.assertNotIn("[anbieter.y]", tabellen)
        self.assertEqual(_serve.rollen.lade(self.root / "wiki.toml")["anbieter"]["openai"]["openai"],
                         "https://api.openai.com/v1")

    # ---------------------------------------------------------------- 8) Rechte der Quelle

    def test_vorschlag_quelle_ohne_ki_recht(self):
        """p5.py: Vorschlag auf einer Quelle mit rights.ki_auswertung: nein wird verweigert (422 mit Rechte-Hinweis),
        kein Modell wird aufgerufen und die Karte bleibt unverändert."""
        pfad = self.datei("sources/q1")
        pfad.write_text('---\ntitle: Quelle 1\ndescription: Testquelle\nstatus: draft\naccess: citation\n'
                        'resource: https://example.org/q1\ngenerated: {by: \'human:t\', at: \'2026-01-01T00:00:00Z\'}\n'
                        'rights: {ki_auswertung: \'nein: Vertrag\'}\n---\nText\n', encoding="utf-8")
        vorher = pfad.read_bytes()
        aufgerufen = []
        with mock.patch.object(_serve.okfw_auftraege, "rollen_aufruf", lambda *a: aufgerufen.append(a) or (0, "{}", "")):
            status, antwort = self.post("/api/auftrag", {"typ": "bearbeiten", "id": "sources/q1", "anweisung": "Fasse zusammen."})
        self.assertEqual(status, 422, antwort)
        fehler = self.json_(antwort).get("fehler", antwort)
        self.assertIn("KI-Auswertung", fehler)
        self.assertIn("Vertrag", fehler)
        self.assertEqual(aufgerufen, [])
        self.assertEqual(pfad.read_bytes(), vorher)

    def test_vorschlag_mit_recht_laeuft(self):
        """p5.py (Kontrolle): Ohne rights-Eintrag läuft der Vorschlag normal."""
        aufgerufen = []
        with mock.patch.object(_serve.okfw_auftraege, "rollen_aufruf", lambda *a: aufgerufen.append(a) or (0, json.dumps({"text": "# Neu\n"}), "")):
            status, antwort = self.post("/api/auftrag", {"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "Kürze."})
            self.assertEqual(status, 202, antwort)
            kennung = self.json_(antwort)["id"]
            for _ in range(500):
                status, antwort = self.anfrage("GET", f"/api/auftrag?id={kennung}")
                if self.json_(antwort)["zustand"] == "fertig":
                    break
                time.sleep(0.02)
        self.assertEqual(self.json_(antwort)["zustand"], "fertig", antwort)
        self.assertEqual(len(aufgerufen), 1)


class RollenUndApi(unittest.TestCase):
    """rollen.url_pruefen (d.py) und api.py pruefe_host (api.py im Angriffsordner): reine Funktionen, kein Netz."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("okfw_rollen_angriff", SKILL / "rollen.py")
        cls.rollen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rollen)
        spec = importlib.util.spec_from_file_location("okfw_api_angriff", SKILL.parent / "recherche" / "scripts" / "api.py")
        cls.api = importlib.util.module_from_spec(spec)
        sys.modules["okfw_api_angriff"] = cls.api
        spec.loader.exec_module(cls.api)

    # ---------------------------------------------------------------- 9) rollen.url_pruefen

    def test_url_pruefen(self):
        """d.py: userinfo, Fragment-Trick, Backslash, 0.0.0.0, 127.1, Tab im Host werden abgelehnt; lokale Hosts und
        HTTPS bleiben erlaubt."""
        boese = ["http://127.0.0.1@evil.example/", "http://evil.example#@127.0.0.1/", "http://127.0.0.1:80\\@evil.example/",
                 "http://0.0.0.0/", "http://127.1/", "http://localhost\t.evil.example/", "http://localhost.evil.example/"]
        gut = ["https://a.example/v1", "http://localhost:11434/v1", "http://127.0.0.1/", "http://[::1]/"]
        for url in boese:
            with self.subTest(url):
                with self.assertRaises(self.rollen.Fehler):
                    self.rollen.url_pruefen(url)
        for url in gut:
            with self.subTest(url):
                self.assertEqual(self.rollen.url_pruefen(url), url.rstrip("/"))

    # ---------------------------------------------------------------- 10) api.py pruefe_host

    def test_pruefe_host(self):
        """api.py im Angriffsordner: .evil-Suffix, Benutzerteil, Backslash, http und Port 444 werden abgelehnt; erlaubt
        bleiben nur die Hosts aus HOSTS auf Port 443. Hostnamen sind groß-klein-unabhängig, der Prüfer normiert sie."""
        boese = ["https://eutils.ncbi.nlm.nih.gov.evil.example/", "https://eutils.ncbi.nlm.nih.gov@evil.example/",
                 "https://eutils.ncbi.nlm.nih.gov\\@evil.example/", "https://eutils.ncbi.nlm.nih.gov:444/x",
                 "http://eutils.ncbi.nlm.nih.gov/"]
        for url in boese:
            with self.subTest(url):
                with self.assertRaises(self.api.Abgelehnt):
                    self.api.pruefe_host(url)
        gut = {"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?x=1": "eutils.ncbi.nlm.nih.gov",
               "https://EUTILS.ncbi.nlm.nih.gov/x": "eutils.ncbi.nlm.nih.gov",
               "https://api.crossref.org/works/10.1/x": "api.crossref.org"}
        for url, host in gut.items():
            with self.subTest(url):
                self.assertEqual(self.api.pruefe_host(url), host)


@unittest.skipIf(_okfw is None, f"okfw lässt sich nicht importieren: {_IMPORT_FEHLER}")
class KernLaden(unittest.TestCase):
    """load_yaml, set_frontmatter_key, css_problem und die Druckbar-Schicht (okfw.py): reine Funktionen."""

    def test_zeitstempel_bleiben_text(self):
        """OKF-Werte wie 2026-06-30T14:00:00Z: Als Datum käme `verified` nach dem nächsten verify als !!timestamp zurück."""
        d = _okfw.load_yaml("timestamp: 2026-06-30T14:00:00Z\nverified:\n- {by: human:a, at: 2026-06-30T14:00:00Z}\n")
        self.assertEqual((d["timestamp"], d["verified"][0]["at"]), ("2026-06-30T14:00:00Z", "2026-06-30T14:00:00Z"))
        self.assertNotIn("!!timestamp", _okfw.dump_key("verified", d["verified"]))

    def test_load_yaml_lehnt_merge_und_doppelte_schluessel_ab(self):
        """okfw.py: Merge-Schlüssel und doppelte Schlüssel werden abgelehnt, auch in Anführungszeichen und verschachtelt;
        verschiedene Schlüssel bleiben erlaubt."""
        boese = ["<<: {a: 1}", "a: 1\na: 2", 'a: 1\n"a": 2', "x: {a: 1, a: 2}"]
        for yaml_text in boese:
            with self.subTest(yaml_text.replace("\n", "\\n")):
                with self.assertRaises(_okfw.yaml.YAMLError):
                    _okfw.load_yaml(yaml_text)
        self.assertEqual(_okfw.load_yaml("a: 1\nb: 2"), {"a": 1, "b": 2})

    def test_set_frontmatter_key_in_anfuehrungszeichen(self):
        """okfw.py: set_frontmatter_key findet einen Top-Level-Schlüssel auch in Anführungszeichen und der Ersatz
        bleibt lesbares Frontmatter."""
        text = _okfw.set_frontmatter_key('"status": draft\n', "status", "stable")
        self.assertEqual(_okfw.load_yaml(text), {"status": "stable"})

    def test_druckbar_ersetzt_steuerzeichen(self):
        """okfw.py: _Druckbar ersetzt Steuerzeichen (außer Tab und LF) in jeder Ausgabe; andere Attribute gehen durch."""
        strom = io.StringIO()
        raus = _okfw._Druckbar(strom)
        raus.write("a\tb\nc\rd\ne\x1b[2Jf\x1b]8;;http://evil.example\x1b\\g\x07h\x7fi\x9fj\U00100000k\n")
        text = strom.getvalue()
        self.assertEqual(text, "a\tb\nc" + chr(0xFFFD) + "d\ne" + chr(0xFFFD) + "[2Jf" + chr(0xFFFD) + "]8;;http://evil.example"
                         + chr(0xFFFD) + "\\g" + chr(0xFFFD) + "h" + chr(0xFFFD) + "i" + chr(0xFFFD) + "j"
                         + "\U00100000k\n")   # jedes Steuerzeichen wird einzeln ersetzt
        self.assertIsNone(raus.flush())   # StringIO.flush ergibt None


class TerminalAusgabe(Grundlage):
    """Titel und Lizenznamen aus einem fremden Wiki gehen gefiltert über print ins Terminal (Kern als Prozess)."""

    def test_rechte_ohne_esc_sequenzen(self):
        """okfw.py als Unterprozess: Ein Lizenzname mit ESC-Sequenz (Zeile löschen) und OSC-8-Link erscheint ohne
        \x1b, der Text bleibt."""
        ziel = self.root / "wiki" / "sources" / "q1.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text("---\ntype: source\ntitle: T\ndescription: D\nstatus: draft\n"
                        "access: citation\nresource: https://example.org/q\n"
                        "license: {name: \"\\u001b[2KL\\u001b]8;;https://evil.example\\u001b\\\\IZZ\", url: \"\"}\n"
                        "generated: {by: 'human:t', at: '2026-01-01T00:00:00Z'}\n---\nText\n", encoding="utf-8")
        done = self.core("rechte", "sources/q1", ok=None)
        self.assertEqual(done.returncode, 0, done.stderr)
        for teil in (done.stdout, done.stderr):
            self.assertNotIn("\x1b", teil)
        self.assertIn("IZZ", done.stdout)


class TerminalFehler(Grundlage):
    """Auch stderr (Bedienfehler) bleibt ohne Steuerzeichen; _Druckbar sitzt vor sys.exit."""

    def test_fehlermeldung_ohne_steuerzeichen(self):
        """Ein Seitenname mit OSC-8-Link landet ungesichert in der Meldung „Seite fehlt“; ohne Filter ginge er als
        klickbarer Link ins Terminal. Der Seitenname kommt in stderr vor, das ESC-Zeichen nicht."""
        name = "concepts/\x1b]8;;https://evil.example\x1b\\x"
        done = self.core("body", name, "-", stdin="T", ok=False)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("x", done.stderr)   # der Rest des Namens steht in der Meldung
        self.assertIn("evil.example", done.stderr)
        self.assertNotIn("\x1b", done.stderr)
        self.assertNotIn("\x1b", done.stdout)


class SvgCssAngriffe(Grundlage):
    """clean_svg (svgcss.py, geheim.svg) und css_problem (svgcss.py): über Grundlage.py im Kernprozess wie oben."""

    E = "http://127.0.0.1:18901"   # Adresse wie im Angriffsskript; nichts geht dorthin, alles wird abgelehnt

    def clean(self, svg):
        return self.py(f"""
try:
    sauber, entfernt = okfw.clean_svg({svg!r})
    result = {{'svg': sauber, 'entfernt': entfernt}}
except okfw.FigureError as exc:
    result = {{'fehler': str(exc)}}""")

    def test_svg_nutzlasten(self):
        """svgcss.py: Skript und on*-Attribute, foreignObject, externes use, externes image, @import im style-Element,
        Groß-/Kleinschreibung, Namensraum-Tricks, animate/set, xml:base, CDATA, Ausbruch über Anführungszeichen in
        href oder id: nichts davon bleibt in der bereinigten Ausgabe."""
        kopf = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                'viewBox="0 0 10 10"')
        faelle = {
            "on*-Attribute": kopf + ' onload="alert(1)"><rect onclick="alert(1)" ONCLICK="alert(2)" width="1"'
                                     ' height="1"/></svg>',
            "foreignObject": kopf + '><foreignObject><iframe xmlns="http://www.w3.org/1999/xhtml" src="x"/>'
                                   '</foreignObject></svg>',
            "externes use": kopf + f'><use href="{self.E}/use.svg#a"/><use xlink:href="{self.E}/use2.svg#a"/>'
                                  '<use href="#ok"/></svg>',
            "style-Element": kopf + f'><style>@import url({self.E}/a.css);</style></svg>',
            "Groß-/Kleinschreibung": kopf + '><SCRIPT>alert(1)</SCRIPT><foreignobject/><USE href="#x"/></svg>',
            "Namensraum-Tricks": kopf + ' xmlns:s="http://www.w3.org/2000/svg" '
                                        'xmlns:x="http://www.w3.org/1999/xhtml"><s:rect width="1"/>'
                                        '<s:script>alert(1)</s:script><x:script>alert(1)</x:script></svg>',
            "animate/set": kopf + '><rect><animate attributeName="fill" to="url(http://x)"/>'
                                  '<set attributeName="onclick" to="alert(1)"/></rect></svg>',
            "image": kopf + f'><image href="{self.E}/img.png"/></svg>',
            "xml:base": kopf + f' xml:base="{self.E}/"><use href="a.svg#a"/><use href="#x" xml:base="{self.E}"/>'
                              '</svg>',
            "CDATA": kopf + '><title><![CDATA[</title><img src=x onerror=alert(1)>]]></title>'
                            '<text><![CDATA[<script>alert(1)</script>]]></text></svg>',
            "Anführungszeichen in href/id": kopf + '><use href="#x&quot; onload=&quot;alert(1)"/>'
                                                   '<rect id="a&quot; onload=&quot;x"/></svg>',
        }
        for name, svg in faelle.items():
            with self.subTest(name):
                r = self.clean(svg)
                self.assertNotIn("fehler", r, (name, r))
                self.assertNotIn(self.E, r["svg"], (name, r["svg"]))
                for tag, attribute in tags(r["svg"]):   # Parser wie im Browser: Maskierter Text ist kein Markup
                    self.assertIn(tag, {e.lower() for e in _okfw.SVG_ELEMENTS}, (name, r["svg"]))
                    for attribut in attribute:
                        self.assertFalse(attribut.lower().startswith("on"), (name, attribut))
                        self.assertNotEqual(attribut.lower(), "xml:base", name)
                for spur in ("<![CDATA[", "<script", "<iframe", "<img", "<image", "<animate", "<set", "<s:", "<x:",
                             "@import", "javascript:"):
                    self.assertNotIn(spur, r["svg"], (name, spur, r["svg"]))
        with self.subTest("CDATA wird maskierter Text"):
            svg = self.clean(faelle["CDATA"])["svg"]
            self.assertIn("&lt;img", svg)
            self.assertIn("&lt;script", svg)
            self.assertEqual([t for t, _ in tags(svg)], ["svg", "title", "text"])
        with self.subTest("Anführungszeichen: use und id fallen weg"):
            svg = self.clean(faelle["Anführungszeichen in href/id"])["svg"]
            self.assertEqual([t for t, _ in tags(svg)], ["svg", "rect"])
            self.assertNotIn("onload", svg)
            self.assertNotIn(" id=", svg)

    def test_svg_nur_gezeichnete_elemente(self):
        """svgcss.py: In der Ausgabe stehen nur Elemente aus SVG_ELEMENTS; Skript, iframe, use mit externem href und
        image fehlen ganz. Ein use auf eine gültige id bleibt."""
        kopf = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"'
        r = self.clean(kopf + f'><script>alert(1)</script><rect width="1" height="1"/><use href="{self.E}/a"/>'
                         f'<image href="{self.E}/b"/><foreignObject/></svg>')
        self.assertNotIn("fehler", r, r)
        self.assertEqual([t for t, _ in tags(r["svg"]) if t not in ("svg",)], ["rect"])
        r = self.clean(kopf + '><rect id="ok" width="1" height="1"/><use href="#ok"/></svg>')
        self.assertEqual([(t, a.get("href")) for t, a in tags(r["svg"]) if t == "use"], [("use", "#ok")])

    def test_geheim_in_kommentar_und_metadaten(self):
        """geheim.svg: Kommentar und metadata fallen ganz weg, metadata wird als entfernt genannt. title und desc stehen
        bewusst in SVG_ELEMENTS (Barrierefreiheit): Sie bleiben, aber nur als Text, auch wenn sie Markup tragen."""
        quelle = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><!-- KOMMENTAR-GEHEIM -->'
                  '<metadata>META-GEHEIM</metadata><title>TITEL</title><desc>&lt;b&gt;BESCHREIBUNG</desc><rect/></svg>')
        r = self.clean(quelle)
        self.assertNotIn("fehler", r, r)
        self.assertNotIn("KOMMENTAR-GEHEIM", r["svg"], r["svg"])
        self.assertNotIn("META-GEHEIM", r["svg"], r["svg"])
        self.assertIn("Element metadata", r["entfernt"], r["entfernt"])
        self.assertIn("TITEL", r["svg"], r["svg"])
        self.assertIn("&lt;b&gt;BESCHREIBUNG", r["svg"], r["svg"])
        self.assertEqual([t for t, _ in tags(r["svg"])], ["svg", "title", "desc", "rect"])

    # ---------------------------------------------------------------- 12) css_problem

    def problem(self, css):
        return self.py(f"result = okfw.css_problem({css!r})")

    def test_css_import_und_url(self):
        """svgcss.py: @import als String, groß geschrieben (@IMPORT), maskiert (\\69mport) oder mit Kommentar im Wort
        (im/**/port); url(…) auf Fremdes direkt, maskiert, groß geschrieben (URL), mit Kommentar darin oder mit
        maskiertem http; @font-face mit fremder URL; src/image-set; data:text/html und data: mit eingebettetem
        SVG-image; Kommentarzeichen in Zeichenketten; url(#x) , url(fremd)."""
        boese = ["@import url(http://x/a.css);", "@import 'http://x/a.css';", "@\\69mport 'http://x/a.css';",
                 "@IMPORT 'x';", "@im/**/port 'x';", "a{background:url(http://x/a.png)}",
                 "a{background:\\75rl(http://x/a.png)}", "a{background:u\\72 l(http://x/a.png)}",
                 "a{background:URL('http://x')}", "a{background:url(\\68ttp://x/a.png)}",
                 "a{background:url(/*x*/http://x)}", "a{background:image-set('http://x' 1x)}",
                 "a{background:\\69mage-set('http://x' 1x)}", "a{background:-webkit-image-set('http://x' 1x)}",
                 "@font-face{font-family:x;src:url(https://x/f.woff)}", "a{background:src('http://x')}",
                 "a{background:url(data:text/html,<script>alert(1)</script>)}",
                 "a{background:url(#x) , url(http://x)}",
                 'a{background:url("data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\'><image href=\''
                 + self.E + '/inner.png\'/></svg>")}',
                 f'a{{content:"/*"}} b{{background:url({self.E}/css-str-comment.png)}} c{{content:"*/"}}',
                 '@charset "/*"; @import url(' + self.E + '/css-str-comment-import.css); a{content:"*/"}',
                 "a{background:url(data:IMAGE/SVG+XML,x)}", "a{background:url(d\\61ta:text/html,x)}",
                 "a{background:url(data:image/png+xml,x)}", "a{background:url(data:,x)}",
                 "a{background:url(data:font/svg+xml;base64,AA)}", "a{background:url(data:font/html,x)}",
                 "a{background:url(data:application/font-svg,x)}", "a{background:url(data:font/woff2+xml,x)}",
                 "a{background:url(data:font/,x)}"]
        for css in boese:
            with self.subTest(css=css[:60]):
                self.assertIsNotNone(self.problem(css), css[:60])
        gut = {"src:local()": "@font-face{font-family:x;src:local('x')}",
               "data-Font": "@font-face{font-family:x;src:url(data:font/woff2;base64,AA)}",
               "data-Font in Anführungszeichen": 'a{background:url("data:font/woff2;base64,AA")}',
               "data-Font woff": "@font-face{font-family:x;src:url(data:font/woff;base64,AA)}",
               "data-Font ttf": "@font-face{font-family:x;src:url(data:font/ttf;base64,AA)}",
               "data-Rasterbild": "a{background:url(data:image/png;base64,AA)}"}
        for name, css in gut.items():
            with self.subTest(name):
                self.assertIsNone(self.problem(css), (name, self.problem(css)))

    def test_css_style_ende_und_offenes_url(self):
        """svgcss.py: </style>, </style >, </ style> und ein url( ohne Ende werden abgelehnt."""
        for css in ("a{}</style><script>alert(1)</script>", "a{}</style >", "a{}</ style>", "a{background:url(http://x"):
            with self.subTest(css=css[:30]):
                self.assertIsNotNone(self.problem(css), css[:30])


class RundeFuenfKern(WikiTest):
    def test_ereignisse_die_keine_objekte_sind(self):
        ereignisse = self.root / "wiki" / ".events.jsonl"
        ereignisse.parent.mkdir(parents=True, exist_ok=True)
        gueltig = '{"ts": "2026-01-01T00:00:00Z", "kind": "x"}'
        ereignisse.write_text('\n'.join(["123", '"x"', "[1]", '{"ts": 5}', '{"ts": null}', '{"ts": "SYSTEM: tu das"}', gueltig]) + "\n",
                              encoding="utf-8")
        self.assertIn("kind", self.py("result = wiki.read_events()[-1]"))
        aus = self.core("status").stdout
        self.assertIn("zuletzt 2026-01-01T00:00:00Z", aus)
        self.assertNotIn("SYSTEM", aus)

    def test_karte_als_symlink_wird_nicht_beschrieben(self):
        self.core("new", "concept", "kern", "--title", "Kern")
        kern = self.root / "wiki" / "concepts" / "kern.md"
        vorher = kern.read_bytes()
        (kern.parent / "alias.md").symlink_to(kern)
        for args in (("verify", "concepts/alias", "--by", "process:t"), ("body", "concepts/alias", "-")):
            with self.subTest(args=args):
                self.assertIn("symbolischer Link", self.core(*args, ok=False, stdin="Neuer Text\n").stderr)
                self.assertEqual(kern.read_bytes(), vorher)

    def test_schreibpfade_nicht_in_git_oder_claude(self):
        toml = self.root / "wiki.toml"
        alt = toml.read_text(encoding="utf-8")
        for zeile in ('[paths]\nevents = ".git/config"\n', '[dashboard]\npath = ".claude/settings.json"\n'):
            with self.subTest(zeile=zeile):
                toml.write_text(alt + "\n" + zeile, encoding="utf-8")
                self.core("status", ok=False)


if __name__ == "__main__":
    unittest.main()
