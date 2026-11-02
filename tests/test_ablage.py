"""Karten ablösen, löschen und zurückholen: Befehle (deprecate, delete, restore) und Dashboard (okfw serve).

Die Befehle laufen wie sonst über `uv run --script`, die Oberfläche gegen einen echten Server im Testprozess (siehe
test_serve.py). Der Ablauf der Rückfragen in serve.js läuft mit node, mit Attrappen für frage() und api().
"""

import json
import re
import shutil
import subprocess
import unittest

from .hilfe import SKILL, WikiTest
from .test_serve import ServeTest, okfw, still

JS = SKILL / "templates" / "serve.js"
TEXT = "Aussage[^q1] und [Link](/sources/q1.md).\n\n[^q1]: [Quelle](/sources/q1.md), S. 4.\n"


class BefehlBasis(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "nutzt", "--title", "Nutzt", "--description", "d", "--sources", "q1")
        self.core("body", "concepts/nutzt", "-", stdin=TEXT)
        self.core("new", "concept", "frei", "--title", "Frei", "--description", "d")

    def karte(self, pid):
        return self.root / "wiki" / f"{pid}.md"

    def korb(self):
        return self.root / "wiki" / ".papierkorb"

    def ereignisse(self, art):
        zeilen = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        return [e for e in map(json.loads, zeilen) if e["kind"] == art]

    def ablegen(self, pid="concepts/frei", *mehr):
        self.core("deprecate", pid, "--by", "human:tester")
        return self.core("delete", pid, "--yes", "--by", "human:tester", *mehr)

    def eintrag(self):
        return json.loads(self.core("restore", "--json").stdout)[0]["eintrag"]

    def falscher_eintrag(self, name="20261101T120000Z-falsch", **felder):
        ordner = self.korb() / name
        ordner.mkdir(parents=True)
        (ordner / "karte.md").write_text("---\ntype: concept\n---\nFremd\n", encoding="utf-8")
        (ordner / "eintrag.json").write_text(json.dumps({"seite": "concepts/neu", **felder}), encoding="utf-8")
        return name

    def rohquelle(self, slug="q2"):
        self.core("capture", slug, "--url", f"https://example.org/{slug}", "--file", str(self.datei(f"{slug}.txt")),
                  "--license", "CC-BY-4.0", "--license-url", "https://creativecommons.org/licenses/by/4.0/")
        self.core("new", "source", slug, "--title", "Mit Rohquelle", "--description", "d", "--access", "fulltext",
                  "--resource", f"https://example.org/{slug}", ok=None)
        if not self.karte(f"sources/{slug}").exists():
            self.fail("Quellenkarte nicht angelegt")
        return self.root / "raw" / slug


