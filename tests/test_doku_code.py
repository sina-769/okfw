"""Gruppe 4: Stellen, an denen Doku und Code auseinanderlagen und der Code nachgezogen wurde."""

import json
import shutil
import unittest
from pathlib import Path

from .hilfe import SKILL, WikiTest

SICHTBARKEIT = """
[visibility]
field = "sichtbarkeit"
default = "oeffentlich"
hidden = ["intern"]

[export.csl]
path = "build/references.json"
"""


class TestordnerOhneBerichte(unittest.TestCase):
    """Auch git-ignorierte Dateien: Berichte hängen am Release, Probedateien gehören in ein Wegwerf-Wiki."""

    def md_in(self, ordner: Path) -> list[str]:
        return sorted(p.relative_to(ordner).as_posix() for p in ordner.rglob("*.md"))

    def test_nur_die_anleitungen_liegen_hier(self):
        self.assertEqual(self.md_in(Path(__file__).resolve().parent), ["angreifer.md", "selbstreview.md"])

    def test_bei_den_tests_von_web_zugriff_liegt_keine(self):
        self.assertEqual(self.md_in(Path(__file__).resolve().parents[1] / "skills" / "web-zugriff" / "scripts" / "tests"), [])


class GuardWieProtectedPaths(WikiTest):
    def setUp(self):
        super().setUp()
        toml = self.root / "wiki.toml"
        text = toml.read_text(encoding="utf-8").replace("protected = []", 'protected = ["notizen/**"]')
        toml.write_text(text + SICHTBARKEIT, encoding="utf-8")

    def test_jeder_geschuetzte_pfad_wird_abgelehnt(self):
        patterns = self.py("result = okfw.protected_paths(wiki)")
        self.assertIn("build/wiki-dashboard-oeffentlich.html", patterns)
        for pattern in patterns:
            sample = self.root / pattern.replace("**", "x/y")
            done = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                                       "tool_input": {"file_path": str(sample), "content": "x"}})
            self.assertIn('"permissionDecision": "deny"', done.stdout, pattern)

    def test_eigenes_public_path(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[dashboard]\npublic_path = "site/wiki.html"\n')
        self.assertIn("site/wiki.html", self.py("result = okfw.protected_paths(wiki)"))
        done = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(self.root),
                                   "tool_input": {"file_path": str(self.root / "site" / "wiki.html")}})
        self.assertIn('"permissionDecision": "deny"', done.stdout)


