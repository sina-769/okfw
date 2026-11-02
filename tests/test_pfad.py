"""Pfade: Format, Lint, Sichtbarkeit, Standardpfad und Export. Die Oberfläche in serve prüft test_serve."""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from .hilfe import SKILL, WikiTest

sys.path.insert(0, str(SKILL))
try:
    import okfw
    from okfw_kern import pfad as pf
except ImportError as exc:   # PyYAML oder markdown fehlen
    raise unittest.SkipTest(f"okfw lässt sich nicht importieren: {exc}")

PROFIL = SKILL / "profiles" / "default.toml"
SICHTBARKEIT = """
[visibility]
field = "sichtbarkeit"
default = "oeffentlich"
hidden = ["intern"]
"""
KOERPER = """Vorweg ein Satz.

# Was im Filter passiert

Zwei Größen entscheiden.

- [Wasser](/concepts/wasser.md)
- [Mahlgrad](/concepts/mahlgrad.md)

Danach mehr Text.

---

Und ein zweites Feld.

> [!notiz]
> Hier die zwei Tassen zeigen.
> Zweite Zeile.

# Noch eine Station

- [Link nach außen](https://example.org/a.md)
- nur Text

```
# keine Überschrift im Code
```
"""


class Format(unittest.TestCase):
    def test_lesen(self):
        stationen, fussnoten = pf.stationen_lesen(KOERPER + "\n[^a]: Beleg\n    weiter\n")
        self.assertEqual([s["titel"] for s in stationen], ["", "Was im Filter passiert", "Noch eine Station"])
        self.assertTrue(stationen[0]["vorspann"])
        s = stationen[1]
        self.assertEqual([(e["art"], e["schluessel"]) for e in s["elemente"]],
                         [("text", "text-1"), ("karte", "/concepts/wasser.md"), ("karte", "/concepts/mahlgrad.md"),
                          ("text", "text-2"), ("text", "text-3")])
        self.assertEqual(s["notizen"], ["Hier die zwei Tassen zeigen.\nZweite Zeile."])
        self.assertEqual(fussnoten, ["[^a]: Beleg\n    weiter"])
        self.assertEqual(len(stationen[2]["elemente"]), 1, "externer Link, Text und Code bleiben ein Textfeld")
        self.assertIn("# keine Überschrift", stationen[2]["elemente"][0]["text"])

    def test_hin_und_zurueck(self):
        stationen, fussnoten = pf.stationen_lesen(KOERPER + "\n[^a]: Beleg\n")
        wieder = pf.stationen_lesen(pf.stationen_schreiben(stationen, fussnoten))
        self.assertEqual(pf.stationen_kern(wieder[0]), pf.stationen_kern(stationen))
        self.assertEqual(wieder[1], fussnoten)

    def test_gleiche_titel_bekommen_eigene_schluessel(self):
        stationen, _ = pf.stationen_lesen("# Eins\n\n# Eins\n\n# Ä ö!\n")
        self.assertEqual([s["slug"] for s in stationen], ["eins", "eins-2", "ä-ö"])

    def test_lage_und_meldungen(self):
        stationen, _ = pf.stationen_lesen("# A\n\n- [W](/w.md)\n- [X](/x.md)\n\nText\n")
        meldungen = pf.lage_anwenden(stationen, {"a": {"/w.md": {"pos": [1, 1, 4, 2], "zeigt": "ganz"},
                                                       "/x.md": {"pos": [10, 0, 5, 1], "zeigt": "alles"},
                                                       "/weg.md": {"pos": [0, 0, 1, 1]}},
                                                 "b": {}})
        w, x, t = stationen[0]["elemente"]
        self.assertEqual((w["pos"], w["zeigt"]), ([1, 1, 4, 2], "ganz"))
        self.assertTrue(x["auto"], "ungültige Lage: okfw ordnet selbst an")
        self.assertNotIn("zeigt", x)
        self.assertEqual(t["pos"][2], 12)
        self.assertEqual(len(meldungen), 4, meldungen)

    def test_uebereinander_wird_gemeldet(self):
        stationen, _ = pf.stationen_lesen("# A\n\n- [W](/w.md)\n- [X](/x.md)\n")
        meldungen = pf.lage_anwenden(stationen, {"a": {"/w.md": {"pos": [0, 0, 6, 5]}, "/x.md": {"pos": [5, 4, 6, 5]}}})
        self.assertIn("übereinander", meldungen[0])
        stationen, _ = pf.stationen_lesen("# A\n\n- [W](/w.md)\n- [X](/x.md)\n")
        self.assertEqual(pf.lage_anwenden(stationen, {"a": {"/w.md": {"pos": [0, 0, 6, 5]}, "/x.md": {"pos": [6, 0, 6, 5]}}}), [])

    def test_ohne_lage_ordnet_okfw_an(self):
        for n, breite in ((1, 12), (2, 6), (3, 4), (4, 6), (5, 4)):
            elemente = [{"art": "karte", "schluessel": str(i)} for i in range(n)] + [{"art": "text", "text": "x"}]
            zeilen = pf.anordnen(elemente)
            self.assertEqual(elemente[0]["pos"][2], breite)
            for e in elemente:
                self.assertTrue(pf.pos_gueltig(e["pos"]), e)
            self.assertGreaterEqual(zeilen, elemente[-1]["pos"][1] + elemente[-1]["pos"][3])

    def test_anordnen_folgt_der_reihenfolge_der_station(self):
        text, karte, zweiter = {"art": "text", "text": "x"}, {"art": "karte", "schluessel": "a"}, {"art": "text", "text": "y"}
        pf.anordnen([text, karte, zweiter])
        self.assertLess(text["pos"][1], karte["pos"][1])
        self.assertLess(karte["pos"][1], zweiter["pos"][1])

    def test_kurze_karten_bleiben_niedrig(self):
        kurz, absatz = {"art": "karte", "schluessel": "a"}, {"art": "karte", "schluessel": "b", "zeigt": "absatz"}
        pf.anordnen([kurz])
        pf.anordnen([absatz])
        self.assertEqual((kurz["pos"][3], absatz["pos"][3]), (3, 5))


