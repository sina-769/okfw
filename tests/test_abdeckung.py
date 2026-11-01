"""Abdeckung: Was der Code kennt, nennt die Doku und berührt ein Test; was die Doku nennt, kennt der Code.

Die Liste kommt aus dem Code, mit derselben Funktion wie im Selbstreview (tests/selbstreview.py, `inventory`):
Unterbefehle und Optionen aus argparse, Lint-Codes aus SEVERITY und add(…), wiki.toml-Schlüssel aus
DEFAULT_CONFIG, TYPE_DEFAULTS und den Lesezugriffen im Kern und im Hook, Hooks aus okf_hook.py. „Genannt“ heißt:
SKILL.md, README.md oder docs/konfiguration.md trifft den Namen; „berührt“: eine Datei unter tests/ (außer dieser)
nennt ihn so, wie ein Test ihn benutzt. Textsuche, deterministisch, ohne Netz und ohne Modell.
"""

import json
import subprocess
import unittest

from . import selbstreview
from .hilfe import CORE, HEADER, SKILL, UV, clean_env

DOKU = ("skill", "readme", "konf")   # CHANGELOG zählt nicht als Doku eines Verhaltens

# Bewusste Ausnahmen, je Zeile mit Grund: (Art, Name, Prüfung) → Grund
AUSNAHMEN = {
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


if __name__ == "__main__":
    unittest.main()
