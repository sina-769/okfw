"""Der Lernpfad im Dashboard folgt dem Umschalter „Umfang“ ([scope]): Kacheln, Starten, Schrittzählung, weiter/zurück."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from .hilfe import WikiTest

DOM = Path(__file__).with_name("dashboard_dom.js")
ENUMS = 'access = ["fulltext", "abstract", "citation"]'
SCOPE = '[scope]\nfield = "umfang"\nlabel = "Umfang"\ndefault = "a"\n\n[labels.umfang]\na = "Teil A"\nb = "Teil B"\nc = "Teil C"\n\n[lint]'


@unittest.skipIf(shutil.which("node") is None, "node fehlt im PATH")
class LernpfadImUmfang(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        for i, umfang in enumerate("aaba", start=1):
            self.core("new", "concept", f"s{i}", "--title", f"Station {i}", "--description", "Test", "--sources", "q1",
                      "--tags", "einstieg", "--set", f"order={i}", "--set", f"umfang={umfang}")

    def mit_umfang(self):
        path = self.root / "wiki.toml"
        toml = path.read_text(encoding="utf-8")
        self.assertIn(ENUMS, toml)
        path.write_text(toml.replace(ENUMS, f'{ENUMS}\numfang = ["a", "b", "c"]', 1).replace("[lint]", SCOPE, 1), encoding="utf-8")

    def bedienen(self) -> dict:
        ziel = self.base / "dashboard.html"
        self.core("dashboard", "--out", str(ziel))
        lauf = subprocess.run(["node", str(DOM), str(ziel)], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        return json.loads(lauf.stdout)

    def test_voreinstellung_zeigt_nur_den_gewaehlten_umfang(self):
        self.mit_umfang()
        start = self.bedienen()["start"]
        self.assertEqual(start["kacheln"], [["1", "Station 1"], ["2", "Station 2"], ["3", "Station 4"]])
        self.assertIn("3 Seiten zum Einlesen", start["hinweis"])
        self.assertIn("(im gewählten Umfang, 4 insgesamt)", start["hinweis"])

    def test_starten_und_weiter_bleiben_im_umfang(self):
        self.mit_umfang()
        gang = self.bedienen()["stellungen"]["Teil A"]["gang"]
        self.assertEqual(gang, [["1Lernpfad · Schritt 1 von 3›", "Station 1"],
                                ["2Lernpfad · Schritt 2 von 3‹›", "Station 2"],
                                ["3Lernpfad · Schritt 3 von 3‹", "Station 4"], "Ende des Lernpfads"])

    def test_jede_stellung_zaehlt_neu(self):
        self.mit_umfang()
        stellungen = self.bedienen()["stellungen"]
        self.assertEqual(stellungen["Teil B"]["kacheln"], [["1", "Station 3"]])
        self.assertEqual(stellungen["Teil B"]["gang"], [["1Lernpfad · Schritt 1 von 1", "Station 3"], "Ende des Lernpfads"])
        self.assertIn("1 Seite zum Einlesen", stellungen["Teil B"]["hinweis"])
        alle = stellungen["Umfang: alle"]
        self.assertEqual([k[1] for k in alle["kacheln"]], ["Station 1", "Station 2", "Station 3", "Station 4"])
        self.assertEqual(len(alle["gang"]), 5)
        self.assertNotIn("insgesamt", alle["hinweis"])

    def test_umfang_ohne_stationen(self):
        self.mit_umfang()
        leer = self.bedienen()["stellungen"]["Teil C"]
        self.assertEqual((leer["kacheln"], leer["gang"], leer["start_sichtbar"]), ([], [], False))
        self.assertTrue(leer["abschnitt_sichtbar"])
        self.assertEqual(leer["hinweis"], "Keine Seite im gewählten Umfang (4 insgesamt)")

    def test_seite_ausserhalb_des_umfangs_zeigt_keinen_schritt(self):
        self.mit_umfang()
        schritte = self.bedienen()["stellungen"]["Teil A"]["schritt_je_seite"]
        self.assertIsNone(schritte["concepts/s3"])
        self.assertIn("Schritt 3 von 3", schritte["concepts/s4"])

    def test_ohne_umfang_alles_wie_bisher(self):
        start = self.bedienen()["start"]
        self.assertEqual([k[0] for k in start["kacheln"]], ["1", "2", "3", "4"])
        self.assertEqual(start["hinweis"], "4 Seiten zum Einlesen, in dieser Reihenfolge")
        self.assertEqual(start["gang"][3][0], "4Lernpfad · Schritt 4 von 4‹")