class PfadTest(unittest.TestCase):
    extra_toml = ""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="okfw-pfad-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        shutil.copy(PROFIL, self.root / "wiki.toml")
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write(self.extra_toml)
        self.wiki = okfw.Wiki.load(root=self.root)
        extra = {"sichtbarkeit": "oeffentlich"} if self.extra_toml else {}
        self.wiki.new_page("concept", "wasser", title="Wasser", description="Grundlage.", extra=extra)
        self.wiki.new_page("concept", "mahlgrad", title="Mahlgrad", description="Bestimmt.", extra=extra)
        self.wiki.replace_body("concepts/wasser", "# Wasser\n\nErster Absatz über Wasser.\n\nZweiter Absatz.\n")

    def pfad(self, koerper: str, frontmatter: str = "", name: str = "kaffee") -> str:
        ordner = self.root / "wiki" / "pfade"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / f"{name}.md").write_text(f"---\ntype: pfad\ntitle: {name.title()}\nstatus: draft\n{frontmatter}---\n\n{koerper}", encoding="utf-8")
        self.wiki._pages = None
        return f"pfade/{name}"

    def codes(self, wiki=None) -> dict:
        out = {}
        for f in (wiki or self.wiki).lint():
            out.setdefault(f.code, []).append(f)
        return out

    def setze(self, karte: str, **werte) -> None:
        self.wiki._write_frontmatter(self.wiki.page_path(karte), werte)


