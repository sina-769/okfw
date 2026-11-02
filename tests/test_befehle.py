"""Gruppe 2: Befehle, die der Skill verlangt – generated nachziehen, sources, Rechte bei capture, Lizenz aus dem Manifest."""

import json
import subprocess

from .hilfe import CORE, UV, WikiTest, clean_env

ALT = {"by": "human:frueher", "at": "2020-01-01T00:00:00Z"}
ACTOR = "claude-code/unknown"   # [wiki] actor im Profil default


class GeneratedNachziehen(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.py(f"wiki._write_frontmatter(wiki.page_path('sources/q1'), {{'generated': {ALT!r}}})")

    def assert_nachgezogen(self):
        gen = self.fm("sources/q1")["generated"]
        self.assertEqual(gen["by"], ACTOR)
        self.assertGreater(gen["at"], ALT["at"])

    def test_body(self):
        self.core("body", "sources/q1", "-", stdin="# Quelle q1\n\nNeuer Inhalt.\n")
        self.assert_nachgezogen()
        self.assertIn("Neuer Inhalt.", self.text("sources/q1"))

    def test_lizenz(self):
        self.core("lizenz", "sources/q1", "--name", "MIT", "--via", "originaldokument")
        self.assert_nachgezogen()
        self.assertEqual(self.fm("sources/q1")["license"]["name"], "MIT")

    def test_fundweg(self):
        self.core("fundweg", "sources/q1", "--art", "person")
        self.assert_nachgezogen()

    def test_body_laesst_uebriges_frontmatter_stehen(self):
        vorher = self.text("sources/q1").split("generated:")[0]
        self.core("body", "sources/q1", "-", stdin="Text\n")
        self.assertEqual(self.text("sources/q1").split("generated:")[0], vorher)

    def test_verify_zieht_generated_nicht_nach(self):
        self.core("verify", "sources/q1", "--by", "human:pruefer")
        self.assertEqual(self.fm("sources/q1")["generated"], ALT)


class QuellenNachtragen(WikiTest):
    def setUp(self):
        super().setUp()
        for slug in ("q1", "q2", "q3"):
            self.quelle(slug)
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test", "--sources", "q1")

    def ids(self):
        return [e["id"] for e in self.fm("concepts/k1")["sources"]]

    def test_add(self):
        done = self.core("sources", "concepts/k1", "--add", "q2,q3")
        self.assertEqual(self.ids(), ["q1", "q2", "q3"])
        self.assertIn("[^q2]: [Quelle q2](/sources/q2.md)", done.stdout)
        self.assertEqual(self.fm("concepts/k1")["sources"][1], {"id": "q2", "resource": "/sources/q2.md"})
        events = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("+q2, +q3", json.loads(events[-1])["detail"])

    def test_add_doppelt_aendert_nichts(self):
        vorher = self.text("concepts/k1")
        done = self.core("sources", "concepts/k1", "--add", "q1")
        self.assertIn("steht schon", done.stderr)
        self.assertEqual(self.text("concepts/k1"), vorher)

    def test_add_eigene_id_fuer_fundstelle(self):
        self.core("sources", "concepts/k1", "--add", "q1-z120=q1")
        self.assertEqual(self.fm("concepts/k1")["sources"][-1], {"id": "q1-z120", "resource": "/sources/q1.md"})

    def test_add_unbekannte_quelle(self):
        self.core("sources", "concepts/k1", "--add", "fehlt", ok=False)
        self.assertEqual(self.ids(), ["q1"])

    def test_remove(self):
        self.core("sources", "concepts/k1", "--add", "q2")
        self.core("sources", "concepts/k1", "--remove", "q2")
        self.assertEqual(self.ids(), ["q1"])

    def test_remove_verweigert_wenn_zitiert(self):
        self.core("body", "concepts/k1", "-", stdin="Aussage.[^q1]\n\n[^q1]: [Quelle q1](/sources/q1.md)\n")
        done = self.core("sources", "concepts/k1", "--remove", "q1", ok=False)
        self.assertIn("[^q1]", done.stderr)
        self.assertEqual(self.ids(), ["q1"])


class RechteBeiCapture(WikiTest):
    def capture(self, slug, *opts, ok=True):
        datei = self.datei(f"{slug}.txt")
        return self.core("capture", slug, "--url", "https://example.org/x", "--file", str(datei), *opts, ok=ok)

    def test_namensnennung_ohne_warnung(self):
        done = self.capture("q-mit", "--license", "MIT")
        self.assertNotIn("Achtung", done.stderr)

    def test_ohne_lizenz_erst_fragen(self):
        done = self.capture("q-ohne")
        self.assertIn("erst fragen", done.stderr)
        self.assertTrue((self.root / "raw" / "q-ohne" / "manifest.json").exists())

    def test_vorbehalten_mit_auflage(self):
        done = self.capture("q-vorb", "--license", "All rights reserved")
        self.assertIn("Auflage", done.stderr)

    def test_nicht_erlaubt_bricht_ab(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[rights.vorbehalten]\nvolltext_ablegen = "nein: Verlag untersagt die Ablage"\n')
        done = self.capture("q-nein", "--license", "All rights reserved", ok=False)
        self.assertIn("nicht erlaubt", done.stderr)
        self.assertIn("Verlag untersagt", done.stderr)
        self.assertFalse((self.root / "raw" / "q-nein").exists())


class LizenzAusManifest(WikiTest):
    def test_new_source_uebernimmt_lizenz(self):
        datei = self.datei("spec.md")
        self.core("capture", "spec", "--url", "https://example.org/spec", "--file", str(datei),
                  "--license", "Apache-2.0", "--license-url", "https://www.apache.org/licenses/LICENSE-2.0")
        self.core("new", "source", "spec", "--title", "Spec", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org/spec", "--set", "raw_packet=raw/spec")
        lic = self.fm("sources/spec")["license"]
        self.assertEqual(lic["name"], "Apache-2.0")
        self.assertEqual(lic["url"], "https://www.apache.org/licenses/LICENSE-2.0")
        self.assertEqual(lic["via"], "capture")
        self.assertRegex(lic["checked"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertNotIn("license_unknown", self.lint_codes("sources/spec"))

    def test_set_license_geht_vor(self):
        datei = self.datei("spec.md")
        self.core("capture", "spec", "--url", "https://example.org/spec", "--file", str(datei), "--license", "MIT")
        self.core("new", "source", "spec", "--title", "Spec", "--description", "D", "--access", "fulltext",
                  "--set", "raw_packet=raw/spec", "--set", "license={name: CC BY 4.0, url: null}")
        self.assertEqual(self.fm("sources/spec")["license"]["name"], "CC BY 4.0")

    def test_ohne_paket_bleibt_unknown(self):
        self.quelle("q1")
        self.assertEqual(self.fm("sources/q1")["license"], {"name": "unknown", "url": None})


class Projektordner(WikiTest):
    """Projektordner ist der Ordner der wiki.toml; --root und --config dürfen nicht auseinanderlaufen."""

    def okfw(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([UV, "run", "--quiet", "--script", str(CORE), *args], capture_output=True, text=True,
                              env=clean_env(), cwd=self.base, timeout=120)

    def test_config_und_root_auf_verschiedene_ordner(self):
        anderer = self.base / "anderer"
        anderer.mkdir()
        config = str(self.root / "wiki.toml")
        done = self.okfw("--config", config, "--root", str(anderer), "status")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("nur --root DIR", done.stderr)
        self.assertEqual(self.okfw("--config", config, "--root", str(self.root), "status").returncode, 0)
        allein = self.okfw("--config", config, "status")       # aus einem anderen Ordner: Projektordner bleibt
        self.assertEqual(allein.returncode, 0, allein.stderr)
        self.assertIn("Bundle: wiki ", allein.stdout)

    def test_init_mit_config(self):
        neu = self.base / "neu"
        neu.mkdir()
        done = self.okfw("--config", str(neu / "wiki.toml"), "init")
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("--root DIR statt --config", done.stderr)
        self.assertFalse((neu / "wiki.toml").exists())
