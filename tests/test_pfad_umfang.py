"""Pfade im Dashboard folgen dem Umschalter „Umfang“ ([scope]): Karten außerhalb entfallen, eigener Text bleibt."""

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
class PfadImUmfang(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        for i, umfang in enumerate("aab", start=1):
            self.core("new", "concept", f"s{i}", "--title", f"Karte {i}", "--description", "Test", "--sources", "q1", "--set", f"umfang={umfang}")
        self.core("pfad", "neu", "Vortrag")
        datei = self.root / "wiki/pfade/vortrag.md"
        datei.write_text(datei.read_text(encoding="utf-8").split("---\n\n", 1)[0] + "---\n\n"
                         "# Eins\n\n- [K1](/concepts/s1.md)\n- [K3](/concepts/s3.md)\n\n"
                         "# Zwei\n\n- [K3](/concepts/s3.md)\n\n# Drei\n\n- [K3](/concepts/s3.md)\n\nEigener Text.\n", encoding="utf-8")

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

    def test_umfang_filtert_karten_und_stationen(self):
        self.mit_umfang()
        teil_a = self.bedienen()["stellungen"]["Teil A"][0]
        self.assertEqual(teil_a["stationen"], [["Eins", ["Karte 1"]], ["Drei", ["text"]]], "Station ohne Karte und Text entfällt, Text bleibt")
        self.assertEqual(teil_a["zeile"], "2 Stationen")

    def test_jede_stellung_zaehlt_neu(self):
        self.mit_umfang()
        stellungen = self.bedienen()["stellungen"]
        self.assertEqual(stellungen["Teil B"][0]["zeile"], "3 Stationen")
        self.assertEqual(stellungen["Umfang: alle"][0]["stationen"][0], ["Eins", ["Karte 1", "Karte 3"]])

    def test_hinweise_nur_zu_gezeigten_karten(self):
        self.mit_umfang()
        stellungen = self.bedienen()["stellungen"]
        self.assertEqual(stellungen["Teil A"][0]["befunde"].count("concepts/s3"), 0)
        self.assertIn("concepts/s1", stellungen["Teil A"][0]["befunde"])
        self.assertIn("concepts/s3", stellungen["Teil B"][0]["befunde"])

    def test_ohne_station_gesperrt(self):
        self.mit_umfang()
        datei = self.root / "wiki/pfade/vortrag.md"
        datei.write_text(datei.read_text(encoding="utf-8").replace("Eigener Text.\n", ""), encoding="utf-8")
        stellungen = self.bedienen()["stellungen"]
        self.assertEqual((stellungen["Teil C"][0]["zeile"], stellungen["Teil C"][0]["gesperrt"]), ("0 Stationen", True))
        self.assertFalse(stellungen["Teil A"][0]["gesperrt"])

    def test_ohne_umfang_alles_da(self):
        pfad = self.bedienen()["start"][0]
        self.assertEqual((pfad["titel"], pfad["zeile"]), ("Vortrag", "3 Stationen"))