class Kern(PfadTest):
    def test_jedes_wiki_kennt_den_typ(self):
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n")
        codes = self.codes()
        self.assertNotIn("type_unknown", codes)
        self.assertNotIn("type_folder_mismatch", codes)
        pid = self.wiki.pfad_neu("Über Größen")
        self.assertEqual(pid.name, "ueber-groessen.md")
        self.assertEqual(self.wiki.pfad_neu("Über Größen").name, "ueber-groessen-2.md")

    def test_pfad_macht_keine_karte_verbunden_und_ist_kein_knoten(self):
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n")
        verwaist = [f.path for f in self.codes().get("orphan", [])]
        self.assertTrue(any("wasser" in p for p in verwaist), verwaist)
        self.assertFalse(any("pfade/" in p for p in verwaist), "ein Pfad ist keine verwaiste Karte")
        daten = self.wiki.dashboard_data()
        self.assertNotIn("pfade/kaffee", [n["id"] for n in daten["nodes"]])
        self.assertEqual([n for n in daten["nodes"] if n["id"] == "concepts/wasser"][0]["in"], 0)
        self.assertEqual(daten["edges"], [])
        self.assertNotIn("pfad", [t["name"] for t in daten["types"]])
        self.assertEqual(daten["pfade"][0]["id"], "pfade/kaffee")

    def test_beleg_regeln(self):
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n- [Weg](/concepts/weg.md)\n\n42 Prozent sagen das.\n\n"
                  "# B\n\n1. nur eine Liste\n\n- [Mahlgrad](/concepts/mahlgrad.md)\n\nMit Beleg 42.[^q]\n\n[^q]: Quelle\n")
        codes = self.codes()
        self.assertIn("pfad_karte_entwurf", codes)
        self.assertEqual(codes["pfad_karte_fehlt"][0].severity, "error")
        self.assertIn("broken_link", codes)
        self.assertEqual(len(codes["pfad_text_ohne_beleg"]), 1)
        self.assertEqual(codes["pfad_text_ohne_beleg"][0].severity, "info")
        self.wiki.verify("concepts/wasser", "claude-code/test")
        self.wiki.verify("concepts/wasser", "human:test", status="stable")
        self.wiki.replace_body("concepts/wasser", "# Wasser\n\nGeändert.\n")
        self.assertIn("pfad_karte_veraltet", self.codes())
        self.wiki.replace_body("concepts/mahlgrad", "⚠️ **Widerspruch:** zu [Wasser](/concepts/wasser.md)\n")
        self.setze("concepts/wasser", status="deprecated", superseded_by="/concepts/mahlgrad.md")
        codes = self.codes()
        self.assertIn("pfad_karte_widerspruch", codes)
        self.assertIn("pfad_karte_abgeloest", codes)
        befunde = self.wiki.pfad_befunde(self.wiki.pfad_seite("kaffee"))
        self.assertTrue(all(b["stopp"] for b in befunde if b["code"] in ("pfad_karte_abgeloest", "pfad_karte_fehlt")))
        stand = self.wiki.karte_stand(self.wiki.parse(self.wiki.page_path("concepts/wasser")))
        self.assertEqual(stand[0]["text"], "Abgelöst durch Mahlgrad")

    def test_lage_verwaist_wird_gemeldet(self):
        self.pfad("# Neu benannt\n\n- [Wasser](/concepts/wasser.md)\n", "lage:\n  alt-benannt:\n    /concepts/wasser.md: {pos: [0, 0, 6, 5]}\n")
        self.assertIn("umbenannt", self.codes()["pfad_lage"][0].message)

    def test_freigabe_deckt_text_nicht_die_lage(self):
        pid = self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n\nEin Text.\n")
        self.wiki.verify(pid, "claude-code/test")
        self.wiki.verify(pid, "human:test", status="stable")
        p = self.wiki.pfad_seite("kaffee")
        stationen = self.wiki.pfad_stationen(p)[0]
        stationen[0]["elemente"][0].update(pos=[0, 0, 6, 5], auto=False)
        self.assertTrue(self.wiki.pfad_speichern(pid, stationen))
        p = self.wiki.pfad_seite("kaffee")
        self.assertEqual(p.fm["lage"], {"a": {"/concepts/wasser.md": {"pos": [0, 0, 6, 5]}}})
        self.assertTrue(self.wiki.release_state(p)["valid"], "Verschieben lässt die Freigabe stehen")
        self.assertEqual(p.fm["status"], "stable")
        stationen = self.wiki.pfad_stationen(p)[0]
        stationen[0]["elemente"][1]["text"] = "Anderer Text."
        self.assertTrue(self.wiki.pfad_speichern(pid, stationen))
        p = self.wiki.pfad_seite("kaffee")
        self.assertFalse(self.wiki.release_state(p)["valid"], "Text ändern lässt sie verfallen")
        self.assertEqual(p.fm["status"], "draft")
        self.assertEqual(p.fm["lage"], {"a": {"/concepts/wasser.md": {"pos": [0, 0, 6, 5]}}})
        details = [e.get("detail", "") for e in self.wiki.read_events() if e.get("page") == pid]
        self.assertTrue(any("Anordnung" in d for d in details) and any("Stationen" in d for d in details), details)

    def test_speichern_ohne_aenderung_schreibt_nichts(self):
        pid = self.pfad("# A\n\n- [W](/concepts/wasser.md)\n\nText.\n")
        vorher = self.wiki.page_path(pid).read_text(encoding="utf-8")
        p = self.wiki.pfad_seite("kaffee")
        self.assertFalse(self.wiki.pfad_speichern(pid, self.wiki.pfad_stationen(p)[0]))
        self.assertEqual(self.wiki.page_path(pid).read_text(encoding="utf-8"), vorher)

    def test_text_darf_den_aufbau_nicht_aendern(self):
        pid = self.pfad("# A\n\nText.\n")
        p = self.wiki.pfad_seite("kaffee")
        for boese in ("Vor\n\n# Neue Station", "Vor\n\n- [W](/concepts/wasser.md)", "> [!notiz]\n> still", "Vor\n\n---\n\nnach"):
            stationen = self.wiki.pfad_stationen(p)[0]
            stationen[0]["elemente"][0]["text"] = boese
            with self.assertRaisesRegex(okfw.WikiError, "Aufbau"):
                self.wiki.pfad_speichern(pid, stationen)

    def test_schlechte_eingaben(self):
        pid = self.pfad("# A\n\nText.\n")
        for stationen in ("nein", [{"titel": "", "elemente": []}], [{"titel": "x"}],
                          [{"titel": "x", "elemente": [{"art": "karte", "id": "concepts/gibtsnicht"}]}],
                          [{"titel": "x", "elemente": [{"art": "karte", "id": "concepts/wasser", "zeigt": "alles"}]}],
                          [{"titel": "x", "elemente": [{"art": "karte", "id": "concepts/wasser", "pos": [0, 0, 99, 1]}]}],
                          [{"titel": "x", "elemente": [{"art": "bild"}]}],
                          [{"titel": "x", "elemente": [], "notizen": [1]}],
                          [{"titel": "x", "elemente": [{"art": "karte", "id": pid}]}]):
            with self.assertRaises(okfw.WikiError, msg=stationen):
                self.wiki.pfad_speichern(pid, stationen)
        with self.assertRaises(okfw.WikiError):
            self.wiki.pfad_seite("gibtsnicht")

    def test_alle_pfade_sind_gleich(self):
        self.setze("concepts/mahlgrad", tags=["einstieg"], order=1)
        self.pfad("# A\n\n- [W](/concepts/wasser.md)\n", "einstieg: true\n")
        self.assertEqual([p.id for p in self.wiki.learning_path()], ["concepts/mahlgrad"], "Zuerst lesen bleibt beim Schlagwort")
        self.assertEqual([p["titel"] for p in self.wiki.dashboard_data()["pfade"]], ["Zuerst lesen", "Kaffee"])
        self.assertNotIn("path", self.wiki.dashboard_data())


