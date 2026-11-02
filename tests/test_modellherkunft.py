"""generated.modelle: Modellherkunft bleibt nach jedem Schreibweg maschinenlesbar, getrennt von der menschlichen Freigabe."""

import json
import re

from .hilfe import WikiTest

SEITE = "concepts/k1"


class Schreibwege(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "k1", "--title", "K1", "--description", "Test", "--sources", "q1")

    def akteur(self, akteur: str):
        pfad = self.root / "wiki.toml"
        text = pfad.read_text(encoding="utf-8")
        pfad.write_text(re.sub(r'^actor = "[^"]*"', f'actor = "{akteur}"', text, count=1, flags=re.M), encoding="utf-8")

    def body(self, text: str, *opts, page: str = SEITE):
        return self.core("body", page, str(self.datei("neu.md", text)), *opts)

    def modelle(self, page: str = SEITE) -> list:
        return self.fm(page)["generated"].get("modelle", [])

    def test_sitzung_mit_modell_steht_mit_pruefwert(self):
        self.akteur("claude-code/m1")
        self.body("Text vom Modell.[^q1]\n")
        eintrag = self.modelle()[-1]
        self.assertEqual({k: eintrag[k] for k in ("harness", "modell", "art", "by")},
                         {"harness": "claude-code", "modell": "m1", "art": "text", "by": "claude-code/m1"})
        self.assertRegex(eintrag["body_sha256"], r"^[0-9a-f]{64}$")

    def test_ereignis_nennt_das_modell(self):
        self.akteur("claude-code/m1")
        self.body("Text.[^q1]\n")
        self.assertEqual(self.py("result = [e for e in wiki.read_events() if e['kind'] == 'update'][-1]['modelle']"),
                         [{"harness": "claude-code", "modell": "m1"}])

    def test_mensch_ueberschreibt_den_vermerk_nicht(self):
        self.akteur("claude-code/m1")
        self.body("Text vom Modell.[^q1]\n")
        vorher = [e for e in self.modelle() if e["modell"] == "m1"]
        self.akteur("human:erika")
        self.body("Text von Hand.[^q1]\n")
        fm = self.fm(SEITE)
        self.assertEqual(fm["generated"]["by"], "human:erika")
        self.assertEqual([e for e in fm["generated"]["modelle"] if e["modell"] == "m1"], vorher)

    def test_freigabe_bleibt_getrennt(self):
        self.akteur("claude-code/m1")
        self.body("Text vom Modell.[^q1]\n")
        vorher = self.fm(SEITE)["generated"]
        self.core("verify", SEITE, "--by", "human:erika", "--status", "stable", "--force")
        fm = self.fm(SEITE)
        self.assertEqual(fm["generated"], vorher)   # generated.by bleibt das Modell, die Freigabe steht in verified
        self.assertEqual(fm["status"], "stable")
        self.assertEqual(fm["verified"][-1]["by"], "human:erika")

    def test_metadaten_nehmen_den_frueheren_akteur_mit(self):
        self.quelle("q2")
        self.akteur("claude-code/m1")
        self.core("sources", SEITE, "--add", "q2")
        self.akteur("human:erika")
        self.core("sources", SEITE, "--remove", "q2")
        eintrag = self.modelle()[-1]
        self.assertEqual((eintrag["modell"], eintrag["art"], eintrag["by"]), ("m1", "akteur", "claude-code/m1"))

    def test_alte_seite_verliert_generated_by_nicht(self):
        """Seite aus der Zeit vor generated.modelle: Der Akteur wird beim ersten Überschreiben zum Eintrag."""
        pfad = self.root / f"wiki/{SEITE}.md"
        text = pfad.read_text(encoding="utf-8")
        pfad.write_text(text.replace("by: claude-code/unknown", "by: claude-code/alt", 1), encoding="utf-8")
        self.akteur("human:erika")
        self.body("Von Hand.[^q1]\n")
        self.assertEqual([(e["modell"], e["art"]) for e in self.modelle()], [("alt", "akteur")])

    def test_mensch_und_prozess_sind_kein_modell(self):
        for akteur in ("human:erika", "process:skript"):
            self.akteur(akteur)
            self.body(f"Text von {akteur}.[^q1]\n")
        self.assertFalse([e for e in self.modelle() if e["by"] in ("human:erika", "process:skript")])

    def test_rolle_mit_modell(self):
        self.akteur("human:erika")
        self.body("Entwurf.[^q1]\n", "--rolle", "lesen", "--modell", "m2")
        eintrag = self.modelle()[-1]
        self.assertEqual({k: eintrag[k] for k in ("modell", "rolle", "art", "by")},
                         {"modell": "m2", "rolle": "lesen", "art": "entwurf", "by": "human:erika"})

    def test_rolle_ohne_angabe_nimmt_das_modell_der_rolle(self):
        self.akteur("human:erika")
        self.body("Entwurf.[^q1]\n", "--rolle", "lesen")
        eintrag = self.modelle()[-1]
        self.assertEqual(eintrag["rolle"], "lesen")
        self.assertTrue(eintrag["modell"])

    def test_rolle_beim_nachziehen(self):
        self.akteur("human:erika")
        self.core("body", SEITE, "--nachziehen", "--rolle", "lesen", "--modell", "m2")
        self.assertEqual(self.modelle()[-1]["art"], "entwurf")

    def test_gleicher_text_mit_rolle_traegt_nach(self):
        self.akteur("human:erika")
        self.body("Entwurf.[^q1]\n")
        self.assertEqual([e for e in self.modelle() if e.get("rolle")], [])
        self.body("Entwurf.[^q1]\n", "--rolle", "lesen", "--modell", "m2")
        self.assertEqual([e["modell"] for e in self.modelle() if e.get("rolle")], ["m2"])

    def test_gleicher_text_ohne_rolle_aendert_nichts(self):
        self.body("Entwurf.[^q1]\n")
        vorher = self.text(SEITE)
        self.assertIn("unverändert", self.body("Entwurf.[^q1]\n").stdout)
        self.assertEqual(self.text(SEITE), vorher)

    def test_bekanntes_modell_ersetzt_unbekannt_derselben_rolle(self):
        self.akteur("human:erika")
        self.body("Entwurf.[^q1]\n", "--rolle", "lesen", "--modell", "unknown")
        self.assertIn("modell_unbekannt", self.lint_codes(SEITE))
        self.body("Entwurf.[^q1]\n", "--rolle", "lesen", "--modell", "m2")
        self.assertEqual([e["modell"] for e in self.modelle() if e.get("rolle")], ["m2"])
        self.assertNotIn("modell_unbekannt", self.lint_codes(SEITE))

    def test_unbekannte_rolle_und_modell_ohne_rolle_scheitern(self):
        self.assertIn("rolle", self.core("body", SEITE, str(self.datei("x.md", "x")), "--rolle", "gibtsnicht", ok=False).stderr.lower())
        self.assertIn("--rolle", self.core("body", SEITE, str(self.datei("x.md", "x")), "--modell", "m", ok=False).stderr)
        self.assertEqual(self.modelle(), [])

    def test_standardakteur_faellt_erst_mit_modelltext_auf(self):
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/unknown")
        self.assertNotIn("modell_unbekannt", self.lint_codes(SEITE))
        self.body("Text aus einer Sitzung ohne bekanntes Modell.[^q1]\n")
        self.assertIn("modell_unbekannt", self.lint_codes(SEITE))

    def test_unbekannt_verschwindet_wenn_das_modell_bekannt_wird(self):
        self.akteur("claude-code/m1")
        self.body("Text.[^q1]\n")
        self.assertEqual([e["modell"] for e in self.modelle()], ["m1"])
        self.assertNotIn("modell_unbekannt", self.lint_codes(SEITE))

    def test_unbekannter_akteur_bleibt_vor_einem_menschen_stehen_ohne_warnung(self):
        self.akteur("human:erika")
        self.body("Von Hand.[^q1]\n")
        self.assertEqual([(e["modell"], e["art"]) for e in self.modelle()], [("unknown", "akteur")])
        self.assertNotIn("modell_unbekannt", self.lint_codes(SEITE))

    def test_gleiches_modell_bleibt_ein_eintrag(self):
        self.akteur("claude-code/m1")
        self.body("Eins.[^q1]\n")
        self.body("Zwei.[^q1]\n")
        self.assertEqual(len([e for e in self.modelle() if e["modell"] == "m1"]), 1)

    def test_von_hand_entfernt_faellt_auf(self):
        self.akteur("claude-code/m1")
        self.body("Text.[^q1]\n")
        pfad = self.root / f"wiki/{SEITE}.md"
        pfad.write_text(re.sub(r"(?ms)^  modelle:\n.*?(?=^---)", "", pfad.read_text(encoding="utf-8")), encoding="utf-8")
        self.assertEqual(self.modelle(), [])
        self.assertIn("modell_herkunft_fehlt", self.lint_codes(SEITE))

    def test_falsche_form_faellt_auf(self):
        self.body("Text.[^q1]\n")
        pfad = self.root / f"wiki/{SEITE}.md"
        pfad.write_text(re.sub(r"art: \w+", "art: erfunden", pfad.read_text(encoding="utf-8")), encoding="utf-8")
        self.assertIn("modelle_invalid", self.lint_codes(SEITE))


