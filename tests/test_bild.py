"""Rasterbilder: WebP prüfen und bereinigen (clean_webp), Wünsche in Worten → Parameter (okfw_kern/bild.py), Befehle
abbildung (WebP) und bild, Einbettung als data: im Dashboard, Lint figure_*, Rolle bild in rollen.py.

Ohne Netz: Der Befehl bild spricht einen Anbieter-Ersatz auf 127.0.0.1 an. Der Key steht in keiner Ausgabe und keinem Ereignis.
"""

import base64
import http.server
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from .hilfe import SKILL, WikiTest
from .test_serve import GEHEIM, ServeTest, okfw_serve

sys.path.insert(0, str(SKILL))
try:
    from okfw_kern import bild as b
    from okfw_kern.basis import FigureError
    from okfw_kern.bereinigung import clean_webp
finally:
    sys.path.remove(str(SKILL))
_spec = importlib.util.spec_from_file_location("okfw_rollen_bild", SKILL / "rollen.py")
rollen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rollen)

KEY = "sk-bild-GEHEIM-0815-nicht-ausgeben"


def chunk(tag: bytes, data: bytes) -> bytes:
    return tag + len(data).to_bytes(4, "little") + data + (b"\0" if len(data) & 1 else b"")


def webp(w: int = 1536, h: int = 864, *extra: bytes, lossy: bool = False) -> bytes:
    """Kleinstes WebP mit gültigem Kopf (die Pixel dekodiert okfw nicht)."""
    if lossy:
        frame = chunk(b"VP8 ", b"\0\0\0\x9d\x01\x2a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + b"\0" * 6)
    else:
        frame = chunk(b"VP8L", b"\x2f" + ((w - 1) | (h - 1) << 14).to_bytes(4, "little") + b"\0" * 5)
    body = b"".join(extra) + frame
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


class Webp(unittest.TestCase):
    def test_gueltig_bleibt_gleich_mit_mass(self):
        data = webp(1536, 864)
        self.assertEqual(clean_webp(data), (data, (1536, 864), []))
        self.assertEqual(clean_webp(webp(1024, 1024, lossy=True))[1], (1024, 1024))

    def test_metadaten_entfallen(self):
        data, size, weg = clean_webp(webp(64, 32, chunk(b"EXIF", b"Kamera GEHEIM"), chunk(b"XMP ", b"<x/>"), chunk(b"ICCP", b"p")))
        self.assertEqual((size, weg), ((64, 32), ["EXIF", "ICCP", "XMP"]))
        self.assertNotIn(b"GEHEIM", data)
        self.assertEqual(clean_webp(data)[0], data)

    def test_transparenz_zu_vp8_behaelt_alpha_ohne_metadaten(self):
        data, _, weg = clean_webp(webp(16, 16, chunk(b"ALPH", b"a"), chunk(b"EXIF", b"x"), lossy=True))
        self.assertEqual(weg, ["EXIF"])
        self.assertEqual(data[12:16], b"VP8X")
        self.assertEqual(data[20], 0x10)   # nur das Alpha-Bit
        self.assertIn(b"ALPH", data)

    def test_abgelehnt(self):
        gut = webp()
        faelle = {"png": b"\x89PNG\r\n\x1a\n" + b"\0" * 30, "abgeschnitten": gut[:-3], "anhang": gut + b"<script>",
                  "animiert": webp(8, 8, chunk(b"ANIM", b"\0" * 6)), "ohne bild": b"RIFF\x04\0\0\0WEBP",
                  "zwei bilder": webp(8, 8, chunk(b"VP8L", b"\x2f" + b"\0" * 9)), "riesig": webp(4097, 10),
                  "kopf kaputt": webp(8, 8)[:20] + b"\0" + webp(8, 8)[21:]}
        for name, data in faelle.items():
            with self.subTest(name), self.assertRaises(FigureError):
                clean_webp(data)


class Woerter(unittest.TestCase):
    def test_seed_und_stil_begrenzt(self):
        """Gefunden in der Prüfung zu 0.7.2: Seed+Varianten liefen über int32, eigene Stile hatten keine Länge."""
        p = b.planen("Ein Tisch", seed=b.SEED_MAX, varianten=b.VARIANTEN_MAX)
        self.assertLessEqual(p["einstellungen"]["seed"] + p["varianten"] - 1, 2 ** 31 - 1)
        with self.assertRaises(b.BildFehler):
            b.planen("Ein Tisch", seed=b.SEED_MAX + 1)
        with self.assertRaises(b.BildFehler):
            b.planen("Ein Tisch", stil="lang", stile={"lang": "x" * (b.STIL_MAX + 1)})
        self.assertLessEqual(b.planen("Ein Tisch")["einstellungen"]["seed"], b.SEED_MAX)

    def test_wunsch_wird_zu_einstellungen(self):
        self.assertEqual(b.wunsch("breit für eine Folie, ruhig, ohne Menschen, keine Kabel, schneller Entwurf, drei Varianten"),
                         {"format": "quer", "tempo": "entwurf", "ohne": ["people", "cables"], "varianten": 3})
        self.assertEqual(b.wunsch("ohne Menschen und Autos")["ohne"], ["people", "cars"])
        p = b.planen("Ein Tisch", wunsch_text="ohne Zitronenpresse")
        self.assertEqual(p["einstellungen"]["ohne"], ["zitronenpresse"])
        self.assertTrue(any("im Feld „Ohne“ übersetzen" in h for h in p["hinweise"]))
        self.assertEqual(b.wunsch("Hochkant fürs Plakat, final, mit Text"), {"format": "hoch", "tempo": "fein", "mit_text": True})
        self.assertEqual(b.wunsch("groß für Druck, ohne Text, in meinem Design"),
                         {"format": "gross", "mit_text": False, "stil": "design"})
        self.assertEqual(b.wunsch("als Ölgemälde, ganz anders")["stil"], "oelgemaelde")
        self.assertEqual(b.wunsch("Linolschnitt"), {"stil": "linolschnitt"})
        self.assertEqual(b.wunsch(""), {})

    def test_groesse_rundet_auf_16_und_sagt_es(self):
        self.assertEqual(b.groesse("quer"), ((1536, 864), ""))
        self.assertEqual(b.groesse("Groß")[0], (2048, 1152))
        self.assertIn("ausprobiert", b.groesse("4:3")[1])
        (maß, hinweis) = b.groesse("1000x700")
        self.assertEqual(maß, (1008, 704))
        self.assertIn("Vielfache von 16", hinweis)
        self.assertEqual(b.groesse("9000×10")[0], (2048, 256))
        with self.assertRaises(b.BildFehler):
            b.groesse("riesig")

    def test_anfrage_nur_geprueft_felder(self):
        n = b.anfrage("Eine Messingmaschine auf einem weißen Tisch", format="quer", tempo="entwurf", seed=42)["nutzlast"]
        self.assertLessEqual(set(n), set(b.FELDER))
        self.assertEqual((n["size"], n["seed"], n["num_inference_steps"], n["output_format"]), ("1536x864", 42, 8, "webp"))
        a = b.anfrage("Eine Messingmaschine auf einem weißen Tisch", format="quer", tempo="entwurf", ohne=["Menschen"], seed=42)
        n = a["nutzlast"]
        self.assertEqual((n["negative_prompt"], n["guidance_scale"]), ("Menschen", 4))
        self.assertNotIn("num_inference_steps", n)   # Guidance mit verkürzten Schritten zerstört das Bild
        self.assertTrue(any("Tempo fein statt Entwurf" in h for h in a["hinweise"]))
        self.assertTrue(n["prompt"].endswith(b.OHNE_TEXT + "."))
        self.assertNotIn("model", n)
        self.assertTrue(any("Wirkt nur mit Guidance" in e for e in a["erklaerung"]))
        fein = b.anfrage("Motiv", mit_text=True)["nutzlast"]
        self.assertNotIn("num_inference_steps", fein)
        self.assertNotIn("negative_prompt", fein)
        self.assertNotIn("no text", fein["prompt"])
        self.assertIsInstance(fein["seed"], int)

    def test_stil_vorlage_mit_motiv(self):
        stile = {"design": "Flat, alabaster (#F4F3ED). {motiv}. Only the key is yellow."}
        n = b.anfrage("Ein Schlüssel links", stil="design", stile=stile)["nutzlast"]
        self.assertTrue(n["prompt"].startswith("Flat, alabaster (#F4F3ED). Ein Schlüssel links. Only"))
        self.assertIn("linocut", b.anfrage("Ein Baum", stil="linolschnitt")["nutzlast"]["prompt"])
        with self.assertRaises(b.BildFehler):
            b.anfrage("x", stil="gibtsnicht")
        with self.assertRaises(b.BildFehler):
            b.anfrage("   ")

    def test_prompt_hinweise(self):
        lang = "Eine Schachtel aus Pappe steht links auf einem Holztisch im weichen Morgenlicht, rechts daneben ein Glas"
        self.assertEqual(b.prompt_hinweise(lang, False), [])
        self.assertTrue(any("Kurzer Prompt" in h for h in b.prompt_hinweise("Schachtel", False)))
        self.assertTrue(any("Gegenstand" in h for h in b.prompt_hinweise(lang + " in lemon-yellow", False)))
        self.assertTrue(any("drei Elemente" in h for h in b.prompt_hinweise(lang + " und fünf Felder", False)))
        self.assertTrue(any("ab drei Wörtern" in h for h in b.prompt_hinweise(lang + ' mit „Achtung KI im Einsatz“', True)))
        self.assertEqual(b.unterschrift("Ablauf", "Z Image Turbo", "Bildanbieter", "2026-11-02"),
                         "*Abb.: Ablauf. KI-generiert mit Z Image Turbo (Bildanbieter), 2026-11-02.*")
        self.assertIn("(Bildanbieter), ", b.unterschrift("A", "M", "Bildanbieter (Beta)", "2026-11-02"))


class RolleBild(unittest.TestCase):
    def cfg(self, harness="direkt", anbieter="fern"):
        return {"anbieter": {"fern": {"name": "Fern", "openai": "https://fern.example/v1"},
                             "anth": {"name": "A", "anthropic": "https://a.example"}},
                "rollen": {"bild": {"harness": harness, "anbieter": anbieter, "modell": "Bildmodell"}},
                "herkunft": {}, "sperre": [], "daten": "intern", "wiki": "x"}

    def test_nur_direkt_und_openai(self):
        self.assertEqual(rollen.loese(self.cfg(), "bild")["gesperrt"], "")
        for harness, anbieter in (("claude", "fern"), ("opencode", "fern"), ("direkt", "anth")):
            with self.subTest(harness=harness, anbieter=anbieter):
                self.assertIn("OpenAI-kompatiblen", rollen.loese(self.cfg(harness, anbieter), "bild")["gesperrt"])

    def test_bild_direkt_dekodiert_und_schickt_das_modell(self):
        ziel, cfg = rollen.loese(self.cfg(), "bild"), self.cfg()
        aufrufe = []
        antwort = {"created": 1, "data": [{"b64_json": base64.b64encode(webp()).decode(), "revised_prompt": None}],
                   "output_format": "webp"}
        with mock.patch.object(rollen, "http", lambda url, kopf, nutzlast=None, zeit=0: aufrufe.append((url, kopf, nutzlast, zeit)) or antwort):
            data, meta = rollen.bild_direkt(ziel, cfg["anbieter"]["fern"], KEY, {"prompt": "p", "model": "falsch"})
        self.assertEqual(data, webp())
        self.assertEqual(meta, {"created": 1, "output_format": "webp"})
        url, kopf, nutzlast, zeit = aufrufe[0]
        self.assertEqual((url, nutzlast["model"], zeit), ("https://fern.example/v1/images/generations", "Bildmodell", rollen.BILD_ZEITLIMIT))
        self.assertEqual(kopf, {"authorization": f"Bearer {KEY}"})
        for kaputt in ({"data": []}, {"data": [{"b64_json": "%%%"}]}, {"data": "x"}):
            with self.subTest(kaputt=kaputt), mock.patch.object(rollen, "http", lambda *a, **k: kaputt), self.assertRaises(rollen.Fehler):
                rollen.bild_direkt(ziel, cfg["anbieter"]["fern"], KEY, {"prompt": "p"})

    def test_antwortangaben_kurz_und_ohne_gespiegelten_key(self):
        """Gefunden in der Prüfung zu 0.7.2: Die Angaben der Antwort gingen ungeschwärzt und ohne Grenze ins Ereignis."""
        ziel, cfg = rollen.loese(self.cfg(), "bild"), self.cfg()
        antwort = {"data": [{"b64_json": base64.b64encode(webp()).decode(), "revised_prompt": f"Bearer {KEY}"}],
                   "lang": "x" * 5000, "Böse Taste": 1, "flag": True, **{f"k{i}": i for i in range(30)}}
        with mock.patch.object(rollen, "http", lambda *a, **k: antwort):
            _, meta = rollen.bild_direkt(ziel, cfg["anbieter"]["fern"], KEY, {"prompt": "p"})
        self.assertNotIn(KEY, json.dumps(meta))
        self.assertLessEqual(len(meta), 12)
        self.assertEqual(len(meta["lang"]), 300)
        self.assertNotIn("Böse Taste", meta)
        self.assertNotIn("flag", meta)

    def test_modelle_nur_bildmodelle_wo_der_anbieter_es_sagt(self):
        cfg = {"anbieter": {"fern": {"openai": "https://fern.example/v1"}}}
        mit = {"data": [{"id": "Text", "modality": "text"}, {"id": "Z Image Turbo", "modality": "image"}]}
        ohne = {"data": [{"id": "a"}, {"id": "b"}]}
        with mock.patch.object(rollen, "http", lambda *a, **k: mit):
            self.assertEqual(rollen.modelle(cfg, "fern", bild=True), ["Z Image Turbo"])
            self.assertEqual(rollen.modelle(cfg, "fern"), ["Text", "Z Image Turbo"])
        with mock.patch.object(rollen, "http", lambda *a, **k: ohne):
            self.assertEqual(rollen.modelle(cfg, "fern", bild=True), ["a", "b"])


SEITE = "Satz mit Beleg.[^mueller]\n\n![Werkbank](/abbildungen/{name}.webp)\n{unterschrift}\n\n[^mueller]: [Quelle](/sources/mueller.md), S. 4\n"
KI = "*Abb.: Werkbank. KI-generiert mit Bildmodell (Ersatz), 2026-11-02.*"


class Grundlage(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("mueller")

    def seite(self, slug: str, body: str) -> None:
        self.core("new", "concept", slug, "--title", slug, "--description", "D", "--sources", "mueller")
        self.core("body", f"concepts/{slug}", "-", stdin=body)

    def html(self, slug: str) -> str:
        return self.py(f"result = wiki.reader_html()['concepts/{slug}']['html']")

    def bilddatei(self, name: str = "b.webp", data: bytes | None = None):
        pfad = self.base / "downloads" / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(webp(64, 36, chunk(b"EXIF", b"GEHEIM")) if data is None else data)
        return pfad


class AbbildungWebp(Grundlage):
    def test_anfrage_hoechstens_64_kb(self):
        gross = self.datei("gross.json", json.dumps({"x": "a" * 70000}))
        fehler = self.core("abbildung", "w", "--file", str(self.bilddatei()), "--rolle", "bild", "--modell", "m",
                           "--anfrage", str(gross), ok=False).stderr
        self.assertIn("64 KB", fehler)

    def test_ablegen_bereinigt(self):
        done = self.core("abbildung", "werkbank", "--file", str(self.bilddatei()))
        abgelegt = (self.root / "wiki" / "abbildungen" / "werkbank.webp").read_bytes()
        self.assertNotIn(b"GEHEIM", abgelegt)
        self.assertIn("EXIF", done.stderr)
        self.assertIn("Einbinden: ![Titel](/abbildungen/werkbank.webp)", done.stdout)
        self.assertIn("64x36", done.stdout)

    def test_png_und_name_in_anderem_format(self):
        png = self.bilddatei("b.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
        self.assertIn("cwebp", self.core("abbildung", "x", "--file", str(png), ok=False).stderr)
        svg = self.datei("a.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>')
        self.core("abbildung", "doppelt", "--file", str(svg))
        fehler = self.core("abbildung", "doppelt", "--file", str(self.bilddatei()), "--ersetzen", ok=False).stderr
        self.assertIn("anderen Format", fehler)


class Einbettung(Grundlage):
    def test_data_webp_mit_unterschrift_ohne_pfad(self):
        self.core("abbildung", "werkbank", "--file", str(self.bilddatei()))
        self.seite("k1", SEITE.format(name="werkbank", unterschrift=KI))
        html = self.html("k1")
        self.assertIn('<figure class="abbildung"><img class="raster" src="data:image/webp;base64,', html)
        self.assertIn('alt="Werkbank" width="64" height="36">', html)
        self.assertIn("<figcaption>Abb.: Werkbank. KI-generiert", html)
        self.assertNotIn("/abbildungen/", html)
        ziel = self.base / "dash.html"
        self.core("dashboard", "--out", str(ziel))
        self.assertIn("data:image/webp;base64,", ziel.read_text(encoding="utf-8"))

    def test_fremde_und_fehlende_bilder_nie_geladen(self):
        self.seite("k1", "![Fremd](data:image/webp;base64,UklGRg==) und ![P](/abbildungen/x.png)\n\n"
                         "![Weg](/abbildungen/weg.webp)\n*Abb.: Weg.[^mueller]*\n\nText.[^mueller]\n\n[^mueller]: [Quelle](/sources/mueller.md)\n")
        html = self.html("k1")
        self.assertNotIn("<img", html)
        self.assertIn("Bild nicht eingebunden: Fremd", html)
        self.assertIn("Abbildung nicht eingebunden: weg", html)


class Lint(Grundlage):
    def ereignis(self, name: str) -> None:
        self.py(f"wiki.append_event('create', 'abbildungen/{name}', 'Bild', modelle=[{{'harness': 'direkt', "
                f"'anbieter': 'ersatz', 'modell': 'Bildmodell', 'rolle': 'bild'}}])")

    def test_erzeugt_braucht_ki_vermerk_aber_keine_fussnote(self):
        self.core("abbildung", "werkbank", "--file", str(self.bilddatei()))
        self.ereignis("werkbank")
        self.seite("k1", SEITE.format(name="werkbank", unterschrift=KI))
        codes = self.lint_codes("concepts/k1")
        self.assertNotIn("figure_caption", codes)
        self.assertNotIn("figure_generated", codes)
        self.assertNotIn("figure_missing", codes)
        self.seite("k2", SEITE.format(name="werkbank", unterschrift="*Abb.: Werkbank.*"))
        self.assertIn("figure_generated", self.lint_codes("concepts/k2"))

    def test_kaputtes_webp_meldet_figure_missing(self):
        (self.root / "wiki" / "abbildungen").mkdir(exist_ok=True)
        (self.root / "wiki" / "abbildungen" / "kaputt.webp").write_bytes(b"RIFF kaputt")
        self.seite("k1", SEITE.format(name="kaputt", unterschrift="*Abb.: K. Nach Müller[^mueller].*"))
        self.assertIn("figure_missing", self.lint_codes("concepts/k1"))


class Ersatz(http.server.BaseHTTPRequestHandler):
    """Anbieter-Ersatz: /v1/images/generations gibt ein WebP zurück und merkt sich Anfrage und Kopf."""
    anfragen: list = []

    def do_POST(self):
        nutzlast = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Ersatz.anfragen.append((self.path, self.headers.get("authorization"), nutzlast))
        roh = json.dumps({"created": 1, "output_format": "webp", "data": [
            {"b64_json": base64.b64encode(webp(64, 36, chunk(b"XMP ", b"<meta/>"))).decode(), "revised_prompt": None}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def log_message(self, *args):
        pass


class BefehlBild(Grundlage):
    def setUp(self):
        super().setUp()
        Ersatz.anfragen = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ersatz)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        einstellungen = self.base / "einstellungen.toml"
        einstellungen.write_text(
            f'[anbieter.ersatz]\nname = "Ersatz"\nopenai = "http://127.0.0.1:{server.server_port}/v1"\n'
            'schluessel_env = "TEST_BILD_KEY"\ndaten = ["oeffentlich", "intern"]\n'
            '[rollen.bild]\nharness = "direkt"\nanbieter = "ersatz"\nmodell = "Bildmodell"\n', encoding="utf-8")
        umgebung = mock.patch.dict(os.environ, {"OKFW_EINSTELLUNGEN": str(einstellungen), "TEST_BILD_KEY": KEY})
        umgebung.start()
        self.addCleanup(umgebung.stop)

    def test_trocken_zeigt_was_gesendet_wuerde_und_sendet_nichts(self):
        out = self.core("bild", "werkbank", "--prompt", "Eine Werkbank", "--wunsch", "breit, Entwurf, ohne Menschen",
                        "--seed", "7", "--trocken").stdout
        gesendet = json.loads(out[out.index("{"):])
        self.assertEqual((gesendet["model"], gesendet["size"], gesendet["seed"], gesendet["guidance_scale"]),
                         ("Bildmodell", "1536x864", 7, 4))
        for text in ("Aus dem Wunsch erkannt", "Wirkt nur mit Guidance", "Tempo fein statt Entwurf", "Kurzer Prompt",
                     "Was gesendet wird (ohne Key)"):
            self.assertIn(text, out)
        self.assertNotIn(KEY, out)
        self.assertEqual(Ersatz.anfragen, [])
        self.assertFalse((self.root / "wiki" / "abbildungen" / "werkbank.webp").exists())

    def test_eigener_stil_aus_der_wiki_toml(self):
        ohne = self.core("bild", "a", "--prompt", "Ein Schlüssel", "--wunsch", "in meinem Design", "--trocken").stdout
        self.assertIn("keinen Stil [bild.stile] design", ohne)
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + '\n[bild.stile]\ndesign = "Flat, alabaster. {motiv}. Calm."\n', encoding="utf-8")
        out = self.core("bild", "a", "--prompt", "Ein Schlüssel", "--wunsch", "in meinem Design", "--trocken").stdout
        self.assertTrue(json.loads(out[out.index("{"):])["prompt"].startswith("Flat, alabaster. Ein Schlüssel. Calm."))
        self.assertNotIn("config_unknown", self.lint_codes())

    def test_erzeugt_bereinigt_protokolliert_ohne_key(self):
        done = self.core("bild", "werkbank", "--prompt", "Eine Werkbank", "--varianten", "2", "--seed", "40",
                         "--titel", "Werkbank", "--ohne", "Kabel")
        pfad, kopf, nutzlast = Ersatz.anfragen[0]
        self.assertEqual((pfad, kopf), ("/v1/images/generations", f"Bearer {KEY}"))
        self.assertLessEqual(set(nutzlast), set(b.FELDER))
        self.assertEqual([a[2]["seed"] for a in Ersatz.anfragen], [40, 41])
        for name in ("werkbank-1", "werkbank-2"):
            self.assertNotIn(b"<meta/>", (self.root / "wiki" / "abbildungen" / f"{name}.webp").read_bytes())
        self.assertIn("![Werkbank](/abbildungen/werkbank-2.webp)", done.stdout)
        self.assertIn("KI-generiert mit Bildmodell (Ersatz)", done.stdout)
        ereignisse = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(KEY, ereignisse + done.stdout + done.stderr)
        letztes = json.loads(ereignisse.strip().splitlines()[-1])
        self.assertEqual((letztes["page"], letztes["bild"]["anfrage"]["seed"], letztes["bild"]["modell"]),
                         ("abbildungen/werkbank-2", 41, "Bildmodell"))
        self.assertEqual(letztes["modelle"][0]["rolle"], "bild")
        self.assertEqual(letztes["bild"]["anfrage"]["negative_prompt"], "Kabel")
        self.core("bild", "werkbank-1", "--prompt", "x", ok=False)   # vorhanden: nur mit --ersetzen

    def test_gesperrte_rolle_sendet_nichts(self):
        (self.root / "wiki.toml").write_text((self.root / "wiki.toml").read_text(encoding="utf-8")
                                             + '\n[rollen]\nsperre = ["ersatz"]\n', encoding="utf-8")
        fehler = self.core("bild", "werkbank", "--prompt", "Eine Werkbank", ok=False).stderr
        self.assertIn("gesperrt", fehler)
        self.assertEqual(Ersatz.anfragen, [])


class RollenBefehl(unittest.TestCase):
    """rollen.py bild: der Weg, auf dem serve ein Bild holt."""

    def test_schreibt_das_bild_und_nennt_das_modell(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "wiki.toml").write_text('[rollen.bild]\nharness = "direkt"\nanbieter = "openai"\nmodell = "bild-modell"\n', encoding="utf-8")
            (tmp / "anfrage.json").write_text(json.dumps({"prompt": "p", "seed": 3}), encoding="utf-8")
            antwort = {"data": [{"b64_json": base64.b64encode(webp()).decode()}], "created": 1}
            gesehen = []
            umgebung = {"OKFW_EINSTELLUNGEN": str(tmp / "leer.toml"), "OPENAI_API_KEY": KEY}
            with mock.patch.dict(os.environ, umgebung), mock.patch.object(rollen, "http", lambda url, kopf, nutzlast=None, zeit=0: gesehen.append(nutzlast) or antwort), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as aus:
                code = rollen.main(["--wiki", str(tmp / "wiki.toml"), "bild", "--nutzlast", str(tmp / "anfrage.json"), "--aus", str(tmp / "b.webp"), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual((tmp / "b.webp").read_bytes(), webp())
            info = json.loads(aus.getvalue())
            self.assertEqual((info["modell"], info["meta"], gesehen[0]["seed"], gesehen[0]["model"]), ("bild-modell", {"created": 1}, 3, "bild-modell"))
            self.assertNotIn(KEY, aus.getvalue())
            with mock.patch.dict(os.environ, umgebung), mock.patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(rollen.main(["--wiki", str(tmp / "fehlt.toml"), "bild", "--nutzlast", "x", "--aus", "y"]), 2)


@unittest.skipUnless(shutil.which("node"), "node fehlt")
class Skript(unittest.TestCase):
    def test_dialog_laesst_sich_parsen(self):
        lauf = subprocess.run(["node", "--check", str(SKILL / "templates" / "auftrag_bild.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)


class Dialog(ServeTest):
    """serve: Vorschau der Anfrage, Auftrag bild mit Varianten, Annehmen legt ab. rollen.py ist durch eine Attrappe ersetzt."""

    def setUp(self):
        super().setUp()
        okfw_serve.rollen.setze_rolle("bild", "direkt", "openai", "bild-modell")

    def modell(self, gesehen: list):
        def attrappe(argv, ordner, abbruch=None):
            nutzlast = json.loads(Path(argv[argv.index("--nutzlast") + 1]).read_text(encoding="utf-8"))
            gesehen.append((argv, nutzlast))
            Path(argv[argv.index("--aus") + 1]).write_bytes(webp(64, 36, chunk(b"EXIF", b"KAMERA")))
            return 0, json.dumps({"harness": "direkt", "anbieter": "openai", "modell": "bild-modell", "bytes": 1,
                                  "meta": {"created": 1}, "dauer_s": 0.5}), ""
        return mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe)

    def test_vorschau_aus_worten_und_schaltern(self):
        status, _, d = self.post("/api/bild/vorschau", {"prompt": "Eine Werkbank", "wunsch": "breit, Entwurf, drei Varianten, ohne people"})
        self.assertEqual(status, 200, d)
        e = d["einstellungen"]
        self.assertEqual((e["format"], e["tempo"], e["varianten"], e["ohne"]), ("quer", "fein", 3, ["people"]))
        self.assertEqual((d["gesendet"]["model"], d["gesendet"]["size"], d["rolle"]["gesperrt"]), ("bild-modell", "1536x864", ""))
        self.assertIn("flach", d["stile"]["eingebaut"])
        status, _, d = self.post("/api/bild/vorschau", {"prompt": "Eine Werkbank", "format": "1000x700", "seed": 5, "tempo": "entwurf"})
        self.assertEqual((d["gesendet"]["size"], d["gesendet"]["seed"], d["gesendet"]["num_inference_steps"]), ("1008x704", 5, 8))
        self.assertTrue(any("gerundet" in h for h in d["hinweise"]))
        self.assertNotIn(GEHEIM, json.dumps(d))
        self.assertEqual(self.post("/api/bild/vorschau", {"prompt": "x", "varianten": 9})[0], 422)

    def test_varianten_annehmen_legt_nur_die_gewaehlte_ab(self):
        gesehen = []
        with self.modell(gesehen):
            status, _, a = self.post("/api/auftrag", {"typ": "bild", "name": "werkbank", "titel": "Werkbank", "prompt": "Eine Werkbank",
                                                       "varianten": 2, "seed": 10, "tempo": "entwurf"})
            self.assertEqual(status, 202, a)
            d = self.auftrag_abwarten(a["id"])
        self.assertEqual(d["zustand"], "fertig", d)
        self.assertEqual([n["seed"] for _, n in gesehen], [10, 11])
        self.assertEqual(gesehen[0][0][gesehen[0][0].index("--wiki") + 2], "bild")
        self.assertFalse((self.root / "wiki" / "abbildungen").exists())
        zweite = d["aenderungen"][1]
        self.assertTrue(zweite["daten"]["vorschau"].startswith("data:image/webp;base64,"))
        self.assertNotIn("webp", zweite["daten"])
        self.assertIn("Illustration, kein Beleg", " ".join(d["warnungen"]))
        status, _, antwort = self.post("/api/vorschlag/annehmen", {"auftrag": d["id"], "aenderung": "a2"})
        self.assertEqual(status, 200, antwort)
        datei = self.root / "wiki" / "abbildungen" / "werkbank-2.webp"
        self.assertNotIn(b"KAMERA", datei.read_bytes())
        self.assertFalse((self.root / "wiki" / "abbildungen" / "werkbank-1.webp").exists())
        self.assertTrue(antwort["einbindung"].startswith("![Werkbank](/abbildungen/werkbank-2.webp)\n*Abb.: Werkbank. KI-generiert mit bild-modell (OpenAI), "))
        ereignis = self.wiki().read_events()[-1]
        self.assertEqual((ereignis["page"], ereignis["by"], ereignis["modelle"][0]["rolle"]), ("abbildungen/werkbank-2", "human:tester", "bild"))
        self.assertEqual((ereignis["bild"]["anfrage"]["seed"], ereignis["bild"]["modell"]), (11, "bild-modell"))
        auftraege = "".join(f.read_text(encoding="utf-8") for f in (self.root / "wiki" / ".auftraege").glob("*.json"))
        self.assertNotIn(GEHEIM, auftraege + json.dumps(ereignis))
        self.assertEqual(self.post("/api/vorschlag/annehmen", {"auftrag": d["id"], "aenderung": "a2"})[0], 409)

    def test_gesperrt_und_vergeben_starten_nicht(self):
        gesehen = []
        with self.modell(gesehen):
            self.assertEqual(self.post("/api/auftrag", {"typ": "bild", "name": "x", "prompt": "p", "varianten": 0})[0], 422)
            (self.root / "wiki" / "abbildungen").mkdir()
            (self.root / "wiki" / "abbildungen" / "belegt.svg").write_text("<svg/>", encoding="utf-8")
            self.assertEqual(self.post("/api/auftrag", {"typ": "bild", "name": "belegt", "prompt": "p"})[0], 409)
            self.assertEqual(self.post("/api/bild/vorschau", {"prompt": "p", "name": "belegt"})[2]["belegt"], ["belegt"])
            okfw_serve.rollen.setze_rolle("bild", "claude", "anthropic", "m")
            status, _, a = self.post("/api/auftrag", {"typ": "bild", "name": "frei", "prompt": "p"})
            self.assertEqual(status, 422)
            self.assertIn("OpenAI-kompatiblen", a["fehler"])
        self.assertEqual(gesehen, [])


if __name__ == "__main__":
    unittest.main()