class Bearbeiten(PfadTest):
    def stand(self):
        self.wiki._pages = None
        return self.wiki.pfad_stationen(self.wiki.pfad_seite("kaffee"))[0]

    def eingabe(self):
        return [{"titel": s["titel"], "vorspann": s["vorspann"], "notizen": s["notizen"],
                 "elemente": [e if e["art"] == "karte" else {"art": "text", "text": e["text"]} for e in s["elemente"]]} for s in self.stand()]

    def koerper(self):
        return (self.root / "wiki/pfade/kaffee.md").read_text(encoding="utf-8")

    def test_schreiben_behaelt_kartenlinks(self):
        self.pfad("# A\n\n- [Eigener Name](../concepts/wasser.md#abschnitt)\n")
        eingabe = self.eingabe()
        eingabe[0]["notizen"] = ["Neu"]
        self.assertTrue(self.wiki.pfad_speichern("kaffee", eingabe))
        self.assertIn("- [Eigener Name](../concepts/wasser.md#abschnitt)", self.koerper())

    def test_nur_ein_anderer_link_ist_keine_aenderung(self):
        self.pfad("# A\n\n- [W](../concepts/wasser.md)\n")
        self.assertFalse(self.wiki.pfad_speichern("kaffee", self.eingabe()))

    def test_vorspann_bleibt_vorn(self):
        self.pfad("Vorweg.\n\n# A\n\n- [W](/concepts/wasser.md)\n")
        self.assertFalse(self.wiki.pfad_speichern("kaffee", [*reversed(self.eingabe())]), "die Folge bleibt dieselbe")
        self.assertEqual([s["vorspann"] for s in self.stand()], [True, False])

    def test_zwei_vorspanne_und_leerer(self):
        self.pfad("Vorweg.\n\n# A\n\nText.\n")
        leer = {"titel": "", "vorspann": True, "notizen": [], "elemente": []}
        with self.assertRaisesRegex(okfw.WikiError, "höchstens einen Vorspann"):
            self.wiki.pfad_speichern("kaffee", [*self.eingabe(), leer])
        self.assertTrue(self.wiki.pfad_speichern("kaffee", [leer, self.eingabe()[1]]))
        self.assertEqual([s["titel"] for s in self.stand()], ["A"])

    def test_fussnote_im_textfeld(self):
        self.pfad("# A\n\nAlt[^1].\n\n[^1]: Erste\n")
        eingabe = self.eingabe()
        eingabe[0]["elemente"][0]["text"] = "Neu[^1] und[^9].\n\n[^9]: Zweite\n    Fortsetzung"
        self.assertTrue(self.wiki.pfad_speichern("kaffee", eingabe))
        self.assertEqual(self.stand()[0]["elemente"][0]["text"], "Neu[^1] und[^9].")
        self.assertTrue(self.koerper().endswith("[^1]: Erste\n[^9]: Zweite\n    Fortsetzung\n"))
        eingabe = self.eingabe()
        eingabe[0]["elemente"][0]["text"] += "\n\n[^1]: Ersetzt"
        self.wiki.pfad_speichern("kaffee", eingabe)
        self.assertEqual(self.koerper().count("[^1]:"), 1)
        self.assertIn("[^1]: Ersetzt", self.koerper())

    def test_fussnoten_je_textfeld(self):
        self.pfad("# A\n\nOhne Verweis.\n\n---\n\nMit[^1].\n\n---\n\nAuch[^1].\n\n[^1]: Beleg\n")
        reader = self.wiki.reader_html()
        felder = list(reader["pfade/kaffee"]["texte"]["a"].values())
        self.assertNotIn("Beleg", felder[0], "ein Textfeld ohne Verweis trägt keine Fußnote")
        self.assertIn("Beleg", felder[1])
        ids = re.findall(r'\bid="([^"]+)"', felder[1] + felder[2])
        self.assertEqual(len(ids), len(set(ids)), ids)
        for ziel in re.findall(r'href="#(fn[^"]+)"', felder[1]):
            self.assertIn(f'id="{ziel}"', felder[1])

    def test_falsche_typen(self):
        self.pfad("# A\n\nText.\n")
        for stationen in ([{"titel": ["x"], "elemente": []}], [{"titel": 3, "elemente": []}], [{"titel": "x", "elemente": [{"art": "karte", "id": ["a"]}]}]):
            with self.assertRaises(okfw.WikiError):
                self.wiki.pfad_speichern("kaffee", stationen)

    def test_enges_raster(self):
        elemente = [{"art": "karte"} for _ in range(3)]
        pf.anordnen(elemente, raster=2)
        self.assertTrue(all(pf.pos_gueltig(e["pos"], 2) for e in elemente), elemente)


