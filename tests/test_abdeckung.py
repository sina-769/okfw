"""Abdeckung: Was der Code kennt, nennt die Doku und berührt ein Test; was die Doku nennt, kennt der Code.

Die Liste kommt aus dem Code, mit derselben Funktion wie im Selbstreview (tests/selbstreview.py, `inventory`):
Unterbefehle und Optionen aus argparse, Lint-Codes aus SEVERITY und add(…), wiki.toml-Schlüssel aus
DEFAULT_CONFIG, TYPE_DEFAULTS und den Lesezugriffen im Kern und im Hook, Hooks aus okf_hook.py. „Genannt“ heißt:
SKILL.md, README.md, docs/rundgang.md, docs/entwicklung.md oder docs/konfiguration.md trifft den Namen; „berührt“: eine Datei unter tests/ (außer dieser)
nennt ihn so, wie ein Test ihn benutzt. Textsuche, deterministisch, ohne Netz und ohne Modell.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from . import selbstreview
from .hilfe import CORE, HEADER, PLUGIN, SKILL, UV, clean_env

DOKU = ("skill", "readme", "rundgang", "entwicklung", "konf")   # CHANGELOG zählt nicht als Doku eines Verhaltens

# Bewusste Ausnahmen, je Zeile mit Grund: (Art, Name, Prüfung) → Grund
AUSNAHMEN = {
    **{("regel", code, "gemeldet"): "kommt aus pfad_befunde über add(b[\"code\"], …), dieselbe Liste wie im Vortrag"
       for code in ("pfad_karte_abgeloest", "pfad_karte_entwurf", "pfad_karte_fehlt", "pfad_karte_veraltet",
                    "pfad_karte_widerspruch", "pfad_text_ohne_beleg")},
}


class Abdeckung(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = selbstreview.inventory()

    def pruefe(self, art: str, name: str, pruefung: str, ok: bool) -> None:
        if (art, name, pruefung) in AUSNAHMEN:
            return
        with self.subTest(art=art, name=name, pruefung=pruefung):
            self.assertTrue(ok, f"{art} {name}: {pruefung}")

    def doku(self, entry: dict) -> bool:
        return any(label in entry.get("doku", {}) for label in DOKU)

    def test_befehle(self):
        for c in self.inv["befehle"]:
            self.pruefe("befehl", c["name"], "doku", self.doku(c))
            self.pruefe("befehl", c["name"], "test", bool(c["tests"]))
            for o in c["options"]:
                if o["flags"][0].startswith("-"):
                    self.pruefe("option", f"{c['name']} {o['flags'][0]}", "doku", self.doku(o))

    def test_regeln(self):
        for r in self.inv["regeln"]:
            self.pruefe("regel", r["name"], "doku", self.doku(r))
            self.pruefe("regel", r["name"], "test", bool(r["tests"]))
            self.pruefe("regel", r["name"], "in SEVERITY", bool(r.get("severity")))
            self.pruefe("regel", r["name"], "gemeldet", bool(r["emitted"]))
            self.pruefe("regel", r["name"], "Schwere wie in konfiguration.md",
                        r.get("doku_severity", [r.get("severity")])[0] == r.get("severity"))

    def test_schluessel(self):
        for k in self.inv["schluessel"]:
            self.pruefe("schluessel", k["name"], "im Code", k["im_code"])
            self.pruefe("schluessel", k["name"], "doku", self.doku(k))
            self.pruefe("schluessel", k["name"], "test", bool(k["tests"]))

    @unittest.skipIf(UV is None, "uv fehlt im PATH")
    def test_config_unknown_kennt_dieselben_schluessel(self):
        """Lint config_unknown liest die bekannten Schlüssel aus dem Kern (known_config_keys); sie decken sich mit
        dem, was der Selbstreview aus DEFAULT_CONFIG und den Lesezugriffen findet."""
        script = (HEADER + f"import json, sys\nsys.path.insert(0, {str(SKILL)!r})\nimport okfw\n"
                  "print(json.dumps(sorted(okfw.known_config_keys())))\n")
        done = subprocess.run([UV, "run", "--quiet", "--script", "-"], input=script, capture_output=True, text=True,
                              env=clean_env(), cwd=CORE.parent, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), sorted(k["name"] for k in self.inv["schluessel"]))

    def test_hooks(self):
        for h in self.inv["hooks"]:
            self.pruefe("hook", h["name"], "doku", self.doku(h))
            self.pruefe("hook", h["name"], "test", bool(h["tests"]))
            self.pruefe("hook", h["name"], "in hooks.json", bool(h["events"].get("hooksjson")))
            self.pruefe("hook", h["name"], "in settings.example.json", bool(h["events"].get("settings")))
        for a in self.inv["abweichungen"]:
            if a["art"] == "hook":
                self.pruefe("hook", a["name"], f"{a['feld']} gleich in hooks.json und settings.example.json", False)

    def test_doku_nennt_nur_was_es_gibt(self):
        for d in self.inv["doku_ohne_code"]:
            self.pruefe(d["art"], d["name"], f"im Code (genannt in {d['datei']}, Z. {d['zeile']})", False)

    def test_ausnahmen_sind_noch_noetig(self):
        """Eine Ausnahme, die nicht mehr greift, wird gestrichen statt mitgeschleppt."""
        offen = set()
        for c in self.inv["befehle"]:
            offen |= {("befehl", c["name"], "doku")} if not self.doku(c) else set()
            offen |= {("befehl", c["name"], "test")} if not c["tests"] else set()
            offen |= {("option", f"{c['name']} {o['flags'][0]}", "doku") for o in c["options"]
                      if o["flags"][0].startswith("-") and not self.doku(o)}
        for art, key in (("regel", "regeln"), ("schluessel", "schluessel"), ("hook", "hooks")):
            for e in self.inv[key]:
                offen |= {(art, e["name"], "doku")} if not self.doku(e) else set()
                offen |= {(art, e["name"], "test")} if not e["tests"] else set()
        offen |= {(k[0], k[1], k[2]) for k in AUSNAHMEN if k[2] not in ("doku", "test")}
        self.assertEqual(sorted(set(AUSNAHMEN) - offen), [], "Ausnahmen ohne Grund mehr: streichen")

    def test_version_des_kerns_folgt_dem_plugin(self):
        """`--version`, `requires_core` und der User-Agent lesen VERSION; ein Patch ohne Änderung am Kern darf nachhinken."""
        kern = re.search(r'^VERSION = "(\d+\.\d+)\.', (PLUGIN / "skills/okfw/okfw_kern/basis.py").read_text(encoding="utf-8"), re.M).group(1)
        plugin = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(kern, plugin.rsplit(".", 1)[0])

    def test_claude_code_version_im_nachbau_steht_in_der_doku(self):
        """`ARG CLAUDE_CODE_VERSION` in tests/Dockerfile und die Zeile „Claude Code“ in docs/entwicklung.md nennen dieselbe Version."""
        docker = re.search(r"^ARG CLAUDE_CODE_VERSION=(\S+)\s*$", (PLUGIN / "tests/Dockerfile").read_text(encoding="utf-8"), re.M)
        doku = re.search(r"^\| \[Claude Code\]\([^)]*\) \| ([^|]+?) \|", (PLUGIN / "docs/entwicklung.md").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(docker, "tests/Dockerfile: Zeile ARG CLAUDE_CODE_VERSION=… fehlt")
        self.assertIsNotNone(doku, "docs/entwicklung.md: Tabellenzeile „Claude Code“ in „Voraussetzungen“ fehlt")
        self.assertEqual(doku.group(1), docker.group(1), "Claude-Code-Version: docs/entwicklung.md (Tabelle „Voraussetzungen“) und ARG CLAUDE_CODE_VERSION in tests/Dockerfile weichen ab")


class Anleitung(unittest.TestCase):
    """Die Anleitung für Agenten (.claude/CLAUDE.md), die Richtungsdatei und der eine Prüfeinstieg bleiben bei dem, was es gibt.

    Was CLAUDE.md in Backticks als Pfad oder Befehl nennt, muss existieren; der Prüfeinstieg ist ausführbar, die CI
    ruft nur ihn auf, und docs/richtung.md hat lückenlos nummerierte Klauseln, die auf bestehende Stellen verweisen."""

    # Pfade, die CLAUDE.md nennt und die hier bewusst nicht liegen: Pfad → Grund
    NICHT_IM_REPO = {
        ".claude/skills/": "nur im privaten Arbeitsrepo; export-ignore in .gitattributes",
        "wiki.toml": "Datei des Projekts, in dem okfw läuft",
    }
    PFAD = re.compile(r"^[\w.\-/]+$")
    ENDUNGEN = (".md", ".sh", ".py", ".json", ".yml", ".toml")

    @classmethod
    def setUpClass(cls):
        cls.claude = (PLUGIN / ".claude/CLAUDE.md").read_text(encoding="utf-8")

    def text(self, rel: str) -> str:
        return (PLUGIN / rel).read_text(encoding="utf-8")

    @staticmethod
    def ohne_codebloecke(text: str) -> str:
        return re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)

    def genannte_pfade(self, text: str) -> list[str]:
        spans = re.findall(r"`([^`\n]+)`", self.ohne_codebloecke(text))
        return sorted({s for s in spans if self.PFAD.match(s) and ("/" in s or s.endswith(self.ENDUNGEN))})

    def test_genannte_pfade_gibt_es(self):
        pfade = self.genannte_pfade(self.claude)
        self.assertIn("tests/pruefen.sh", pfade)
        for pfad in pfade:
            if pfad in self.NICHT_IM_REPO:
                continue
            with self.subTest(pfad=pfad):
                self.assertTrue((PLUGIN / pfad).exists(), f"CLAUDE.md nennt {pfad}, den es nicht gibt")

    def test_ausnahmen_der_anleitung_sind_noch_noetig(self):
        genannt = set(self.genannte_pfade(self.claude))
        self.assertEqual(sorted(set(self.NICHT_IM_REPO) - genannt), [], "Ausnahme ohne Nennung in CLAUDE.md: streichen")

    def test_befehle_in_bash_bloecken_gibt_es(self):
        bloecke = re.findall(r"^```bash\n(.*?)^```", self.claude, flags=re.S | re.M)
        self.assertTrue(bloecke, "CLAUDE.md nennt den Prüfbefehl in einem bash-Block")
        for zeile in "".join(bloecke).splitlines():
            erstes = zeile.split()[0] if zeile.split() else ""
            if erstes:
                with self.subTest(befehl=zeile):
                    self.assertTrue((PLUGIN / erstes).is_file() or shutil.which(erstes), f"{erstes} gibt es nicht")

    def test_anleitung_ist_kurz(self):
        self.assertLessEqual(len(self.claude.splitlines()), 90, "CLAUDE.md soll bei etwa 80 Zeilen bleiben")

    def test_anleitung_nennt_zweige_und_richtung(self):
        for wort in ("`main`", "Feature-Zweige", "docs/richtung.md", "Co-Authored-By", "Reviewed-by", "Tested-by", ".claude/skills/"):
            with self.subTest(wort=wort):
                self.assertIn(wort, self.claude)

    def test_pruefeinstieg_ist_ausfuehrbar_und_einzig(self):
        skript = PLUGIN / "tests" / "pruefen.sh"
        self.assertTrue(os.access(skript, os.X_OK), "tests/pruefen.sh ist nicht ausführbar")
        inhalt = skript.read_text(encoding="utf-8")
        for befehl in ("python -m unittest", "skills/web-zugriff/scripts/tests/test_pruefung.py"):
            self.assertIn(befehl, inhalt)
        schritte = re.findall(r"^\s*-?\s*run:\s*(.+)$", self.text(".github/workflows/tests.yml"), flags=re.M)
        self.assertEqual(schritte, ["tests/pruefen.sh"], "die CI ruft nur den Prüfeinstieg auf")
        self.assertIn("tests/pruefen.sh", self.text("docs/entwicklung.md"))

    def test_pruefeinstieg_scheitert_ohne_uv(self):
        """Ein fehlendes Werkzeug ist ein Fehler, kein stilles Grün."""
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash fehlt im PATH")
        with tempfile.TemporaryDirectory(prefix="okfw-leer-") as leer:
            done = subprocess.run([bash, str(PLUGIN / "tests" / "pruefen.sh")], env={"PATH": leer},
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("uv fehlt", done.stderr)

    def test_changelog_beginnt_mit_unveroeffentlicht_oder_einer_version(self):
        erste = next(z for z in self.text("CHANGELOG.md").splitlines() if z.startswith("## "))
        self.assertRegex(erste, r"^## (Unveröffentlicht|\d+\.\d+\.\d+ · \d+\.\d+\.\d{4})$")

    def test_pr_vorlage_hat_die_abschnitte(self):
        vorlage = self.text(".github/pull_request_template.md")
        for titel in ("Plan", "Änderungen", "Abweichungen vom Plan", "Prüfergebnis", "Beteiligte Modelle"):
            with self.subTest(titel=titel):
                self.assertRegex(vorlage, re.compile(rf"^## {titel}$", re.M))

    def test_beitragsweg_ist_verlinkt_und_seine_pfade_gibt_es(self):
        """CONTRIBUTING.md ist der eine Ort für den Beitragsweg; README und Entwicklung verweisen darauf, seine Pfade und Links stimmen."""
        beitrag = self.text("CONTRIBUTING.md")
        for rel in ("README.md", "docs/entwicklung.md"):
            with self.subTest(verweis=rel):
                self.assertIn("CONTRIBUTING.md", self.text(rel))
        for pfad in self.genannte_pfade(beitrag):
            with self.subTest(pfad=pfad):
                self.assertTrue((PLUGIN / pfad).exists(), f"CONTRIBUTING.md nennt {pfad}, den es nicht gibt")
        for ziel in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", beitrag):
            with self.subTest(link=ziel):
                self.assertTrue((PLUGIN / ziel).exists(), f"CONTRIBUTING.md verlinkt {ziel}, das es nicht gibt")
        for wort in ("tests/pruefen.sh", "Unveröffentlicht", "docs/richtung.md", "Co-Authored-By", "Reviewed-by", "Tested-by"):
            with self.subTest(wort=wort):
                self.assertIn(wort, beitrag)

    def test_richtung_klauseln_sind_lueckenlos_und_haben_eine_quelle(self):
        richtung = self.text("docs/richtung.md")
        klauseln = re.findall(r"^\*\*R(\d+)\. .+?\*\*\n(.*?)(?=^\*\*R\d+\. |\Z)", richtung, flags=re.S | re.M)
        self.assertEqual([int(n) for n, _ in klauseln], list(range(1, len(klauseln) + 1)), "R1, R2, … ohne Lücke")
        self.assertGreaterEqual(len(klauseln), 1)
        for nummer, rumpf in klauseln:
            with self.subTest(klausel=f"R{nummer}"):
                quelle = re.search(r"^Quelle: (.+)$", rumpf, flags=re.M)
                self.assertTrue(quelle, "Quelle: fehlt")
                pfade = re.findall(r"`([^`]+)`", quelle.group(1))
                self.assertTrue(pfade)
                for pfad in pfade:
                    self.assertTrue((PLUGIN / pfad).exists(), f"R{nummer} verweist auf {pfad}, den es nicht gibt")
        self.assertIn("docs/richtung.md", self.claude)


if __name__ == "__main__":
    unittest.main()