class Ausgabe(WikiTest):
    """Dashboard: maschinenlesbar in den Daten, auch öffentlich, ohne Kennung einer Person."""

    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "k1", "--title", "K1", "--description", "Test", "--sources", "q1")
        self.core("body", SEITE, str(self.datei("e.md", "Entwurf.[^q1]\n")), "--rolle", "lesen", "--modell", "m2")

    def daten(self, code: str = "wiki.dashboard_data()") -> dict:
        return self.py(f"result = {code}")

    def knoten(self, daten: dict) -> dict:
        return next(n for n in daten["nodes"] if n["id"] == SEITE)

    def test_daten_und_vorlage(self):
        daten = self.daten()
        eintrag = next(e for e in self.knoten(daten)["modelle"] if e["modell"] == "m2")
        self.assertEqual((eintrag["rolle"], eintrag["art"], eintrag["gleich"]), ("lesen", "entwurf", True))
        self.assertIn("art", eintrag)
        self.assertIn("entwurf", daten["modell_arten"])

    def test_geaenderter_text_ist_erkennbar(self):
        self.core("body", SEITE, str(self.datei("h.md", "Von Hand geändert.[^q1]\n")))
        eintrag = next(e for e in self.knoten(self.daten())["modelle"] if e["modell"] == "m2")
        self.assertFalse(eintrag["gleich"])

    def test_html_traegt_vermerk(self):
        self.core("dashboard")
        html = next(self.root.rglob("*dashboard*.html")).read_text(encoding="utf-8")
        self.assertIn('"modell":"m2"', html.replace('": "', '":"'))
        self.assertIn("r-modell", html)

    def test_oeffentlich_ohne_person(self):
        pfad = self.root / "wiki.toml"
        toml = pfad.read_text(encoding="utf-8")
        pfad.write_text(toml.replace('access = ["fulltext", "abstract", "citation"]',
                                     'access = ["fulltext", "abstract", "citation"]\nsicht = ["offen", "intern"]', 1)
                        .replace("[lint]", '[visibility]\nfield = "sicht"\nhidden = ["intern"]\ndefault = "offen"\n\n[lint]', 1),
                        encoding="utf-8")
        # Der letzte Akteur ist eine Person, deren Kennung nicht in die öffentliche Ausgabe gehört
        toml = pfad.read_text(encoding="utf-8")
        pfad.write_text(re.sub(r'^actor = "[^"]*"', 'actor = "human:erika"', toml, count=1, flags=re.M), encoding="utf-8")
        self.core("body", SEITE, str(self.datei("v.md", "Vorschlag.[^q1]\n")), "--rolle", "lesen", "--modell", "m3")
        eintraege = self.knoten(self.daten("wiki.public_view().dashboard_data()"))["modelle"]
        self.assertIn("m3", [e["modell"] for e in eintraege])
        self.assertFalse([e for e in eintraege if "by" in e])