class Befehle(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "a", "--title", "Karte a", "--description", "Test", "--sources", "q1", "--tags", "einstieg", "--set", "order=1")

    def test_befehle(self):
        self.assertIn("Karte a", self.core("pfad", "uebernehmen", "--dry-run").stdout)
        self.assertFalse((self.root / "wiki/pfade/zuerst-lesen.md").exists())
        self.core("pfad", "uebernehmen")
        self.assertTrue((self.root / "wiki/pfade/zuerst-lesen.md").exists())
        self.core("pfad", "neu", "Mein Vortrag")
        self.assertEqual(json.loads(self.core("pfad", "pruefen", "mein-vortrag", "--json", ok=None).stdout), [])
        ziel = self.base / "aus.html"
        self.core("pfad", "export", "zuerst-lesen", "--out", str(ziel))
        self.assertIn("Karte a", ziel.read_text(encoding="utf-8"))

    def test_pfad_ohne_station(self):
        self.core("pfad", "neu", "Leer")
        datei = self.root / "wiki/pfade/leer.md"
        datei.write_text(datei.read_text(encoding="utf-8").split("---\n\n", 1)[0] + "---\n", encoding="utf-8")
        codes =[f["code"] for f in json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]]
        self.assertIn("pfad_ohne_station", codes)