class OeffentlichOhneAblageort(WikiTest):
    def test_stored_nur_in_normaler_ausgabe(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write(SICHTBARKEIT)
        datei = self.datei("a.md")
        self.core("capture", "q1", "--url", "https://example.org", "--file", str(datei), "--license", "MIT")
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", "raw_packet=raw/q1")
        stored = self.py("""
nodes = lambda w: {n['id']: n['stored'] for n in w.dashboard_data()['nodes']}
result = [nodes(wiki)['sources/q1'], nodes(wiki.public_view())['sources/q1']]""")
        self.assertEqual(stored[0], [{"path": "raw/q1", "tracked": False}])
        self.assertEqual(stored[1], [])


class ApaAlsStandard(WikiTest):
    ITEM = {"id": "x", "type": "book", "title": "Ein Titel", "author": [{"family": "Muster", "given": "Max"}],
            "issued": {"date-parts": [[2024]]}}

    def test_stil_ohne_angabe_ist_apa(self):
        self.assertEqual(self.py("result = str(wiki.csl_style())"), str(SKILL / "templates" / "apa.csl"))

    def test_eigener_stil_geht_vor(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[export.csl]\nstyle = "stil/eigen.csl"\n')
        self.assertEqual(self.py("result = str(wiki.csl_style())"), str(self.root / "stil" / "eigen.csl"))

    @unittest.skipIf(shutil.which("pandoc") is None, "pandoc fehlt")
    def test_render_apa_formatiert_nach_apa(self):
        out = self.py(f"result = wiki.render_apa([{json.dumps(self.ITEM)}])")
        self.assertIn("Muster, M. (2024)", out)


class KurzzitatNachApa(WikiTest):
    """Im Text steht der Kurzbeleg: Abkürzung, Rechtsakt-Nummer oder die ersten Wörter, nie der ganze Titel."""

    TITEL = {
        "Gesetz zum kontrollierten Umgang mit Cannabis und zur Änderung weiterer Vorschriften (CanG)": "CanG",
        "Einkommensteuergesetz (EStG), § 33b Pauschbeträge für Menschen mit Behinderungen": "§ 33b EStG",
        "Verordnung über den Betrieb von Apotheken (Apothekenbetriebsordnung – ApBetrO), §§ 2a, 3, 17, 20":
            "§§ 2a, 3, 17, 20 ApBetrO",
        "Verordnung (EU) 2017/745 über Medizinprodukte (Medizinprodukteverordnung, MDR), konsolidierte Fassung": "MDR",
        "Verordnung (EU) 2024/1689 zur Festlegung harmonisierter Vorschriften für künstliche Intelligenz":
            "Verordnung (EU) 2024/1689",
        "§ 20 Apothekenbetriebsordnung: Information und Beratung": "§ 20 Apothekenbetriebsordnung",
        "Registernummern in PubMed und die Brücke zu CTIS (Probe)": "Registernummern in PubMed",
        "AIB 2025-1 / MDCG 2025-6: Interplay between the MDR & IVDR and the Artificial Intelligence Act (AIA)":
            "AIB 2025-1 / MDCG 2025-6",
        "Kurzer Titel": "",
    }
    NAMEN = {
        "Bundesarbeitsgemeinschaft der Inklusionsämter und Hilfen der Sozialen Entschädigung (BIH)": "BIH",
        "ABDA – Bundesvereinigung Deutscher Apothekerverbände": "ABDA",
        "Institute of Medicine (US) Committee on Quality of Health Care in America":
            "Institute of Medicine (US) Committee on Quality of Health Care in America",
        "European Medicines Agency": "European Medicines Agency",
    }

    def test_kurztitel(self):
        out = self.py(f"result = {{t: okfw.kurztitel(t) for t in {list(self.TITEL)!r}}}")
        self.assertEqual(out, self.TITEL)

    def test_kurzname(self):
        out = self.py(f"result = {{n: okfw.kurzname(n) for n in {list(self.NAMEN)!r}}}")
        self.assertEqual(out, self.NAMEN)

    @unittest.skipIf(shutil.which("pandoc") is None, "pandoc fehlt")
    def test_klammerbeleg_kurz_literaturangabe_lang(self):
        self.core("new", "source", "cang", "--title", "Gesetz zum kontrollierten Umgang mit Cannabis (CanG)",
                  "--description", "D", "--access", "citation", "--set", "year=2024")
        self.core("new", "source", "bih", "--title", "Fachlexikon", "--description", "D", "--access", "citation",
                  "--set", "year=2026", "--set", "authors=Bundesarbeitsgemeinschaft der Inklusionsämter (BIH)")
        out = self.py("result = {k: v['cite'] + ' | ' + v['entry'] for k, v in wiki.apa_catalog().items()}")
        self.assertRegex(out["cang"], r"^<em>CanG</em>, 2024 \| .*Gesetz zum kontrollierten Umgang mit Cannabis")
        self.assertRegex(out["bih"], r"^BIH, 2026 \| Bundesarbeitsgemeinschaft der Inklusionsämter \(BIH\)")


class ZeilenAlsFundstelle(WikiTest):
    def test_z_landet_im_beleg(self):
        self.quelle("q1", extra=("--set", "date=2026-10-25"))
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "D", "--sources", "q1")
        self.core("sources", "concepts/k1", "--add", "q1-z120=q1")
        self.core("body", "concepts/k1", "-", stdin="Erste Aussage.[^q1-z120]\n\n"
                  "[^q1-z120]: [Quelle q1](/sources/q1.md), Z. 120–135\n")
        html = self.py("result = wiki.reader_html()['concepts/k1']['html']")
        self.assertRegex(html, r"class=\"cite-ref\".*?, 2026, Z\. 120–135</a>")
        refs = self.py("result = [r['key'] for r in wiki.reader_html()['concepts/k1']['refs']]")
        self.assertEqual(refs, ["q1"])


class ProfilDokumentation(WikiTest):
    profile = "dokumentation"

    def test_specification_und_dataset_erlaubt(self):
        for slug, fmt in (("spec", "specification"), ("daten", "dataset")):
            datei = self.datei(f"{slug}.md")
            self.core("capture", slug, "--url", "https://example.org", "--file", str(datei), "--license", "MIT")
            self.core("new", "source", slug, "--title", slug, "--description", "D", "--access", "fulltext",
                      "--resource", "https://example.org", "--set", f"raw_packet=raw/{slug}", "--set", f"format={fmt}")
            self.assertNotIn("enum_invalid", self.lint_codes(f"sources/{slug}"))
        self.core("new", "source", "falsch", "--title", "F", "--description", "D", "--access", "citation",
                  "--set", "format=video")
        self.assertIn("enum_invalid", self.lint_codes("sources/falsch"))


class SelbstreviewNullDreiNull(WikiTest):
    """Befunde aus dem Selbstreview 0.3.0, im Code behoben."""

    def toml_dazu(self, text: str) -> None:
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + text, encoding="utf-8")

    def test_rebuild_quiet_meldet_kaputte_wiki_toml(self):
        self.toml_dazu("\n[wiki\n")
        done = self.core("rebuild", "--quiet", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("nicht lesbar", done.stderr)

    def test_falsche_schwere_bricht_mit_meldung_ab(self):
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("[lint.severity]", '[lint.severity]\norphan = "warning"'),
                        encoding="utf-8")
        done = self.core("lint", "--summary", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("orphan = 'warning'", done.stderr)

    def test_requires_core_ohne_zahl_ist_bedienfehler(self):
        toml = self.root / "wiki.toml"
        text = toml.read_text(encoding="utf-8")
        toml.write_text(text.replace('requires_core = "0.1"', 'requires_core = "v0.3"'), encoding="utf-8")
        done = self.core("status", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("MAJOR.MINOR", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_literaturverzeichnis_ohne_csl_nennt_den_grund(self):
        self.toml_dazu('\n[export.bibliography]\npath = "build/literatur.md"\n')
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        msgs = [f["message"] for f in out["findings"] if f["code"] == "projection_unavailable"]
        self.assertEqual(len(msgs), 1)
        # vorher hieß es mit pandoc und ohne Quellen „pandoc fehlt?“
        self.assertIn("pandoc fehlt" if shutil.which("pandoc") is None else "keine Quellenkarte", msgs[0])

    def test_date_zaehlt_auch_mit_authors(self):
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "citation",
                  "--resource", "https://example.org/q1", "--set", "authors=[Muster, M.]",
                  "--set", "date=2026-10-25", "--set", "license={name: MIT, url: null}")
        self.assertNotIn("source_undated", self.lint_codes("sources/q1"))
        self.core("new", "source", "q2", "--title", "Q2", "--description", "D", "--access", "citation",
                  "--resource", "https://example.org/q2", "--set", "authors=[Muster, M.]")
        self.assertIn("source_undated", self.lint_codes("sources/q2"))

    def test_lizenz_class_ohne_namen(self):
        self.quelle("q1")
        toml_text = self.text("sources/q1").replace("license: {name: unknown, url: null}\n", "")
        (self.root / "wiki" / "sources" / "q1.md").write_text(toml_text, encoding="utf-8")
        done = self.core("lizenz", "sources/q1", "--class", "by")
        self.assertIn("ohne Namen", done.stdout)

    def test_zitatverfolgung_nennt_die_option_von_new(self):
        done = self.core("new", "source", "q3", "--title", "Q3", "--description", "D", "--access", "citation",
                         "--fundweg", "zitatverfolgung", ok=False)
        self.assertIn("--fundweg-von", done.stderr)