class Abloesen(BefehlBasis):
    def test_setzt_status_und_nachfolger_die_karte_bleibt(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester", "--nachfolger", "concepts/nutzt", "--note", "veraltet")
        fm = self.fm("concepts/frei")
        self.assertEqual((fm["status"], fm["superseded_by"]), ("deprecated", "/concepts/nutzt.md"))
        e = self.ereignisse("deprecate")[-1]
        self.assertEqual((e["page"], e["by"]), ("concepts/frei", "human:tester"))
        self.assertIn("veraltet", e["detail"])
        self.assertIn("Als überholt markiert", (self.root / "wiki" / "log.md").read_text(encoding="utf-8"))

    def test_jeder_akteur_darf_ablösen(self):
        self.core("deprecate", "concepts/frei", "--by", "claude-code/claude-opus-5-5")
        self.assertEqual(self.fm("concepts/frei")["status"], "deprecated")

    def test_abgewiesen(self):
        self.core("deprecate", "concepts/frei", "--by", "kein akteur", ok=False)
        self.core("deprecate", "concepts/fehlt", "--by", "human:tester", ok=False)
        self.core("deprecate", "concepts/frei", "--by", "human:tester", "--nachfolger", "concepts/fehlt", ok=False)
        self.core("deprecate", "concepts/frei", "--by", "human:tester", "--nachfolger", "concepts/frei", ok=False)
        self.assertNotEqual(self.fm("concepts/frei").get("status"), "deprecated")
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.core("deprecate", "concepts/frei", "--by", "human:tester", ok=False)

    def test_superseded_missing(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester", "--nachfolger", "concepts/nutzt")
        self.assertNotIn("superseded_missing", self.lint_codes("concepts/frei"))
        self.karte("concepts/nutzt").unlink()
        self.assertIn("superseded_missing", self.lint_codes("concepts/frei"))


class Folgen(BefehlBasis):
    def test_vorschau_nennt_alles_und_aendert_nichts(self):
        vorher = self.karte("sources/q1").read_bytes()
        aus = self.core("delete", "sources/q1").stdout
        for teil in ("source_missing", "concepts/nutzt", "[^q1]", "broken_link", "erst ablösen", "Nichts geändert"):
            self.assertIn(teil, aus)
        self.assertEqual(self.karte("sources/q1").read_bytes(), vorher)
        self.assertFalse(self.korb().exists())
        self.assertEqual(self.ereignisse("delete"), [])

    def test_json_trennt_die_arten(self):
        f = json.loads(self.core("delete", "sources/q1", "--json").stdout)
        self.assertEqual([(b["seite"], b["id"], b["fussnoten"]) for b in f["belege"]], [("concepts/nutzt", "q1", 1)])
        self.assertEqual([v["fussnote"] for v in f["fussnoten"]], ["q1"])
        self.assertEqual([v["seite"] for v in f["links"]], ["concepts/nutzt"])

    def test_code_zaehlt_nicht_wie_im_lint(self):
        self.core("body", "concepts/frei", "-", stdin="`[x](/concepts/nutzt.md)`\n\n```\n[y](/concepts/nutzt.md)\n```\n")
        self.assertEqual(json.loads(self.core("delete", "concepts/nutzt", "--json").stdout)["links"], [])

    def test_nachfolger_symlink_und_unlesbare_seite(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester", "--nachfolger", "concepts/nutzt")
        (self.root / "wiki" / "concepts" / "zweig.md").symlink_to("nutzt.md")
        (self.root / "wiki" / "concepts" / "kaputt.md").write_text("---\n: : :\n---\nText\n", encoding="utf-8")
        f = json.loads(self.core("delete", "concepts/nutzt", "--json").stdout)
        self.assertEqual(f["nachfolger"], ["concepts/frei"])
        self.assertEqual(f["symlinks"], ["concepts/zweig"])
        self.assertEqual(f["unlesbar"], ["concepts/kaputt"])


class Loeschen(BefehlBasis):
    def test_erst_ablösen(self):
        self.assertIn("erst ablösen", self.core("delete", "concepts/frei", "--yes", "--by", "human:tester", ok=False).stderr)
        self.assertTrue(self.karte("concepts/frei").exists())

    def test_nur_ein_mensch(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.core("delete", "concepts/frei", "--yes", ok=False)
        self.core("delete", "concepts/frei", "--yes", "--by", "claude-code/claude-opus-5-5", ok=False)   # nur Menschen löschen
        for wer in ("claude-code/claude-opus-5-5", "process:x", "human:", "human:a b"):
            self.core("delete", "concepts/frei", "--yes", "--by", wer, ok=False)
        self.assertTrue(self.karte("concepts/frei").exists())

    def test_ablauf_papierkorb_ereignis_indexe(self):
        self.ablegen()
        self.assertFalse(self.karte("concepts/frei").exists())
        e = self.ereignisse("delete")[-1]
        self.assertEqual((e["page"], e["by"]), ("concepts/frei", "human:tester"))
        name = self.eintrag()
        self.assertIn(name, e["detail"])
        self.assertIn("status: deprecated", (self.korb() / name / "karte.md").read_text(encoding="utf-8"))
        self.assertEqual((self.korb() / ".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertNotIn("frei", (self.root / "wiki" / "concepts" / "index.md").read_text(encoding="utf-8"))
        self.assertIn("Entfernt", (self.root / "wiki" / "log.md").read_text(encoding="utf-8"))
        self.assertEqual(self.core("rebuild", "--check", ok=None).returncode, 0)
        self.assertNotIn("frontmatter_invalid", self.lint_codes())
        self.assertNotIn("broken_link", self.lint_codes())
        self.assertEqual(self.py("result = [p.id for p in wiki.pages() if 'papierkorb' in p.id]"), [])

    def test_letzte_karte_laesst_keinen_alten_index_zurueck(self):
        self.core("new", "concept", "tief/unten", "--title", "Unten", "--description", "d", ok=None)
        tief = self.root / "wiki" / "concepts" / "tief"
        tief.mkdir(exist_ok=True)
        (tief / "unten.md").write_text("---\ntype: concept\ntitle: Unten\ndescription: d\n---\nText\n", encoding="utf-8")
        self.core("rebuild")
        self.assertTrue((tief / "index.md").exists())
        self.ablegen("concepts/tief/unten")
        self.assertFalse((tief / "index.md").exists())
        self.assertTrue((self.root / "wiki" / "concepts" / "index.md").exists())   # im Ordner darüber liegen noch Seiten
        self.assertEqual(self.core("rebuild", "--check", ok=None).returncode, 0)
        self.core("restore", self.eintrag(), "--by", "human:tester")
        self.assertIn("Unten", (tief / "index.md").read_text(encoding="utf-8"))

    def test_belege_sperren_bis_zur_bestaetigung(self):
        self.core("deprecate", "sources/q1", "--by", "human:tester")
        fehler = self.core("delete", "sources/q1", "--yes", "--by", "human:tester", ok=False).stderr
        self.assertIn("source_missing", fehler)
        self.assertIn("concepts/nutzt", fehler)
        self.assertTrue(self.karte("sources/q1").exists())
        self.core("delete", "sources/q1", "--yes", "--by", "human:tester", "--belege-brechen")
        self.assertFalse(self.karte("sources/q1").exists())
        self.assertIn("source_missing", self.lint_codes("concepts/nutzt"))
        self.assertIn("broken_link", self.lint_codes("concepts/nutzt"))
        self.assertIn("ausdrücklich bestätigt: concepts/nutzt", self.ereignisse("delete")[-1]["detail"])

    def test_unlesbare_karte_laesst_sich_ohne_abloesen_loeschen(self):
        kaputt = self.karte("concepts/kaputt")
        kaputt.write_text("---\n: : :\n---\nText\n", encoding="utf-8")
        inhalt = kaputt.read_bytes()
        self.core("deprecate", "concepts/kaputt", "--by", "human:tester", ok=False)
        self.core("delete", "concepts/kaputt", "--yes", "--by", "human:tester")
        self.assertFalse(kaputt.exists())
        self.core("restore", self.eintrag(), "--by", "human:tester")
        self.assertEqual(kaputt.read_bytes(), inhalt)

    def test_protected_sperrt(self):
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("protected = []", 'protected = ["wiki/concepts/fr*"]'), encoding="utf-8")
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.assertIn("geschützt", self.core("delete", "concepts/frei", "--yes", "--by", "human:tester", ok=False).stderr)
        self.assertTrue(self.karte("concepts/frei").exists())

    def test_ohne_ereignis_bleibt_alles_stehen(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        vorher = self.karte("concepts/frei").read_bytes()
        self.py("""
def kaputt(*a, **k):
    raise OSError("Platte voll")
wiki.append_event = kaputt
try:
    wiki.delete_page("concepts/frei", "human:tester")
    result = "kein Fehler"
except okfw.WikiError as exc:
    result = str(exc)
assert "Nichts verschoben" in result, result
""")
        self.assertEqual(self.karte("concepts/frei").read_bytes(), vorher)
        self.assertEqual([d for d in self.korb().iterdir() if d.name != ".gitignore"], [])

    def test_gescheitertes_zurueckverschieben_behaelt_den_eintrag(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.py("""
import os
echt = os.replace
def replace(von, nach):
    if str(nach).endswith("concepts/frei.md"):
        raise OSError("gesperrt")
    echt(von, nach)
os.replace = replace
def kaputt(*a, **k):
    raise OSError("Platte voll")
wiki.append_event = kaputt
try:
    wiki.delete_page("concepts/frei", "human:tester")
    result = "kein Fehler"
except okfw.WikiError as exc:
    result = str(exc)
assert "von Hand" in result and "Nichts verschoben" not in result, result
""")
        self.assertFalse(self.karte("concepts/frei").exists())
        name = self.eintrag()
        self.assertTrue((self.korb() / name / "karte.md").is_file())
        self.core("restore", name, "--by", "human:tester")
        self.assertTrue(self.karte("concepts/frei").exists())

    def test_zweimal_in_derselben_sekunde(self):
        self.ablegen()
        self.core("restore", self.eintrag(), "--by", "human:tester")
        self.py("""
okfw_now = __import__("okfw_kern.wiki_ablage", fromlist=["now_iso"])
okfw_now.now_iso = lambda: "2026-11-01T12:00:00Z"
wiki.delete_page("concepts/frei", "human:tester")
(wiki.bundle / "concepts/frei.md").write_text((wiki.bundle / ".papierkorb/20261101T120000Z-concepts-frei/karte.md").read_text())
wiki._pages = None
wiki.delete_page("concepts/frei", "human:tester")
""")
        self.assertTrue((self.korb() / "20261101T120000Z-concepts-frei-2").is_dir())


class Rohquelle(BefehlBasis):
    def test_bleibt_ohne_eigene_bestaetigung(self):
        paket = self.rohquelle()
        self.assertIn("raw/q2", self.core("delete", "sources/q2").stdout)
        self.ablegen("sources/q2")
        self.assertTrue((paket / "manifest.json").is_file())
        self.assertIn("Rohquelle bleibt: raw/q2", self.ereignisse("delete")[-1]["detail"])
        self.assertIn("raw_uningested", self.lint_codes())

    def test_geht_mit_und_kommt_zurueck(self):
        paket = self.rohquelle()
        vorher = sorted(p.relative_to(paket).as_posix() for p in paket.rglob("*"))
        self.ablegen("sources/q2", "--mit-rohquelle")
        self.assertFalse(paket.exists())
        name = self.eintrag()
        self.assertTrue((self.korb() / name / "rohquelle" / "manifest.json").is_file())
        self.assertIn("mit Rohquelle q2", self.core("restore").stdout)
        self.core("restore", name, "--by", "human:tester")
        self.assertEqual(sorted(p.relative_to(paket).as_posix() for p in paket.rglob("*")), vorher)
        self.assertTrue(self.karte("sources/q2").exists())
        self.assertFalse((self.korb() / name).exists())

    def test_geteilte_rohquelle_bleibt(self):
        self.rohquelle()
        self.core("new", "concept", "teilt", "--title", "Teilt", "--description", "d")
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/teilt'), {'raw_packet': 'raw/q2'})")
        self.core("deprecate", "sources/q2", "--by", "human:tester")
        fehler = self.core("delete", "sources/q2", "--yes", "--by", "human:tester", "--mit-rohquelle", ok=False).stderr
        self.assertIn("concepts/teilt", fehler)
        self.assertTrue(self.karte("sources/q2").exists())

    def test_unlesbare_karte_haelt_die_rohquelle_fest(self):
        paket = self.rohquelle()
        self.karte("concepts/kaputt").write_text("---\n: : :\n---\nText\n", encoding="utf-8")
        self.core("deprecate", "sources/q2", "--by", "human:tester")
        f = json.loads(self.core("delete", "sources/q2", "--json").stdout)
        self.assertEqual(f["rohquelle_geteilt"], ["concepts/kaputt"])
        fehler = self.core("delete", "sources/q2", "--yes", "--by", "human:tester", "--mit-rohquelle", ok=False).stderr
        self.assertIn("concepts/kaputt", fehler)
        self.assertTrue((paket / "manifest.json").is_file())
        self.assertTrue(self.karte("sources/q2").exists())

    def test_ohne_rohquelle_kein_mitnehmen(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.core("delete", "concepts/frei", "--yes", "--by", "human:tester", "--mit-rohquelle", ok=False)
        self.assertTrue(self.karte("concepts/frei").exists())

    def test_raw_packet_ausserhalb_bleibt_unangetastet(self):
        fremd = self.base / "fremd"
        fremd.mkdir()
        (fremd / "geheim.txt").write_text("x", encoding="utf-8")
        for wert in ("../fremd", str(fremd)):
            self.py(f"wiki._write_frontmatter(wiki.page_path('concepts/frei'), {{'raw_packet': {wert!r}, 'status': 'deprecated'}})")
            f = json.loads(self.core("delete", "concepts/frei", "--json").stdout)
            self.assertIsNone(f["rohquelle"])
            self.core("delete", "concepts/frei", "--yes", "--by", "human:tester", "--mit-rohquelle", ok=False)
            self.assertTrue((fremd / "geheim.txt").is_file())

    def test_paket_als_symlink_bleibt_unangetastet(self):
        (self.root / "raw").mkdir(exist_ok=True)
        fremd = self.base / "fremd"
        fremd.mkdir()
        (self.root / "raw" / "q1").symlink_to(fremd)
        f = json.loads(self.core("delete", "sources/q1", "--json").stdout)
        self.assertIsNone(f["rohquelle"])
        self.assertIn("unangetastet", f["rohquelle_hinweis"])


class Zurueckholen(BefehlBasis):
    def test_inhalt_und_ereignis(self):
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        vorher = self.karte("concepts/frei").read_bytes()
        self.core("delete", "concepts/frei", "--yes", "--by", "human:tester")
        name = self.eintrag()
        self.core("restore", name, "--by", "claude-code/claude-opus-5-5")
        self.assertEqual(self.karte("concepts/frei").read_bytes(), vorher)
        e = self.ereignisse("restore")[-1]
        self.assertEqual((e["page"], e["by"]), ("concepts/frei", "claude-code/claude-opus-5-5"))
        self.assertIn("frei", (self.root / "wiki" / "concepts" / "index.md").read_text(encoding="utf-8"))
        self.assertIn("Wiederhergestellt", (self.root / "wiki" / "log.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.core("restore", "--json").stdout), [])

    def test_ziel_belegt(self):
        self.ablegen()
        name = self.eintrag()
        self.core("new", "concept", "frei", "--title", "Neu", "--description", "d")
        neu = self.karte("concepts/frei").read_bytes()
        self.assertIn("existiert schon", self.core("restore", name, "--by", "human:tester", ok=False).stderr)
        self.assertEqual(self.karte("concepts/frei").read_bytes(), neu)
        self.assertTrue((self.korb() / name / "karte.md").is_file())

    def test_veraenderter_eintrag_wird_gemeldet(self):
        self.ablegen()
        name = self.eintrag()
        karte = self.korb() / name / "karte.md"
        karte.write_text(karte.read_text(encoding="utf-8") + "dazu\n", encoding="utf-8")
        self.assertIn("weicht vom Stand", self.core("restore", name, "--by", "human:tester").stdout)
        self.assertIn("Weicht vom Stand", self.ereignisse("restore")[-1]["detail"])

    def test_braucht_akteur(self):
        self.ablegen()
        self.core("restore", self.eintrag(), ok=False)

    def test_geschuetztes_ziel(self):
        name = self.falscher_eintrag()
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("protected = []", 'protected = ["wiki/concepts/ne*"]'), encoding="utf-8")
        self.assertIn("geschützt", self.core("restore", name, "--by", "human:tester", ok=False).stderr)
        self.assertFalse(self.karte("concepts/neu").exists())
        self.assertTrue((self.korb() / name / "karte.md").is_file())


class Pfadausbruch(BefehlBasis):
    def test_nur_seiten_des_bundles(self):
        draussen = self.base / "draussen.md"
        draussen.write_text("---\nstatus: deprecated\n---\nText\n", encoding="utf-8")
        for pid in ("../../draussen", str(draussen), "../wiki.toml", "index", "log", "concepts/index", ".papierkorb/x", ".events"):
            with self.subTest(pid=pid):
                self.assertIn("Seite fehlt", self.core("delete", pid, ok=False).stderr)
                self.core("delete", pid, "--yes", "--by", "human:tester", "--belege-brechen", ok=False)
                self.core("deprecate", pid, "--by", "human:tester", ok=False)
        self.assertTrue(draussen.exists())
        self.assertTrue((self.root / "wiki" / "index.md").exists())
        self.assertTrue((self.root / "wiki" / "log.md").exists())

    def test_karte_als_symlink(self):
        draussen = self.base / "draussen.md"
        draussen.write_text("---\ntype: concept\nstatus: deprecated\n---\nText\n", encoding="utf-8")
        (self.root / "wiki" / "concepts" / "link.md").symlink_to(draussen)
        self.core("delete", "concepts/link", "--yes", "--by", "human:tester", ok=False)
        (self.root / "wiki" / "concepts" / "link.md").unlink()
        (self.root / "wiki" / "concepts" / "innen.md").symlink_to("frei.md")
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.core("deprecate", "concepts/innen", "--by", "human:tester", ok=False)
        self.assertIn("symbolischer Link", self.core("delete", "concepts/innen", "--yes", "--by", "human:tester", ok=False).stderr)
        self.assertTrue(self.karte("concepts/frei").exists())
        self.assertTrue(draussen.exists())

    def test_papierkorb_als_symlink(self):
        ziel = self.base / "anderswo"
        ziel.mkdir()
        self.korb().symlink_to(ziel)
        self.core("deprecate", "concepts/frei", "--by", "human:tester")
        self.core("delete", "concepts/frei", "--yes", "--by", "human:tester", ok=False)
        self.core("restore", ok=False)
        self.assertTrue(self.karte("concepts/frei").exists())
        self.assertEqual(list(ziel.iterdir()), [])

    def test_gefaelschte_ziele(self):
        draussen = self.base / "ziel.md"
        for i, seite in enumerate(("../../ziel", str(self.base / "ziel"), "..\\..\\ziel", ".papierkorb/x", "index", "log",
                                   "concepts/index", "abbildungen/x", ".hooks/pre-commit")):
            with self.subTest(seite=seite):
                name = self.falscher_eintrag(f"20261101T12000{i}Z-falsch", seite=seite)
                self.assertIn("keine Seite", self.core("restore", name, "--by", "human:tester", ok=False).stderr)
                self.assertFalse(draussen.exists())

    def test_gefaelschte_namen(self):
        self.falscher_eintrag()
        for name in ("../../projekt/wiki", "..", ".", "20261101T120000Z-falsch/../..", "20261101T120000Z-falsch/", "x", ""):
            with self.subTest(name=name):
                self.core("restore", name, "--by", "human:tester", ok=False)
        self.assertFalse(self.karte("concepts/neu").exists())

    def test_symlinks_im_eintrag(self):
        draussen = self.base / "geheim.md"
        draussen.write_text("geheim\n", encoding="utf-8")
        name = self.falscher_eintrag()
        karte = self.korb() / name / "karte.md"
        karte.unlink()
        karte.symlink_to(draussen)
        self.assertIn("Symlink", self.core("restore", name, "--by", "human:tester", ok=False).stderr)
        self.assertFalse(self.karte("concepts/neu").exists())

    def test_ordner_im_ziel_zeigt_nach_draussen(self):
        draussen = self.base / "anderswo"
        draussen.mkdir()
        (self.root / "wiki" / "ausgang").symlink_to(draussen)
        name = self.falscher_eintrag(seite="ausgang/neu")
        self.core("restore", name, "--by", "human:tester", ok=False)
        self.assertEqual(list(draussen.iterdir()), [])

    def test_gefaelschte_rohquelle(self):
        ziel = self.base / "raus"
        for i, roh in enumerate(("../../raus", "/tmp/raus", "..", ".versteckt", "a/b", 7)):
            with self.subTest(roh=roh):
                name = self.falscher_eintrag(f"20261101T12000{i}Z-falsch", rohquelle=roh)
                (self.korb() / name / "rohquelle").mkdir()
                self.core("restore", name, "--by", "human:tester", ok=False)
                self.assertFalse(self.karte("concepts/neu").exists())
                self.assertFalse(ziel.exists())

    def test_rohquelle_mit_symlink_innen(self):
        (self.base / "geheim").write_text("x", encoding="utf-8")
        name = self.falscher_eintrag(rohquelle="neu")
        paket = self.korb() / name / "rohquelle"
        paket.mkdir()
        (paket / "innen").symlink_to(self.base / "geheim")
        self.assertIn("Symlink", self.core("restore", name, "--by", "human:tester", ok=False).stderr)
        self.assertFalse(self.karte("concepts/neu").exists())

    def test_kaputte_eintragsdatei(self):
        name = self.falscher_eintrag()
        datei = self.korb() / name / "eintrag.json"
        for roh in ("kein json", "[1]", '{"seite": 7}', "x" * 70000):
            datei.write_text(roh, encoding="utf-8")
            self.core("restore", name, "--by", "human:tester", ok=False)
        self.assertIn(name, self.core("restore").stdout)   # die Liste zeigt ihn mit Fehler, statt zu scheitern
        self.assertEqual(json.loads(self.core("restore", "--json").stdout)[0]["eintrag"], name)

    def test_guard_schuetzt_den_papierkorb(self):
        self.ablegen()
        ziel = self.korb() / self.eintrag() / "karte.md"
        for werkzeug in ("Write", "Edit"):
            done = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": werkzeug, "cwd": str(self.root),
                                       "tool_input": {"file_path": str(ziel), "content": "x"}})
            self.assertIn('"permissionDecision": "deny"', done.stdout)


class FremdesBundle(BefehlBasis):
    def setUp(self):
        super().setUp()
        self.anderes = self.base / "anderes"
        self.anderes.mkdir()
        self.core("init", "--profile", self.profile, root=self.anderes)
        self.core("new", "concept", "fremd", "--title", "Fremd", "--description", "d", root=self.anderes)
        self.fremde_karte = self.anderes / "wiki" / "concepts" / "fremd.md"
        self.core("deprecate", "concepts/fremd", "--by", "human:tester", root=self.anderes)

    def test_seite_eines_anderen_wikis_ist_hier_unbekannt(self):
        for pid in ("concepts/fremd", "../../anderes/wiki/concepts/fremd", str(self.fremde_karte)):
            self.core("delete", pid, "--yes", "--by", "human:tester", ok=False)
        self.assertTrue(self.fremde_karte.exists())

    def test_eintrag_kann_nicht_ins_andere_wiki_schreiben(self):
        name = self.falscher_eintrag(seite="../../anderes/wiki/concepts/neu")
        self.core("restore", name, "--by", "human:tester", ok=False)
        self.assertFalse((self.anderes / "wiki" / "concepts" / "neu.md").exists())

    def test_eintraege_bleiben_im_eigenen_wiki(self):
        self.core("delete", "concepts/fremd", "--yes", "--by", "human:tester", root=self.anderes)
        name = json.loads(self.core("restore", "--json", root=self.anderes).stdout)[0]["eintrag"]
        self.assertEqual(json.loads(self.core("restore", "--json").stdout), [])
        self.core("restore", name, "--by", "human:tester", ok=False)
        self.assertFalse(self.fremde_karte.exists())

    def test_verweis_aus_dem_anderen_wiki_ist_kein_verweis(self):
        self.core("body", "concepts/frei", "-", stdin="[raus](../../../anderes/wiki/concepts/fremd.md)\n")
        self.assertEqual(json.loads(self.core("delete", "concepts/fremd", "--json", root=self.anderes).stdout)["links"], [])


class Oberflaeche(ServeTest):
    def setUp(self):
        super().setUp()
        self.kern("new", "concept", "nutzt", "--title", "Nutzt", "--description", "d", "--sources", "q1")

    def stand(self, pid):
        status, _, daten = self.anfrage("GET", f"/api/seite?id={pid}")
        self.assertEqual(status, 200)
        return daten["hash"]

    def abloesen(self, pid="concepts/kern", **mehr):
        return self.post("/api/abloesen", {"id": pid, "hash": self.stand(pid), **mehr})

    def korb(self):
        return self.root / "wiki" / ".papierkorb"

    def eintraege(self):
        status, _, daten = self.anfrage("GET", "/api/papierkorb")
        self.assertEqual(status, 200)
        return daten["eintraege"]

    def ereignisse(self):
        zeilen = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        return list(map(json.loads, zeilen))

    def test_abgeloeste_karte_wird_nicht_freigegeben(self):
        self.abloesen()
        vorher = self.datei("concepts/kern").read_bytes()
        status, _, daten = self.post("/api/freigabe", {"ids": ["concepts/kern", "concepts/nutzt"]})
        self.assertEqual(status, 422, daten)
        self.assertIn("concepts/kern", daten["fehler"])
        self.assertEqual(self.datei("concepts/kern").read_bytes(), vorher)
        self.assertNotEqual(self.seite("concepts/nutzt").fm.get("status"), "stable")

    def test_ablauf(self):
        status, _, daten = self.abloesen(nachfolger="concepts/nutzt", notiz="alt")
        self.assertEqual(status, 200, daten)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "deprecated")
        self.assertEqual(self.seite("concepts/kern").fm["superseded_by"], "/concepts/nutzt.md")
        self.assertEqual(self.ereignisse()[-1]["by"], "human:tester")
        status, _, f = self.anfrage("GET", "/api/folgen?id=concepts/kern")
        self.assertEqual((status, f["folgen"]["sperren"]), (200, []))
        self.assertTrue(any("Keine andere Karte" in z for z in f["zeilen"]))
        status, _, daten = self.post("/api/loeschen", {"id": "concepts/kern", "hash": f["hash"]})
        self.assertEqual(status, 200, daten)
        self.assertFalse(self.datei("concepts/kern").exists())
        e = self.ereignisse()[-1]
        self.assertEqual((e["kind"], e["page"], e["by"]), ("delete", "concepts/kern", "human:tester"))
        self.assertNotIn("kern", (self.root / "wiki" / "concepts" / "index.md").read_text(encoding="utf-8"))
        self.assertEqual(self.anfrage("GET", "/api/seite?id=concepts/kern")[0], 404)
        self.assertEqual([x["seite"] for x in self.eintraege()], ["concepts/kern"])
        status, _, daten = self.post("/api/wiederherstellen", {"eintrag": daten["eintrag"]})
        self.assertEqual(status, 200, daten)
        self.assertTrue(self.datei("concepts/kern").exists())
        self.assertEqual(self.eintraege(), [])
        self.assertEqual(self.ereignisse()[-1]["kind"], "restore")

    def test_deprecated_steht_im_dashboard(self):
        self.abloesen()
        self.assertIn('"deprecated": true', self.anfrage("GET", "/")[2].replace('"deprecated":true', '"deprecated": true'))

    def test_erst_ablösen(self):
        status, _, daten = self.post("/api/loeschen", {"id": "concepts/kern", "hash": self.stand("concepts/kern")})
        self.assertEqual(status, 422)
        self.assertIn("erst ablösen", daten["fehler"])
        self.assertTrue(self.datei("concepts/kern").exists())

    def test_belege_sperren_bis_zur_bestaetigung(self):
        self.abloesen("sources/q1")
        hash_ = self.stand("sources/q1")
        status, _, daten = self.post("/api/loeschen", {"id": "sources/q1", "hash": hash_})
        self.assertEqual(status, 409)
        self.assertEqual(daten["folgen"]["belege"][0]["seite"], "concepts/nutzt")
        self.assertTrue(any("source_missing" in z for z in daten["zeilen"]))
        for falsch in ([], ["concepts/anders"]):
            self.assertEqual(self.post("/api/loeschen", {"id": "sources/q1", "hash": hash_, "belege_bestaetigt": falsch})[0], 409)
        self.assertTrue(self.datei("sources/q1").exists())
        status, _, daten = self.post("/api/loeschen", {"id": "sources/q1", "hash": hash_, "belege_bestaetigt": ["concepts/nutzt"]})
        self.assertEqual(status, 200, daten)
        self.assertGreaterEqual(daten["befunde_gesamt"]["error"], 1)
        self.assertIn("ausdrücklich bestätigt", self.ereignisse()[-1]["detail"])

    def test_neuer_beleg_nach_der_rueckfrage_sperrt_wieder(self):
        self.abloesen("sources/q1")
        hash_ = self.stand("sources/q1")
        self.kern("new", "concept", "spaeter", "--title", "Später", "--description", "d", "--sources", "q1")
        status, _, daten = self.post("/api/loeschen", {"id": "sources/q1", "hash": hash_, "belege_bestaetigt": ["concepts/nutzt"]})
        self.assertEqual(status, 409)
        self.assertIn("concepts/spaeter", daten["fehler"])
        self.assertTrue(self.datei("sources/q1").exists())

    def test_rohquelle_nur_mit_eigener_bestaetigung(self):
        paket = self.root / "raw" / "q1"
        (paket / "original").mkdir(parents=True)
        (paket / "manifest.json").write_text('{"files": []}\n', encoding="utf-8")
        self.kern("sources", "concepts/nutzt", "--remove", "q1")
        self.abloesen("sources/q1")
        status, _, daten = self.post("/api/loeschen", {"id": "sources/q1", "hash": self.stand("sources/q1")})
        self.assertEqual(status, 200, daten)
        self.assertTrue(paket.is_dir())
        self.post("/api/wiederherstellen", {"eintrag": daten["eintrag"]})
        status, _, daten = self.post("/api/loeschen", {"id": "sources/q1", "hash": self.stand("sources/q1"), "rohquelle": True})
        self.assertEqual(status, 200, daten)
        self.assertFalse(paket.exists())
        self.assertEqual(self.eintraege()[0]["rohquelle"], "q1")
        self.post("/api/wiederherstellen", {"eintrag": daten["eintrag"]})
        self.assertTrue(paket.is_dir())

    def test_geaenderte_karte_409(self):
        alt = self.stand("concepts/kern")
        with self.datei("concepts/kern").open("a", encoding="utf-8") as f:
            f.write("Dazwischen geändert.\n")
        for pfad in ("/api/abloesen", "/api/loeschen"):
            self.assertEqual(self.post(pfad, {"id": "concepts/kern", "hash": alt})[0], 409)
        self.assertNotEqual(self.seite("concepts/kern").fm.get("status"), "deprecated")

    def test_falsche_typen(self):
        self.abloesen()
        hash_ = self.stand("concepts/kern")
        for mehr in ({"rohquelle": "ja"}, {"rohquelle": 1}, {"belege_bestaetigt": "concepts/nutzt"}, {"belege_bestaetigt": [1]}):
            self.assertEqual(self.post("/api/loeschen", {"id": "concepts/kern", "hash": hash_, **mehr})[0], 400, mehr)
        self.assertEqual(self.post("/api/abloesen", {"id": "concepts/nutzt", "hash": self.stand("concepts/nutzt"), "nachfolger": 3})[0], 400)
        self.assertTrue(self.datei("concepts/kern").exists())

    def test_schutzmassnahmen_wie_jeder_post(self):
        self.abloesen()
        ziel = {"id": "concepts/kern", "hash": self.stand("concepts/kern")}
        for pfad in ("/api/abloesen", "/api/loeschen", "/api/wiederherstellen"):
            with self.subTest(pfad=pfad):
                self.assertEqual(self.post(pfad, ziel, cookie=False)[0], 403)
                self.assertEqual(self.post(pfad, ziel, origin=None)[0], 403)
                self.assertEqual(self.post(pfad, ziel, origin="http://boese.example")[0], 403)
                self.assertEqual(self.post(pfad, ziel, host="boese.example")[0], 403)
                self.assertEqual(self.post(pfad, ziel, kopf={"X-OKFW": ""})[0], 403)
                self.assertEqual(self.post(pfad, ziel, typ="text/plain")[0], 415)
                self.assertEqual(self.post(f"/api{pfad}", ziel, fremd=True)[0], 404)
        for pfad in ("/api/folgen?id=concepts/kern", "/api/papierkorb"):
            self.assertEqual(self.anfrage("GET", pfad, cookie=False)[0], 403)
            self.assertEqual(self.anfrage("GET", pfad, host="boese.example")[0], 403)
            self.assertEqual(self.anfrage("GET", f"/api{pfad}", fremd=True)[0], 404)
        self.assertTrue(self.datei("concepts/kern").exists())
        self.assertEqual(self.eintraege(), [])

    def test_pfadausbruch(self):
        draussen = self.base / "draussen.md"
        draussen.write_text("---\nstatus: deprecated\n---\nText\n", encoding="utf-8")
        hash_ = okfw.sha256_of(draussen)
        for pid in ("../../draussen", "../draussen", str(draussen), "index", "log", ".papierkorb/x", "concepts/../../../draussen"):
            with self.subTest(pid=pid):
                for pfad in ("/api/abloesen", "/api/loeschen"):
                    self.assertEqual(self.post(pfad, {"id": pid, "hash": hash_, "belege_bestaetigt": []})[0], 404)
                self.assertEqual(self.anfrage("GET", f"/api/folgen?id={pid}")[0], 404)
        for pid in (None, 7, ["concepts/kern"], ""):
            self.assertEqual(self.post("/api/loeschen", {"id": pid, "hash": hash_})[0], 400)
        self.assertTrue(draussen.exists())

    def test_gefaelschter_eintrag(self):
        draussen = self.base / "ziel.md"
        for name in ("../../x", "..", "20261101T120000Z-x/../..", None, 7, ["x"], {}):
            self.assertEqual(self.post("/api/wiederherstellen", {"eintrag": name})[0], 422, name)
        ordner = self.korb() / "20261101T120000Z-falsch"
        ordner.mkdir(parents=True)
        (ordner / "karte.md").write_text("Fremd\n", encoding="utf-8")
        (ordner / "eintrag.json").write_text(json.dumps({"seite": "../../ziel", "by": "<img src=x>"}), encoding="utf-8")
        self.assertEqual(self.post("/api/wiederherstellen", {"eintrag": ordner.name})[0], 422)
        self.assertFalse(draussen.exists())
        self.assertEqual(self.eintraege()[0]["by"], "<img src=x>")   # kommt als Daten; serve.js setzt ihn als Text

    def test_fremdes_bundle(self):
        anderes = self.base / "anderes"
        anderes.mkdir()
        still(okfw.main, ["--root", str(anderes), "init"])
        still(okfw.main, ["--root", str(anderes), "new", "concept", "fremd", "--title", "Fremd", "--description", "d"])
        still(okfw.main, ["--root", str(anderes), "deprecate", "concepts/fremd", "--by", "human:tester"])
        karte = anderes / "wiki" / "concepts" / "fremd.md"
        hash_ = okfw.sha256_of(karte)
        for pid in ("concepts/fremd", "../../anderes/wiki/concepts/fremd", str(karte)):
            self.assertEqual(self.post("/api/loeschen", {"id": pid, "hash": hash_})[0], 404)
        still(okfw.main, ["--root", str(anderes), "delete", "concepts/fremd", "--yes", "--by", "human:tester"])
        fremder = json.loads(next((anderes / "wiki" / ".papierkorb").glob("2*/eintrag.json")).read_text(encoding="utf-8"))
        self.assertEqual(self.post("/api/wiederherstellen", {"eintrag": fremder["eintrag"]})[0], 422)
        self.assertFalse(karte.exists())

    def test_symlink_wird_nicht_geloescht(self):
        self.abloesen()
        link = self.root / "wiki" / "concepts" / "link.md"
        link.symlink_to("kern.md")
        status, _, daten = self.post("/api/loeschen", {"id": "concepts/link", "hash": self.stand("concepts/link")})
        self.assertEqual(status, 422)
        self.assertIn("symbolischer Link", daten["fehler"])
        self.assertTrue(self.datei("concepts/kern").exists())


class OhneKennung(ServeTest):
    kennung = None

    def test_nichts_ohne_kennung(self):
        self.kern("deprecate", "concepts/kern", "--by", "human:tester")
        status, _, daten = self.anfrage("GET", "/api/seite?id=concepts/kern")
        status, _, daten = self.post("/api/loeschen", {"id": "concepts/kern", "hash": daten["hash"]})
        self.assertEqual(status, 422)
        self.assertIn("Kennung", daten["fehler"])
        self.assertTrue(self.datei("concepts/kern").exists())
        hash_ = self.anfrage("GET", "/api/seite?id=sources/q1")[2]["hash"]
        self.assertEqual(self.post("/api/abloesen", {"id": "sources/q1", "hash": hash_})[0], 422)
        self.assertEqual(self.post("/api/wiederherstellen", {"eintrag": "20261101T120000Z-x"})[0], 422)
        self.assertNotEqual(self.seite("sources/q1").fm.get("status"), "deprecated")


@unittest.skipIf(shutil.which("node") is None, "node fehlt im PATH")
class KnoepfeInServeJs(unittest.TestCase):
    def knoepfe(self, **knoten) -> list[str]:
        m = re.search(r"^  function aktionen\(id\) \{\n.*?^  \}\n", JS.read_text(encoding="utf-8"), re.S | re.M)
        self.assertIsNotNone(m, "function aktionen fehlt in serve.js")
        rahmen = (f"const node = () => ({json.dumps(knoten)});\n"
                  "const h = (tag, attrs, ...kinder) => ({kinder});\n"
                  f"{m.group(0)}\n"
                  "console.log(JSON.stringify(aktionen('x').kinder.filter(Boolean).map(k => k.kinder[0])));")
        lauf = subprocess.run(["node", "-e", rahmen], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        return json.loads(lauf.stdout)

    def test_abgeloeste_karte_hat_keine_freigabe(self):
        self.assertEqual(self.knoepfe(release="none", deprecated=True), ["Bearbeiten", "Löschen"])
        self.assertEqual(self.knoepfe(release="none"), ["Bearbeiten", "Freigeben", "Ablösen"])
        self.assertEqual(self.knoepfe(release="valid"), ["Bearbeiten", "Ablösen"])


class RueckfragenInServeJs(unittest.TestCase):
    """Der Ablauf in loeschen(): Was fragt der Dialog, was geht an den Server? frage() und api() sind Attrappen."""

    def lauf(self, folgen: dict, antworten: list[bool]) -> dict:
        js = JS.read_text(encoding="utf-8")
        m = re.search(r"^  async function loeschen\(id\) \{\n.*?^  \}\n", js, re.S | re.M)
        self.assertIsNotNone(m, "function loeschen fehlt in serve.js")
        rahmen = (f"const fragen = [], posts = [], meldungen = [], antworten = {json.dumps(antworten)};\n"
                  "const frage = async (text, ja, nein) => { fragen.push({text, ja, nein}); return antworten.shift() ?? false; };\n"
                  "const api = async (pfad, body) => { posts.push({pfad, body}); return {ok: true, status: 200, data: {eintrag: 'e1', hinweise: []}}; };\n"
                  f"const folgenLaden = async id => ({json.dumps(folgen)});\n"
                  "const say = (text, art) => meldungen.push({text, art});\n"
                  "const fail = r => meldungen.push({text: r.data.fehler, art: 'err'});\n"
                  "const reloadTo = () => {};\n"
                  f"{m.group(0)}\n"
                  "loeschen('sources/q1').then(() => console.log(JSON.stringify({fragen, posts, meldungen})));")
        lauf = subprocess.run(["node", "-e", rahmen], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        return json.loads(lauf.stdout)

    @staticmethod
    def folgen(**mehr) -> dict:
        f = {"titel": "Quelle 1", "sperren": [], "belege": [], "rohquelle": None, "rohquelle_geteilt": [], **mehr}
        return {"hash": "h1", "folgen": f, "zeilen": ["Folge A", "Folge B"]}

    def test_die_rueckfrage_nennt_die_folgen(self):
        aus = self.lauf(self.folgen(), [True])
        self.assertIn("Folge A\nFolge B", aus["fragen"][0]["text"])
        self.assertIn("Papierkorb", aus["fragen"][0]["text"])
        self.assertEqual(aus["posts"], [{"pfad": "api/loeschen", "body": {"id": "sources/q1", "hash": "h1", "belege_bestaetigt": [], "rohquelle": False}}])

    def test_abbrechen_schickt_nichts(self):
        self.assertEqual(self.lauf(self.folgen(), [False])["posts"], [])

    def test_gesperrt_fragt_nicht(self):
        aus = self.lauf(self.folgen(sperren=["erst ablösen"]), [True])
        self.assertEqual((aus["fragen"], aus["posts"]), ([], []))
        self.assertEqual(aus["meldungen"][0]["art"], "err")

    def test_belege_brauchen_eine_eigene_antwort(self):
        f = self.folgen(belege=[{"seite": "concepts/a"}, {"seite": "concepts/a"}, {"seite": "concepts/b"}])
        aus = self.lauf(f, [True, False])
        self.assertEqual(aus["posts"], [])
        self.assertIn("source_missing", aus["fragen"][1]["text"])
        self.assertEqual(aus["fragen"][1]["ja"], "Belege brechen lassen")
        aus = self.lauf(f, [True, True])
        self.assertEqual(aus["posts"][0]["body"]["belege_bestaetigt"], ["concepts/a", "concepts/b"])

    def test_rohquelle_ist_eine_eigene_frage(self):
        f = self.folgen(rohquelle="raw/q1")
        aus = self.lauf(f, [True, False])
        self.assertEqual(aus["fragen"][1]["nein"], "Rohquelle liegen lassen")
        self.assertFalse(aus["posts"][0]["body"]["rohquelle"])
        self.assertTrue(self.lauf(f, [True, True])["posts"][0]["body"]["rohquelle"])

    def test_geteilte_rohquelle_wird_nicht_angeboten(self):
        aus = self.lauf(self.folgen(rohquelle="raw/q1", rohquelle_geteilt=["concepts/a"]), [True, True])
        self.assertEqual(len(aus["fragen"]), 1)
        self.assertFalse(aus["posts"][0]["body"]["rohquelle"])

    def test_knoepfe_und_reiter(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn('n.deprecated ? h("button", { class: "ghost okfw-danger"', js)
        self.assertIn('["papierkorb", "Papierkorb"]', js)
        self.assertNotIn(".innerHTML = e.", js)   # Einträge kommen aus dem Bundle, also nur als Text


if __name__ == "__main__":
    unittest.main()