class AlteWikis(PfadTest):
    def setUp(self):
        super().setUp()
        self.setze("concepts/mahlgrad", tags=["einstieg"], order="1")
        self.setze("concepts/wasser", tags=["einstieg"], order=2)

    def test_standardpfad_ohne_datei(self):
        vorher = sorted(p.name for p in (self.root / "wiki").rglob("*.md"))
        daten = self.wiki.dashboard_data()["pfade"]
        self.assertEqual(len(daten), 1)
        self.assertTrue(daten[0]["standard"])
        self.assertEqual([s["titel"] for s in daten[0]["stationen"]], ["Mahlgrad", "Wasser"])
        self.assertEqual(sorted(p.name for p in (self.root / "wiki").rglob("*.md")), vorher, "nichts geschrieben")
        self.assertIsNone(self.wiki.pfad_seite("zuerst-lesen"))
        with self.assertRaises(okfw.WikiError):
            self.wiki.pfad_speichern("zuerst-lesen", [])

    def test_uebernehmen(self):
        bericht = self.wiki.pfad_uebernehmen(dry_run=True)
        self.assertFalse(bericht["geschrieben"])
        self.assertFalse((self.root / "wiki/pfade/zuerst-lesen.md").exists())
        self.assertEqual([s["id"] for s in bericht["schritte"]], ["concepts/mahlgrad", "concepts/wasser"])
        self.assertIn("order '1' ist keine Zahl", bericht["auffaellig"][0])
        vorher = [p.id for p in self.wiki.learning_path()]
        karten = {i: self.wiki.page_path(i).read_text(encoding="utf-8") for i in vorher}
        vorschau = self.wiki.dashboard_data()["pfade"][0]["stationen"][0]["elemente"][0]
        self.assertTrue(self.wiki.pfad_uebernehmen()["geschrieben"])
        for i, text in karten.items():
            self.assertEqual(self.wiki.page_path(i).read_text(encoding="utf-8"), text, "die Karten bleiben unverändert")
        element = self.wiki.dashboard_data()["pfade"][0]["stationen"][0]["elemente"][0]
        self.assertEqual((element["zeigt"], element["pos"]), (vorschau["zeigt"], vorschau["pos"]), "sieht aus wie vorher")
        with self.assertRaisesRegex(okfw.WikiError, "gibt es schon"):
            self.wiki.pfad_uebernehmen(dry_run=True)
        self.assertEqual([p.id for p in self.wiki.learning_path()], vorher, "Zuerst lesen bleibt gleich")
        self.assertEqual([p["id"] for p in self.wiki.dashboard_data()["pfade"]], ["pfade/zuerst-lesen"], "kein zweiter daneben")
        with self.assertRaisesRegex(okfw.WikiError, "gibt es schon"):
            self.wiki.pfad_uebernehmen()

    def test_export_des_standardpfads(self):
        ziel, _ = self.wiki.pfad_export("zuerst-lesen", out=self.root / "aus.html")
        self.assertIn("Mahlgrad", ziel.read_text(encoding="utf-8"))