class Hook(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "k1", "--title", "K1", "--description", "Test", "--sources", "q1")
        self.pfad = self.root / f"wiki/{SEITE}.md"

    def transkript(self, name: str, *zeilen: dict) -> str:
        pfad = self.base / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
        return str(pfad)

    def edit(self, **extra):
        alt = self.pfad.read_text(encoding="utf-8")
        neu = alt.rstrip("\n") + "\n\nNeuer Absatz.\n"
        self.pfad.write_text(neu, encoding="utf-8")
        return self.hook("post-edit", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(self.root),
                                       "tool_input": {"file_path": str(self.pfad)},
                                       "tool_response": {"originalFile": alt}, **extra})

    def antwort(self, modell: str) -> dict:
        return {"type": "assistant", "message": {"role": "assistant", "model": modell, "content": []}}

    def test_modell_aus_dem_transkript(self):
        pfad = self.transkript("s.jsonl", self.antwort("claude-alt"), self.antwort("claude-neu"))
        self.edit(transcript_path=pfad)
        gen = self.fm(SEITE)["generated"]
        self.assertEqual(gen["by"], "claude-code/claude-neu")
        self.assertEqual(gen["modelle"][-1]["modell"], "claude-neu")

    def test_werkzeugausgabe_zaehlt_nicht(self):
        pfad = self.transkript("s.jsonl", self.antwort("claude-echt"),
                               {"type": "user", "message": {"model": "untergeschoben"}, "toolUseResult": {"model": "untergeschoben"}})
        self.edit(transcript_path=pfad)
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/claude-echt")

    def test_subagent_nimmt_nicht_das_modell_der_hauptsitzung(self):
        haupt = self.transkript("s.jsonl", self.antwort("claude-haupt"))
        self.edit(transcript_path=haupt, agent_id="a1")
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/unknown")

    def test_subagent_mit_eigenem_transkript(self):
        haupt = self.transkript("s.jsonl", self.antwort("claude-haupt"))
        self.transkript("s/subagents/agent-a1.jsonl", self.antwort("claude-sub"))
        self.edit(transcript_path=haupt, agent_id="a1")
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/claude-sub")

    def test_ohne_transkript_bleibt_unbekannt(self):
        self.edit()
        self.assertEqual(self.fm(SEITE)["generated"]["modelle"][-1]["modell"], "unknown")

    def test_fremder_akteur_bleibt(self):
        pfad = self.root / "wiki.toml"
        toml = pfad.read_text(encoding="utf-8")
        pfad.write_text(re.sub(r'^actor = "[^"]*"', 'actor = "eigene-pipeline/1.0"', toml, count=1, flags=re.M), encoding="utf-8")
        self.edit(transcript_path=self.transkript("s.jsonl", self.antwort("claude-neu")))
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "eigene-pipeline/1.0")

    def test_eingetragener_mensch_gilt_nicht_fuer_die_sitzung(self):
        pfad = self.root / "wiki.toml"
        toml = pfad.read_text(encoding="utf-8")
        pfad.write_text(re.sub(r'^actor = "[^"]*"', 'actor = "human:erika"', toml, count=1, flags=re.M), encoding="utf-8")
        self.edit()
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/unknown")
        self.edit(transcript_path=self.transkript("s.jsonl", self.antwort("claude-neu")))
        self.assertEqual(self.fm(SEITE)["generated"]["by"], "claude-code/claude-neu")

    def guard(self, neu: str):
        return self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                                   "tool_input": {"file_path": str(self.pfad), "content": neu}})

    def mit_vermerk(self) -> str:
        self.edit(transcript_path=self.transkript("s.jsonl", self.antwort("claude-neu")))
        return self.pfad.read_text(encoding="utf-8")

    def test_vermerk_laesst_sich_nicht_wegschreiben(self):
        text = self.mit_vermerk()
        done = self.guard(re.sub(r"(?ms)^  modelle:\n.*?(?=^---)", "", text))
        self.assertIn("generated.modelle", done.stdout + done.stderr)
        self.assertIn("deny", done.stdout)

    def test_generated_ganz_weg_ist_auch_gesperrt(self):
        text = self.mit_vermerk()
        done = self.guard(re.sub(r"(?ms)^generated:\n.*?(?=^---)", "", text))
        self.assertIn("deny", done.stdout)

    def test_erfundener_vermerk_wird_abgelehnt(self):
        text = self.pfad.read_text(encoding="utf-8")
        neu = re.sub(r"(?m)^generated: \{by: ([^,]+), at: ([^}]+)\}$",
                     r"generated:\n  by: \1\n  at: \2\n  modelle:\n  - {modell: erfunden, art: text}", text)
        self.assertIn("erfunden", neu)
        self.assertIn("deny", self.guard(neu).stdout)

    def test_text_aendern_bleibt_frei(self):
        done = self.guard(self.mit_vermerk() + "\nNoch ein Absatz.\n")
        self.assertNotIn("deny", done.stdout)