class Export(PfadTest):
    def daten(self, ziel: Path) -> dict:
        text = ziel.read_text(encoding="utf-8")
        return json.loads(re.search(r'id="okf-pfad">(.*?)</script>', text, re.S).group(1))

    def test_eigenes_design_hat_das_letzte_wort(self):
        (self.root / "design.css").write_text(".pf-karte { color: #123456; }\n", encoding="utf-8")
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n")
        text = self.wiki.pfad_export("kaffee", out=self.root / "aus.html")[0].read_text(encoding="utf-8")
        marke = "/* okfw Pfad: Lesen und Vortrag"
        self.assertEqual(text.count(marke), 1)
        self.assertLess(text.index(marke), text.index("#123456"))

    def test_nur_seine_karten_und_keine_notizen(self):
        self.wiki.new_page("concept", "fremd", title="Fremdkarte", description="Nicht im Pfad.")
        self.wiki.replace_body("concepts/wasser", "Siehe [Fremdkarte](/concepts/fremd.md) und [Mahlgrad](/concepts/mahlgrad.md).\n")
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n- [Mahlgrad](/concepts/mahlgrad.md)\n\nEigener Text.\n\n> [!notiz]\n> GEHEIME NOTIZ\n")
        ziel, _ = self.wiki.pfad_export("kaffee", out=self.root / "aus.html")
        text = ziel.read_text(encoding="utf-8")
        self.assertNotIn("GEHEIME NOTIZ", text)
        self.assertIn("Content-Security-Policy", text)
        self.assertNotRegex(text, r"<script(?! nonce| type=\"application/json\")", "jedes Skript trägt den Nonce")
        d = self.daten(ziel)
        self.assertEqual(sorted(d["karten"]), ["concepts/mahlgrad", "concepts/wasser"])
        self.assertNotIn("concepts/fremd", text, "kein Link auf eine Karte, die nicht mitgeht")
        self.assertIn("Fremdkarte", d["karten"]["concepts/wasser"]["html"])
        self.assertIn('data-page="concepts/mahlgrad"', d["karten"]["concepts/wasser"]["html"])
        self.assertIn("Eigener Text", d["pfad"]["stationen"][0]["elemente"][2]["html"])
        self.assertEqual(d["pfad"]["befunde"], [], "Befunde bleiben bei dir")
        self.assertTrue(d["stand"])

    def test_notizen_nur_auf_wunsch(self):
        self.pfad("# A\n\n> [!notiz]\n> MEINE NOTIZ\n")
        ziel, _ = self.wiki.pfad_export("kaffee", out=self.root / "aus.html", notizen=True)
        self.assertEqual(self.daten(ziel)["pfad"]["stationen"][0]["notizen"], ["MEINE NOTIZ"])
        self.assertNotIn("MEINE NOTIZ", json.dumps(self.wiki.dashboard_data()["pfade"], ensure_ascii=False))

    def test_datei_laeuft_mit_eigenem_ziel(self):
        self.pfad("# A\n\nText.\n")
        ziel, _ = self.wiki.pfad_export("kaffee")
        self.assertEqual(ziel, self.root / "build" / "pfad-kaffee.html")
        with self.assertRaisesRegex(okfw.WikiError, "fehlt"):
            self.wiki.pfad_export("gibtsnicht")

    def test_kein_platzhalter_bleibt_stehen(self):
        self.pfad("# A\n\nText.\n")
        text = self.wiki.pfad_export("kaffee", out=self.root / "aus.html")[0].read_text(encoding="utf-8")
        for marke in ("__POLICY__", "__TITLE__", "__DATA__", "/*__"):
            self.assertNotIn(marke, text)


class Oeffentlich(PfadTest):
    def test_ohne_interne_stoppt_nicht_am_zustand_der_internen(self):
        self.setze("concepts/wasser", status="deprecated", sichtbarkeit="intern")
        self.setze("concepts/mahlgrad", sichtbarkeit="oeffentlich")
        self.pfad("# A\n\n- [W](/concepts/wasser.md)\n- [M](/concepts/mahlgrad.md)\n", "sichtbarkeit: oeffentlich\n")
        with self.assertRaisesRegex(okfw.WikiError, "Abgelöst"):
            self.wiki.pfad_export("kaffee", out=self.root / "a.html", public=True)
        _, warnungen = self.wiki.pfad_export("kaffee", out=self.root / "a.html", public=True, ohne_interne=True)
        self.assertIn("weggelassen, weil intern: concepts/wasser", warnungen)
        self.assertFalse([w for w in warnungen if "Wasser" in w], "nichts über den Zustand der weggelassenen Karte")

    extra_toml = SICHTBARKEIT

    def setUp(self):
        super().setUp()
        self.wiki.new_page("concept", "geheim", title="Geheime Karte", description="Nur intern.", extra={"sichtbarkeit": "intern"})

    def test_notizen_nie_oeffentlich(self):
        self.pfad("# A\n", "sichtbarkeit: oeffentlich\n")
        with self.assertRaisesRegex(okfw.WikiError, "Sprechernotizen"):
            self.wiki.pfad_export("kaffee", public=True, notizen=True)

    def test_pfad_ohne_eigenen_wert(self):
        pid = self.pfad("# A\n\nEigener Text.\n")
        self.assertEqual(self.codes()["pfad_sichtbarkeit_fehlt"][0].severity, "error")
        self.assertIn(pid, self.wiki.hidden_ids())
        with self.assertRaisesRegex(okfw.WikiError, "keinen eigenen Wert"):
            self.wiki.pfad_export("kaffee", public=True)
        self.assertEqual(self.wiki.public_view().dashboard_data()["pfade"], [])

    def test_interner_pfad_nicht_oeffentlich(self):
        self.pfad("# A\n\nText.\n", "sichtbarkeit: intern\n")
        with self.assertRaisesRegex(okfw.WikiError, "selbst intern"):
            self.wiki.pfad_export("kaffee", public=True)

    def test_interne_karte_stoppt_und_nennt_die_station(self):
        pid = self.pfad("# Erste\n\n- [Geheime Karte](/concepts/geheim.md)\n- [Wasser](/concepts/wasser.md)\n\nText.\n", "sichtbarkeit: oeffentlich\n")
        self.assertIn("visibility_leak", self.codes())
        with self.assertRaisesRegex(okfw.WikiError, "Erste.*concepts/geheim"):
            self.wiki.pfad_export("kaffee", public=True, out=self.root / "aus.html")
        self.assertFalse((self.root / "aus.html").exists())
        ziel, warnungen = self.wiki.pfad_export("kaffee", public=True, out=self.root / "aus.html", ohne_interne=True)
        text = ziel.read_text(encoding="utf-8")
        self.assertNotIn("Geheime Karte", text, "weder Titel noch Linktext")
        self.assertNotIn("concepts/geheim", text)
        self.assertIn("Wasser", text)
        self.assertTrue(any("concepts/geheim" in w for w in warnungen))
        public = json.dumps(self.wiki.public_view().dashboard_data()["pfade"], ensure_ascii=False)
        self.assertNotIn("Geheime Karte", public)
        self.assertIn(pid, public)

    def test_abgeloest_und_fehlend_stoppen(self):
        self.pfad("# A\n\n- [Weg](/concepts/weg.md)\n", "sichtbarkeit: oeffentlich\n")
        with self.assertRaisesRegex(okfw.WikiError, "weg.md fehlt"):
            self.wiki.pfad_export("kaffee", public=True, out=self.root / "aus.html")
        self.pfad("# A\n\n- [Wasser](/concepts/wasser.md)\n", "sichtbarkeit: oeffentlich\n")
        self.setze("concepts/wasser", status="deprecated")
        with self.assertRaisesRegex(okfw.WikiError, "Abgelöst"):
            self.wiki.pfad_export("kaffee", public=True, out=self.root / "aus.html")
        self.assertFalse((self.root / "aus.html").exists())
        self.wiki.pfad_export("kaffee", out=self.root / "aus.html")   # nicht öffentlich: nur Warnung

    def test_neuer_pfad_ist_intern(self):
        pfad = self.wiki.pfad_neu("Neuer Pfad")
        self.assertIn("sichtbarkeit: intern", pfad.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
