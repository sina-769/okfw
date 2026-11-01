"""Gruppe 5: jeder wiki.toml-Schlüssel und jeder Befehl mit sichtbarer Wirkung.

Teil 1 schreibt je Schlüssel eine Zeile `name = …` in die wiki.toml eines frischen Wikis aus dem Profil
default und prüft, was sich ändert (index.md, log.md, Frontmatter, Lint-Befund, Exportdatei, Rückgabe aus
dem Kern). Teil 2 deckt Befehle und Optionen ab, die sonst kein Test aufruft. Kein Netz: Abrufe von
Metadaten laufen, wo nötig, über einen ersetzten `fetch_source`.
"""

import hashlib
import json
import re
import shutil
import subprocess
import unittest

from .hilfe import CORE, UV, WikiTest, clean_env

PANDOC = shutil.which("pandoc") is not None


class KonfTest(WikiTest):
    """Hilfen, um die wiki.toml des Testwikis zu ändern und Ergebnisse zu lesen."""

    def toml(self) -> str:
        return (self.root / "wiki.toml").read_text(encoding="utf-8")

    def setze(self, header: str, *zeilen: str) -> None:
        """Setzt `schluessel = wert`-Zeilen im Abschnitt `header` (etwa "[index]"): ersetzt eine vorhandene Zeile
        mit demselben Schlüssel, sonst steht die neue direkt unter der Überschrift; fehlt der Abschnitt, wird
        er angehängt. So entsteht nie eine doppelte Tabelle."""
        lines = self.toml().split("\n")
        head = re.compile(r"\s*" + re.escape(header) + r"\s*(#.*)?$")
        start = next((i for i, line in enumerate(lines) if head.match(line)), None)
        if start is None:
            text = self.toml().rstrip("\n") + f"\n\n{header}\n" + "\n".join(zeilen) + "\n"
        else:
            for zeile in zeilen:
                key = zeile.split("=")[0].strip()
                end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
                own = next((i for i in range(start + 1, end) if re.match(rf"\s*{re.escape(key)}\s*=", lines[i])), None)
                if own is None:
                    lines.insert(start + 1, zeile)
                else:
                    lines[own] = zeile
            text = "\n".join(lines)
        (self.root / "wiki.toml").write_text(text, encoding="utf-8")

    def toml_dazu(self, text: str) -> None:
        (self.root / "wiki.toml").write_text(self.toml().rstrip("\n") + "\n\n" + text.strip("\n") + "\n",
                                             encoding="utf-8")

    def datei_lesen(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def rebuild(self) -> None:
        self.core("rebuild", "--quiet")

    def ereignisse(self, rel: str = "wiki/.events.jsonl") -> list[dict]:
        return [json.loads(z) for z in self.datei_lesen(rel).splitlines() if z.strip()]

    def konzept(self, slug: str, *extra: str, titel: str | None = None) -> None:
        self.core("new", "concept", slug, "--title", titel or f"Konzept {slug}", "--description", "Beschreibung", *extra)

    def befunde(self, page: str) -> list[tuple[str, str]]:
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        return [(f["code"], f["message"]) for f in out["findings"] if f["path"] == f"wiki/{page}.md"]

    def zeile_mit(self, rel: str, text: str) -> str:
        zeilen = [z for z in self.datei_lesen(rel).splitlines() if text in z]
        self.assertTrue(zeilen, f"{rel}: keine Zeile mit {text!r}\n{self.datei_lesen(rel)}")
        return zeilen[0]


# ------------------------------------------------------------------------------------------------ [wiki], [paths]


class AbschnittWiki(KonfTest):
    def test_title(self):
        self.setze("[wiki]", 'title = "Mein Wiki – zuerst lesen"')
        self.rebuild()
        self.assertIn("\n# Mein Wiki – zuerst lesen\n", self.datei_lesen("wiki/index.md"))
        # das Dashboard lässt den Zusatz „zuerst lesen“ weg
        self.assertEqual(self.py("result = wiki.dashboard_data()['title']"), "Mein Wiki")

    def test_bundle(self):
        self.setze("[wiki]", 'bundle = "buendel"')
        self.konzept("k1")
        self.assertTrue((self.root / "buendel" / "concepts" / "k1.md").is_file())
        self.rebuild()
        self.assertIn("[Konzepte](concepts/)", self.datei_lesen("buendel/index.md"))
        self.assertIn("Bundle: buendel (1 Seiten)", self.core("status").stdout)

    def test_actor(self):
        self.setze("[wiki]", 'actor = "claude-code/test-modell"')
        self.konzept("k1")
        self.assertEqual(self.fm("concepts/k1")["generated"]["by"], "claude-code/test-modell")
        self.assertEqual(self.ereignisse()[-1]["by"], "claude-code/test-modell")

    def test_umgebungsvariable_wiki_actor_geht_vor(self):
        self.setze("[wiki]", 'actor = "claude-code/test-modell"')
        done = subprocess.run([UV, "run", "--quiet", "--script", str(CORE), "--root", str(self.root), "new", "concept",
                               "k1", "--title", "K1", "--description", "D"], capture_output=True, text=True,
                              env=clean_env(WIKI_ACTOR="human:erika"), cwd=self.root, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.fm("concepts/k1")["generated"]["by"], "human:erika")
        self.assertEqual(self.ereignisse()[-1]["by"], "human:erika")

    def test_okf_version(self):
        self.setze("[wiki]", 'okf_version = "0.3"')
        # der Root-Index trägt noch 0.2: Lint meldet es, rebuild zieht die neue Version nach
        self.assertIn("root_index_frontmatter", self.lint_codes())
        self.rebuild()
        self.assertTrue(self.datei_lesen("wiki/index.md").startswith('---\nokf_version: "0.3"\n---\n'))
        self.assertNotIn("root_index_frontmatter", self.lint_codes())


class AbschnittPaths(KonfTest):
    def test_events(self):
        self.setze("[paths]", 'events = "log/ereignisse.jsonl"')
        self.core("event", "note", ".", "Probe")
        self.assertEqual(self.ereignisse("log/ereignisse.jsonl")[-1]["detail"], "Probe")
        self.assertEqual(len(self.ereignisse("wiki/.events.jsonl")), 1)   # nur das Ereignis aus init
        self.assertIn("log/ereignisse.jsonl", self.py("result = okfw.protected_paths(wiki)"))

    def test_raw_leer_verweigert_capture(self):
        self.setze("[paths]", 'raw = ""')
        done = self.core("capture", "q1", "--url", "https://example.org", "--file", str(self.datei("a.md")), ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("kein [paths] raw", done.stderr)
        self.assertFalse((self.root / "raw").exists())

    def test_raw_anderer_ordner(self):
        self.setze("[paths]", 'raw = "rohdaten"')
        self.core("capture", "q1", "--url", "https://example.org", "--file", str(self.datei("a.md")),
                  "--license", "MIT")
        self.assertTrue((self.root / "rohdaten" / "q1" / "manifest.json").is_file())
        self.assertIn("rohdaten/**", self.py("result = okfw.protected_paths(wiki)"))
        self.assertIn("q1", self.core("status").stdout.split("Rohquellen ohne Karte:")[1])

    def test_protected(self):
        self.setze("[paths]", 'protected = ["notizen/**", "export/*.csv"]')
        patterns = self.py("result = okfw.protected_paths(wiki)")
        self.assertEqual(patterns[-2:], ["notizen/**", "export/*.csv"])
        self.assertIn("notizen/**", json.loads(self.core("status", "--hook").stdout)
                      ["hookSpecificOutput"]["additionalContext"])


# ------------------------------------------------------------------------------------------------ [index]


class AbschnittIndex(KonfTest):
    def test_root_full_listet_jede_seite(self):
        self.konzept("k1")
        self.rebuild()
        self.assertNotIn("(/concepts/k1.md)", self.datei_lesen("wiki/index.md"))   # overview: nur Anzahl
        self.setze("[index]", 'root = "full"')
        self.rebuild()
        text = self.datei_lesen("wiki/index.md")
        self.assertIn("# Konzepte\n\n* [Konzepte](concepts/) - 1 Seiten\n* [Konzept k1](/concepts/k1.md)", text)

    def test_sort(self):
        self.konzept("a-slug", titel="Zett")
        self.konzept("b-slug", titel="Anna")
        order = lambda: [m for m in re.findall(r"\[(Zett|Anna)\]", self.datei_lesen("wiki/concepts/index.md"))]
        self.rebuild()
        self.assertEqual(order(), ["Anna", "Zett"])
        self.setze("[index]", 'sort = "slug"')
        self.rebuild()
        self.assertEqual(order(), ["Zett", "Anna"])

    def test_entry_tag_und_start_heading(self):
        self.konzept("k1", "--tags", "einstieg", "--set", "order=2")
        self.konzept("k2", "--tags", "einstieg", "--set", "order=1")
        self.konzept("k3", "--tags", "start")
        self.rebuild()
        text = self.datei_lesen("wiki/index.md")
        start = text.split("# Zuerst lesen\n")[1].split("# Bereiche")[0]
        self.assertLess(start.index("k2.md"), start.index("k1.md"))
        self.assertNotIn("k3.md", start)
        self.setze("[index]", 'entry_tag = "start"', 'start_heading = ""')
        self.rebuild()
        text = self.datei_lesen("wiki/index.md")
        self.assertNotIn("# Zuerst lesen", text)
        self.assertRegex(text, r"# Wiki – zuerst lesen\n\n\* \[Konzept k3\]\(concepts/k3\.md\)")
        self.assertNotIn("k1.md", text)
        self.assertEqual(self.py("result = [p.id for p in wiki.learning_path()]"), ["concepts/k3"])

    def test_order_als_text_und_unbrauchbar(self):
        self.konzept("k1", "--tags", "einstieg", "--set", "order=10")
        self.konzept("k2", "--tags", "einstieg", "--set", 'order="2"')
        self.konzept("k3", "--tags", "einstieg", "--set", "order=abc")
        self.konzept("k4", "--tags", "einstieg")
        self.konzept("k5", "--set", "order=abc")
        self.rebuild()
        self.assertEqual(self.py("result = [p.id for p in wiki.learning_path()]"),
                         ["concepts/k2", "concepts/k1", "concepts/k3", "concepts/k4"])
        self.assertIn("order_invalid", self.lint_codes("concepts/k2"))
        self.assertIn("order_invalid", self.lint_codes("concepts/k3"))
        for sauber in ("k1", "k4", "k5"):
            self.assertNotIn("order_invalid", self.lint_codes(f"concepts/{sauber}"))

    def test_areas_heading_und_unit(self):
        self.konzept("k1")
        self.setze("[index]", 'areas_heading = "Rubriken"', 'unit = "Karten"')
        self.rebuild()
        text = self.datei_lesen("wiki/index.md")
        self.assertIn("# Rubriken\n", text)
        self.assertNotIn("# Bereiche", text)
        self.assertIn("(1 Karten)", self.zeile_mit("wiki/index.md", "[Konzepte]"))

    def test_badges(self):
        self.core("new", "source", "q1", "--manuell", "--titel", "Q1", "--url", "https://example.org/q1")
        self.core("lizenz", "sources/q1", "--name", "MIT")
        self.rebuild()
        zeile = self.zeile_mit("wiki/sources/index.md", "[Q1]")
        self.assertIn("`citation` `MIT`", zeile)       # Standard des Profils: access, license
        self.setze("[index]", 'badges = ["status", "access"]')
        self.rebuild()
        zeile = self.zeile_mit("wiki/sources/index.md", "[Q1]")
        self.assertIn("`draft` `citation`", zeile)
        self.assertNotIn("MIT", zeile)

    def test_star_field(self):
        self.konzept("k1", "--set", "core=true")
        self.konzept("k2")
        self.setze("[index]", 'star_field = "core"')
        self.rebuild()
        self.assertIn("[Konzept k1 ★](k1.md)", self.datei_lesen("wiki/concepts/index.md"))
        self.assertIn("[Konzept k2](k2.md)", self.datei_lesen("wiki/concepts/index.md"))

    def test_banner(self):
        self.konzept("k1")
        self.setze("[index]", 'banner = "> Generiert – nicht von Hand bearbeiten."')
        self.rebuild()
        for rel in ("wiki/index.md", "wiki/concepts/index.md"):
            self.assertIn("\n> Generiert – nicht von Hand bearbeiten.\n", self.datei_lesen(rel), rel)


# ------------------------------------------------------------------------------------------------ [log]


class AbschnittLog(KonfTest):
    def test_title_und_banner(self):
        self.setze("[log]", 'title = "Chronik"', 'banner = "> Neueste zuerst."')
        self.rebuild()
        self.assertTrue(self.datei_lesen("wiki/log.md").startswith("# Chronik\n\n> Neueste zuerst.\n\n## "))

    def test_link_text(self):
        self.konzept("k1", titel="Sprechender Titel")
        self.rebuild()
        self.assertIn("[Sprechender Titel](/concepts/k1.md)", self.datei_lesen("wiki/log.md"))
        self.setze("[log]", 'link_text = "id"')
        self.rebuild()
        self.assertIn("[concepts/k1](/concepts/k1.md)", self.datei_lesen("wiki/log.md"))
        self.assertNotIn("[Sprechender Titel]", self.datei_lesen("wiki/log.md"))

    def test_show_actor(self):
        self.konzept("k1")
        self.rebuild()
        self.assertNotIn("(claude-code/", self.zeile_mit("wiki/log.md", "Erstellt"))
        self.setze("[log]", 'show_actor = true')
        self.rebuild()
        self.assertTrue(self.zeile_mit("wiki/log.md", "Erstellt").endswith("(claude-code/claude-opus-5-5)"))

    def test_deprecate_heisst_ueberholt(self):
        self.konzept("k1")
        self.core("event", "deprecate", "concepts/k1", "Probe")
        self.rebuild()
        self.assertIn("* **Als überholt markiert**: [Konzept k1]", self.datei_lesen("wiki/log.md"))

    def test_labels(self):
        self.konzept("k1")
        self.core("event", "export", "concepts/k1", "Probe")
        self.setze("[log]", 'labels = {create = "Angelegt", export = "Ausgegeben"}')
        self.rebuild()
        text = self.datei_lesen("wiki/log.md")
        self.assertIn("* **Angelegt**: [Konzept k1]", text)
        self.assertIn("* **Ausgegeben**: [Konzept k1]", text)
        self.assertNotIn("**Erstellt**: [Konzept", text)
        self.assertIn("**Initialisierung**", text)   # nicht überschriebene Arten behalten das Standardwort


# ------------------------------------------------------------------------------------------------ [lint]


class AbschnittLint(KonfTest):
    def test_orphans(self):
        self.konzept("k1")
        self.konzept("k2")
        self.konzept("k3")
        self.core("body", "concepts/k1", "-", stdin="Siehe [Zwei](/concepts/k2.md).\n")
        self.assertIn("orphan", self.lint_codes("concepts/k1"))       # niemand verlinkt k1
        self.assertNotIn("orphan", self.lint_codes("concepts/k2"))
        self.setze("[lint]", 'orphans = "isolated"')
        self.assertNotIn("orphan", self.lint_codes("concepts/k1"))    # k1 verlinkt selbst
        self.assertIn("orphan", self.lint_codes("concepts/k3"))       # k3 weder noch

    def test_fulltext_access(self):
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "abstract",
                  "--resource", "https://example.org/q1")
        self.assertNotIn("fulltext_unanchored", self.lint_codes("sources/q1"))
        self.setze("[lint]", 'fulltext_access = ["fulltext", "abstract"]')
        self.assertIn("fulltext_unanchored", self.lint_codes("sources/q1"))

    def test_claims_section(self):
        self.quelle("q1")
        text = self.text("sources/q1") + "\n## Befunde\n\n- Etwas Behauptetes.\n"
        (self.root / "wiki/sources/q1.md").write_text(text, encoding="utf-8")
        self.assertNotIn("citation_with_claims", self.lint_codes("sources/q1"))   # Standard: Kernaussagen
        self.setze("[lint]", 'claims_section = "Befunde"')
        self.assertIn("citation_with_claims", self.lint_codes("sources/q1"))

    def test_priority_field(self):
        self.quelle("q1", extra=("--set", "core=true"))
        self.assertNotIn("priority_citation_only", self.lint_codes("sources/q1"))
        self.setze("[lint]", 'priority_field = "core"')
        self.assertIn("priority_citation_only", self.lint_codes("sources/q1"))
        self.quelle("q2")   # ohne das Feld: kein Befund
        self.assertNotIn("priority_citation_only", self.lint_codes("sources/q2"))


    def test_severity(self):
        self.konzept("k1")
        schwere = lambda code: [f["severity"] for f in json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
                                if f["code"] == code and f["path"] == "wiki/concepts/k1.md"]
        self.assertEqual(schwere("orphan"), ["warn"])
        self.setze("[lint.severity]", 'orphan = "off"', 'few_links = "error"')
        self.assertEqual(schwere("orphan"), [])
        self.assertEqual(schwere("few_links"), ["error"])
        self.assertEqual(self.core("lint", ok=False).returncode, 1)     # ein Fehler: Exit 1


# ------------------------------------------------------------------------------------------------ [export]


class UnbekannteSchluessel(KonfTest):
    def unbekannt(self) -> list[str]:
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        return [f["message"] for f in out["findings"] if f["code"] == "config_unknown" and f["path"] == "wiki.toml"]

    def test_profil_ohne_unbekannte_schluessel(self):
        self.assertEqual(self.unbekannt(), [])

    def test_tippfehler_in_trust_und_lint_severity(self):
        self.setze("[trust]", "signed_humen = true")
        self.setze("[lint.severity]", 'path_not_kebap = "error"')
        meldungen = self.unbekannt()
        self.assertIn("[trust] signed_humen kennt der Kern nicht und wirkt nicht (gemeint: signed_human?)", meldungen)
        self.assertIn("[lint.severity] path_not_kebap kennt der Kern nicht und wirkt nicht (gemeint: path_not_kebab?)",
                      meldungen)
        self.assertEqual(len(meldungen), 2)

    def test_abschnitte_und_exporte(self):
        self.toml_dazu('[visibilty]\nfield = "x"\n\n[export.ris]\npath = "a.ris"\n\n[export.csl]\npfad = "r.json"')
        meldungen = self.unbekannt()
        self.assertIn("[visibilty] kennt der Kern nicht und wirkt nicht (gemeint: visibility?)", meldungen)
        self.assertIn("[export.ris] kennt der Kern nicht und wirkt nicht", meldungen)
        self.assertIn("[export.csl] pfad kennt der Kern nicht und wirkt nicht", meldungen)

    def test_bekannter_abschnitt_ohne_tabelle_ist_ein_bedienfehler(self):
        toml = self.root / "wiki.toml"
        for sec in ("taxonomy", "trust"):
            text = toml.read_text(encoding="utf-8")
            toml.write_text(f'{sec} = "x"\n' + re.sub(rf"^\[{sec}\]\n(?:[^\[].*\n|\n)*", "", text, flags=re.M),
                            encoding="utf-8")
            done = self.core("lint", ok=False)
            self.assertEqual(done.returncode, 2)
            self.assertIn(f"[{sec}] muss eine Tabelle sein", done.stderr)
            self.assertNotIn("Traceback", done.stderr)
            toml.write_text(text, encoding="utf-8")

    def test_freie_tabellen_bleiben_ungeprueft(self):
        self.setze("[enums]", 'eigenes_feld = ["a"]')
        self.setze("[labels.eigenes_feld]", 'a = "A"')
        self.setze("[rights.by]", 'abbildung = "ja"')
        self.setze("[licenses.patterns]", '"beispiel" = "by"')
        self.setze("[log]", 'labels = {create = "Neu"}')
        self.toml_dazu('[types.notiz]\nfolder = "notizen"\ndefaults = {beliebig = 1}\n\n'
                       '[types.notiz.todo.citation]\nkernaussagen = "> [!todo] x"')
        self.assertEqual(self.unbekannt(), [])


class AbschnittExport(KonfTest):
    def test_csl_field_und_path(self):
        self.setze("[export.csl]", 'field = "zotero"', 'path = "build/refs.json"')
        self.core("new", "source", "q1", "--manuell", "--titel", "Titel", "--url", "https://example.org/q1",
                  "--autor", "Muster, M.", "--jahr", "2020")
        fm = self.fm("sources/q1")
        self.assertIn("zotero", fm)
        self.assertNotIn("csl", fm)
        self.rebuild()
        items = json.loads(self.datei_lesen("build/refs.json"))
        self.assertEqual([i["id"] for i in items], ["q1"])
        self.assertEqual(items[0]["title"], "Titel")

    def test_csl_export_auch_ohne_csl_block(self):
        self.setze("[export.csl]", 'path = "build/refs.json"')
        self.quelle("q2", extra=("--set", "date=2021-03-04"))      # ohne csl-Block
        self.assertNotIn("csl", self.fm("sources/q2"))
        self.rebuild()
        [item] = json.loads(self.datei_lesen("build/refs.json"))
        self.assertEqual((item["id"], item["title"], item["URL"], item["issued"]),
                         ("q2", "Quelle q2", "https://example.org/q2", {"date-parts": [[2021, 3, 4]]}))

    def test_csl_export_id_ist_der_slug_und_steht_vorn(self):
        self.setze("[export.csl]", 'path = "build/refs.json"')
        self.quelle("q2", extra=("--set", "csl={type: book, id: eigen, title: Ein Titel}"))
        self.rebuild()
        [item] = json.loads(self.datei_lesen("build/refs.json"))
        self.assertEqual(list(item)[:2], ["id", "type"])
        self.assertEqual(item["id"], "q2")

    @unittest.skipUnless(PANDOC, "pandoc fehlt")
    def test_csl_lang(self):
        item = {"id": "x", "type": "book", "title": "Ein Titel", "author": [{"literal": "Muster"}]}   # ohne Jahr
        code = f"result = wiki.render_apa([{json.dumps(item)}])"
        deutsch = self.py(code)
        self.setze("[export.csl]", 'lang = "en-US"')
        englisch = self.py(code)
        self.assertIn("(o. J.)", deutsch)      # de-DE, Standard von render_apa
        self.assertIn("(n.d.)", englisch)

    @unittest.skipUnless(PANDOC, "pandoc fehlt")
    def test_bibliography_path_und_title(self):
        self.setze("[export.bibliography]", 'path = "arbeit/literatur.md"', 'title = "Quellenverzeichnis"')
        self.core("new", "source", "q1", "--manuell", "--titel", "Mein Buch", "--url", "https://example.org/q1",
                  "--autor", "Muster, M.", "--jahr", "2020")
        self.rebuild()
        text = self.datei_lesen("arbeit/literatur.md")
        self.assertTrue(text.startswith("# Quellenverzeichnis\n"))
        self.assertIn("Mein Buch", text)

    def test_csl_style(self):
        self.setze("[export.csl]", 'style = "stil/eigen.csl"')
        self.assertEqual(self.py("result = str(wiki.csl_style())"), str(self.root / "stil" / "eigen.csl"))


# ------------------------------------------------------------------------------------------------ [scope]


class AbschnittScope(KonfTest):
    def scope(self, *zeilen: str) -> None:
        self.setze("[enums]", 'umfang = ["kern", "vertiefung"]')
        self.setze("[scope]", 'field = "umfang"', *zeilen)

    def test_field(self):
        self.konzept("k1")
        self.assertNotIn("scope_missing", self.lint_codes("concepts/k1"))    # ohne [scope] field aus
        self.scope()
        self.assertIn("scope_missing", self.lint_codes("concepts/k1"))
        self.konzept("k2", "--set", "umfang=kern")
        self.assertNotIn("scope_missing", self.lint_codes("concepts/k2"))
        self.konzept("k3", "--set", "umfang=falsch")
        self.assertIn("enum_invalid", self.lint_codes("concepts/k3"))

    def test_derive(self):
        self.quelle("q1")
        self.quelle("q2")
        self.scope()
        self.konzept("k1", "--sources", "q1", "--set", "umfang=vertiefung")
        self.konzept("k2", "--sources", "q1", "--set", "umfang=kern")
        scopes = self.py("result = wiki.scopes()")
        self.assertEqual(scopes["sources/q1"], ["kern", True])          # engster Umfang der zitierenden Seiten
        self.assertNotIn("sources/q2", scopes)                          # niemand zitiert q2
        self.assertNotIn("scope_missing", self.lint_codes("sources/q1"))
        self.assertIn("scope_missing", self.lint_codes("sources/q2"))
        self.setze("[scope]", 'derive = false')
        self.assertNotIn("sources/q1", self.py("result = wiki.scopes()"))
        self.assertIn("scope_missing", self.lint_codes("sources/q1"))

    def test_label_und_default(self):
        self.scope('label = "Reichweite"', 'default = "kern"')
        self.setze("[labels.umfang]", 'kern = "Kernbestand"')
        self.konzept("k1", "--set", "umfang=kern")
        spec = self.py("result = wiki.dashboard_data()['scope']")
        self.assertEqual(spec, {"field": "umfang", "label": "Reichweite", "default": "kern",
                                "values": [["kern", "Kernbestand"], ["vertiefung", "vertiefung"]]})
        self.assertIn("Reichweite: Kernbestand 1", self.core("status").stdout)
        self.setze("[scope]", 'default = ""')
        self.assertEqual(self.py("result = wiki.dashboard_data()['scope']['default']"), "")

    def test_ohne_field_kein_scope_im_dashboard(self):
        self.assertIsNone(self.py("result = wiki.dashboard_data()['scope']"))


# ------------------------------------------------------------------------------------------------ [visibility]


class AbschnittVisibility(KonfTest):
    def sicht(self, *zeilen: str) -> None:
        self.setze("[visibility]", 'field = "sichtbarkeit"', 'default = "oeffentlich"', 'hidden = ["intern"]', *zeilen)

    def test_field_default_hidden_in_oeffentlicher_ausgabe(self):
        self.sicht()
        self.konzept("offen")
        self.konzept("geheim", "--set", "sichtbarkeit=intern", titel="Geheimseite")
        normal = self.root / "normal.html"
        oeffentlich = self.root / "oeffentlich.html"
        self.core("dashboard", "--out", str(normal))
        self.core("dashboard", "--oeffentlich", "--out", str(oeffentlich))
        self.assertIn("Geheimseite", normal.read_text(encoding="utf-8"))
        self.assertNotIn("Geheimseite", oeffentlich.read_text(encoding="utf-8"))
        self.assertIn("Konzept offen", oeffentlich.read_text(encoding="utf-8"))
        self.assertEqual(self.py("result = wiki.visibility()['concepts/offen']"), ["oeffentlich", False])
        # default ändert, was Seiten ohne Feld sind
        self.setze("[visibility]", 'default = "intern"')
        self.assertIn("concepts/offen", self.py("result = sorted(wiki.hidden_ids())"))

    def test_label(self):
        self.sicht('label = "Zugang"')
        self.konzept("geheim", "--set", "sichtbarkeit=intern")
        self.assertEqual(self.py("result = wiki.dashboard_data()['visibility']['label']"), "Zugang")
        self.assertIn("Zugang: 1 Seiten fehlen in der öffentlichen Ausgabe", self.core("status").stdout)

    def test_derive(self):
        self.quelle("q1")
        self.quelle("q2")
        self.sicht()
        self.konzept("geheim", "--sources", "q1", "--set", "sichtbarkeit=intern")
        self.konzept("offen", "--sources", "q2")
        vis = self.py("result = wiki.visibility()")
        self.assertEqual(vis["sources/q1"], ["intern", True])           # nur eine verborgene Seite nutzt q1
        self.assertEqual(vis["sources/q2"], ["oeffentlich", False])
        self.assertIn("sources/q1", self.py("result = sorted(wiki.hidden_ids())"))
        self.setze("[visibility]", 'derive = false')
        self.assertEqual(self.py("result = wiki.visibility()['sources/q1']"), ["oeffentlich", False])

    def test_ohne_bekannte_werte_wird_ein_tippfehler_nicht_oeffentlich(self):
        self.setze("[visibility]", 'field = "sichtbarkeit"', 'hidden = ["intern"]')   # weder [enums] noch default
        self.konzept("offen")
        self.konzept("vertippt", "--set", "sichtbarkeit=intren", titel="Tippfehlerseite")
        out = json.loads(self.core("lint", "--json", ok=False).stdout)
        befund = [f for f in out["findings"] if f["code"] == "visibility_values_missing"]
        self.assertEqual([(f["severity"], f["path"]) for f in befund], [("error", "wiki.toml")])
        self.assertIn("concepts/vertippt", self.py("result = sorted(wiki.hidden_ids())"))
        self.assertNotIn("concepts/offen", self.py("result = sorted(wiki.hidden_ids())"))
        done = self.core("dashboard", "--oeffentlich", ok=None)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("visibility_values_missing", done.stderr)
        self.assertFalse((self.root / "build" / "wiki-dashboard-oeffentlich.html").exists())
        self.setze("[visibility]", 'default = "oeffentlich"')
        self.assertNotIn("visibility_values_missing", [f["code"] for f in json.loads(
            self.core("lint", "--json", ok=None).stdout)["findings"]])


# ------------------------------------------------------------------------------------------------ [fundweg], [sources]


class AbschnittFundweg(KonfTest):
    def test_field(self):
        self.setze("[fundweg]", 'field = "herkunft"')
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "citation",
                  "--resource", "https://example.org/q1", "--fundweg", "suche", "--db", "pubmed", "--query", "qm")
        fm = self.fm("sources/q1")
        self.assertEqual(fm["herkunft"]["art"], "suche")
        self.assertNotIn("fundweg", fm)
        self.assertNotIn("fundweg_missing", self.lint_codes("sources/q1"))
        self.quelle("q2")
        message = dict(self.befunde("sources/q2"))["fundweg_missing"]
        self.assertTrue(message.startswith("herkunft fehlt"), message)
        self.core("fundweg", "sources/q2", "--art", "person")
        self.assertEqual(self.fm("sources/q2")["herkunft"]["art"], "person")
        self.assertNotIn("fundweg_missing", self.lint_codes("sources/q2"))


FAKE_FETCH = """
wiki.fetch_source = lambda doi='', pmid='', arxiv='': (
    {'type': 'article-journal', 'title': 'Ein Aufsatz', 'author': [{'family': 'Muster', 'given': 'Max'}],
     'issued': {'date-parts': [[2020]]}, 'DOI': '10.1/x'},
    'https://doi.org/10.1/x', {'by': 'process:crossref', 'at': '2026-01-01T00:00:00Z'}, ['retraction'])
def offline(*args, **kwargs):
    raise OSError('offline')
okfw.http_json = okfw.http_bytes = offline   # die Rückzugsprüfung fragt PubMed; hier ohne Netz
args = okfw.build_parser().parse_args(['new', 'source', 'q9', '--doi', '10.1/x', '--trotz-rueckzug'])
okfw.cmd_new(wiki, args)
"""


class AbschnittSources(KonfTest):
    def test_checked_field_und_updates_field(self):
        self.setze("[sources]", 'checked_field = "geprueft_am"', 'updates_field = "hinweise_x"')
        self.setze("[types.source]", 'template = "quelle.md"')
        (self.root / "quelle.md").write_text("# {{kurzbeleg}}\n\n## Hinweise{{hinweise}}\n", encoding="utf-8")
        self.py(FAKE_FETCH)
        fm = self.fm("sources/q9")
        self.assertEqual(fm["geprueft_am"], {"by": "process:crossref", "at": "2026-01-01T00:00:00Z"})
        self.assertEqual(fm["hinweise_x"], ["retraction"])
        self.assertNotIn("metadaten_geprueft", fm)
        self.assertNotIn("crossref_hinweise", fm)
        self.assertIn("⚠ Crossref meldet: retraction", self.text("sources/q9"))

    def test_standardnamen_der_felder(self):
        self.py(FAKE_FETCH)
        fm = self.fm("sources/q9")
        self.assertEqual(fm["metadaten_geprueft"]["by"], "process:crossref")
        self.assertEqual(fm["crossref_hinweise"], ["retraction"])

    @unittest.skipUnless(PANDOC, "pandoc fehlt: ohne APA-Block kein Platz darunter; den Hinweis selbst prüfen die Tests darüber")
    def test_hinweis_auch_ohne_eigene_vorlage(self):
        self.py(FAKE_FETCH)
        zeilen = self.text("sources/q9").split("\n")
        apa = next(i for i, z in enumerate(zeilen) if z.startswith("> "))
        self.assertEqual(zeilen[apa + 1:apa + 3], ["", "- ⚠ Crossref meldet: retraction – vor Nutzung ansehen."])

    def test_manual_csl_type(self):
        self.setze("[sources]", 'manual_csl_type = "legislation"')
        self.core("new", "source", "g1", "--manuell", "--titel", "Gesetz", "--url", "https://example.org/g1")
        self.assertEqual(self.fm("sources/g1")["csl"]["type"], "legislation")
        self.core("new", "source", "g2", "--manuell", "--titel", "Bericht", "--url", "https://example.org/g2",
                  "--csl-typ", "report")
        self.assertEqual(self.fm("sources/g2")["csl"]["type"], "report")   # --csl-typ geht vor

    def test_manuell_ohne_einstellung_ist_document(self):
        self.core("new", "source", "g1", "--manuell", "--titel", "Ding", "--url", "https://example.org/g1")
        self.assertEqual(self.fm("sources/g1")["csl"]["type"], "document")


# ------------------------------------------------------------------------------------------------ [licenses], [rights]


class AbschnittLizenzen(KonfTest):
    def klasse(self) -> str:
        return json.loads(self.core("rechte", "sources/q1", "--json").stdout)["license"]["cls"]

    def vorbereiten(self) -> None:
        self.quelle("q1")
        self.core("lizenz", "sources/q1", "--name", "Hauslizenz 7", "--url", "https://haus.example/lizenz")

    def test_patterns_inline(self):
        self.vorbereiten()
        self.assertEqual(self.klasse(), "unbekannt")
        self.assertIn("license_unclassified", self.lint_codes("sources/q1"))
        self.setze("[licenses]", 'patterns = {"hauslizenz" = "vorbehalten"}')
        self.assertEqual(self.klasse(), "vorbehalten")
        self.assertNotIn("license_unclassified", self.lint_codes("sources/q1"))

    def test_patterns_tabelle_wirkt_auf_url(self):
        self.vorbereiten()
        self.toml_dazu('[licenses.patterns]\n"haus\\\\.example" = "by"')
        self.assertEqual(self.klasse(), "by")

    def test_rights_ueberschreibt_matrix(self):
        self.vorbereiten()
        self.setze("[licenses]", 'patterns = {"hauslizenz" = "vorbehalten"}')
        self.toml_dazu('[rights.vorbehalten]\nabbildung = "ja: Verlag hat zugestimmt"')
        rechte = json.loads(self.core("rechte", "sources/q1", "--json").stdout)["rights"]
        self.assertEqual(rechte["abbildung"], {"value": "ja", "note": "Verlag hat zugestimmt"})
        self.assertEqual(rechte["bearbeiten"]["value"], "nein")   # Rest der Klasse bleibt


# ------------------------------------------------------------------------------------------------ [dashboard], [taxonomy], [labels]


class AbschnittDashboard(KonfTest):
    def test_auto(self):
        self.konzept("k1")
        self.rebuild()
        self.assertFalse((self.root / "build" / "wiki-dashboard.html").exists())
        self.setze("[dashboard]", 'auto = true')
        self.core("rebuild", "--check")
        self.assertFalse((self.root / "build" / "wiki-dashboard.html").exists())   # --check schreibt nichts
        self.rebuild()
        self.assertIn("Konzept k1", self.datei_lesen("build/wiki-dashboard.html"))

    def test_path_und_public_path(self):
        self.setze("[dashboard]", 'path = "site/dash.html"')
        self.core("dashboard")
        self.assertTrue((self.root / "site" / "dash.html").is_file())
        self.assertEqual(self.py("result = str(wiki.dashboard_path(public=True))"),
                         str(self.root / "site" / "dash-oeffentlich.html"))
        self.setze("[dashboard]", 'public_path = "pub/index.html"')
        self.assertEqual(self.py("result = str(wiki.dashboard_path(public=True))"), str(self.root / "pub" / "index.html"))

    def test_glossary(self):
        self.assertEqual(self.py("result = wiki.dashboard_data()['glossary']"), ["concept", "entity"])
        self.setze("[dashboard]", 'glossary = ["entity"]')
        self.assertEqual(self.py("result = wiki.dashboard_data()['glossary']"), ["entity"])
        self.setze("[dashboard]", 'glossary = []')
        self.assertEqual(self.py("result = wiki.dashboard_data()['glossary']"), [])


class AbschnittTaxonomie(KonfTest):
    def test_field(self):
        self.konzept("k1", "--set", "domain=qm")
        self.konzept("k2", "--set", "thema=hygiene")
        domain = lambda: self.py("result = {n['id']: n['domain'] for n in wiki.dashboard_data()['nodes']}")
        self.assertEqual(domain(), {"concepts/k1": ["qm"], "concepts/k2": []})   # Standardfeld ist domain
        self.setze("[taxonomy]", 'field = "thema"')
        self.assertEqual(domain(), {"concepts/k1": [], "concepts/k2": ["hygiene"]})

    def test_label_gibt_es_nicht_mehr(self):
        # bis 0.4.0 landete [taxonomy] label nur in den Dashboard-Daten, angezeigt wurde es nie
        self.setze("[taxonomy]", 'label = "Rubrik"')
        self.assertNotIn("domain_label", self.py("result = sorted(wiki.dashboard_data())"))
        meldungen = [f["message"] for f in json.loads(self.core("lint", "--json", ok=None).stdout)["findings"]
                     if f["code"] == "config_unknown"]
        self.assertEqual(meldungen, ["[taxonomy] label kennt der Kern nicht und wirkt nicht"])

    def test_labels_je_feld(self):
        self.setze("[labels.domain]", 'qm = "Qualitätsmanagement"')
        self.assertEqual(self.py("result = wiki.dashboard_data()['labels']"), {"domain": {"qm": "Qualitätsmanagement"}})


# ------------------------------------------------------------------------------------------------ [types.<typ>]


class AbschnittTypen(KonfTest):
    def notiz(self, *zeilen: str) -> None:
        """Hängt einen eigenen Typ an; Standardwerte, wo ein Test nichts anderes setzt."""
        self.toml_dazu("[types.notiz]\n" + "\n".join(zeilen))

    def neu(self, slug: str = "n1", *extra: str) -> None:
        self.core("new", "notiz", slug, "--title", f"Notiz {slug}", "--description", "Beschreibung", *extra)

    def test_folder_label_description(self):
        self.notiz('folder = "merkzettel"', 'label = "Notizen"', 'description = "Kurze Zettel"')
        self.neu()
        self.assertTrue((self.root / "wiki/merkzettel/n1.md").is_file())
        self.assertEqual(self.fm("merkzettel/n1")["type"], "notiz")
        self.rebuild()
        self.assertIn("* [Notizen](merkzettel/) - Kurze Zettel (1 Seiten)", self.datei_lesen("wiki/index.md"))
        self.assertTrue(self.datei_lesen("wiki/merkzettel/index.md").startswith("# Notizen\n"))
        self.assertEqual(self.lint_codes("merkzettel/n1").count("type_folder_mismatch"), 0)

    def test_ohne_folder_und_label_gilt_der_typname(self):
        self.notiz('description = "Z"')
        self.neu()
        self.assertTrue((self.root / "wiki/notiz/n1.md").is_file())
        self.rebuild()
        self.assertIn("[notiz](notiz/)", self.datei_lesen("wiki/index.md"))

    def test_required(self):
        self.notiz('required = ["title", "description", "status", "generated", "autor"]')
        self.neu()
        self.assertIn(("missing_required", "Pflichtfeld autor fehlt (Profil)"), self.befunde("notiz/n1"))
        self.neu("n2", "--set", "autor=Ich")
        self.assertNotIn("missing_required", self.lint_codes("notiz/n2"))

    def test_recommended(self):
        self.notiz('recommended = ["ort", "kontext"]')
        self.neu("n1", "--set", "ort=Hamburg")
        self.assertEqual([m for c, m in self.befunde("notiz/n1") if c == "missing_recommended"],
                         ["kontext fehlt (empfohlen)"])

    def test_sections(self):
        self.notiz('sections = ["Kern", "Folgen"]')
        self.neu()
        text = self.text("notiz/n1")
        self.assertRegex(text, r"## Kern\n\n> \[!todo\] Ausstehend\.\n\n## Folgen\n")
        self.assertNotIn("sections_missing", self.lint_codes("notiz/n1"))
        self.core("body", "notiz/n1", "-", stdin="## Kern\n\nText.\n")
        self.assertEqual([m for c, m in self.befunde("notiz/n1") if c == "sections_missing"],
                         ["Abschnitte fehlen: Folgen"])

    def test_min_links(self):
        self.notiz('min_links = 1')
        self.konzept("k1")
        self.neu("n1")
        self.assertIn(("few_links", "nur 0 interne Links (Profil: mindestens 1)"), self.befunde("notiz/n1"))
        self.core("body", "notiz/n1", "-", stdin="Siehe [K1](/concepts/k1.md).\n")
        self.assertNotIn("few_links", self.lint_codes("notiz/n1"))
        self.konzept("k2")                                   # Standard der Typen: zwei Links
        self.core("body", "concepts/k2", "-", stdin="Siehe [K1](/concepts/k1.md).\n")
        self.assertIn(("few_links", "nur 1 interne Links (Profil: mindestens 2)"), self.befunde("concepts/k2"))

    def test_needs_footnotes(self):
        self.notiz('needs_footnotes = true')
        self.neu()
        self.assertIn("no_footnotes", self.lint_codes("notiz/n1"))
        self.quelle("q1")
        self.neu("n2", "--sources", "q1")
        self.core("body", "notiz/n2", "-", stdin="Aussage.[^q1]\n\n[^q1]: [Quelle q1](/sources/q1.md)\n")
        self.assertNotIn("no_footnotes", self.lint_codes("notiz/n2"))

    def test_stale_months(self):
        self.notiz('stale_months = 6')
        self.neu()
        erwartet = self.py("result = okfw.add_months(okfw.dt.datetime.now(okfw.dt.timezone.utc), 6)"
                           ".strftime('%Y-%m-%dT%H:%M:%SZ')")
        self.assertEqual(self.fm("notiz/n1")["stale_after"], erwartet)
        self.konzept("k1")                                   # Standardtyp concept: keine Frist
        self.assertNotIn("stale_after", self.fm("concepts/k1"))
        self.assertEqual(self.py("result = okfw.add_months(okfw.dt.datetime(2026, 8, 31, 5), 6).isoformat()"),
                         "2027-02-28T00:00:00")

    def test_template(self):
        (self.root / "vorlage.md").write_text("# {{title}}\n\nKurz: {{description}}\nOrt: {{fm.ort}}\n"
                                              "Unbekannt: {{gibts-nicht}}\n", encoding="utf-8")
        self.notiz('template = "vorlage.md"', 'defaults = {ort = "Bonn"}', 'sections = ["Kern"]')
        self.neu()
        body = self.text("notiz/n1").split("---\n", 2)[2]
        self.assertEqual(body.strip(), "# Notiz n1\n\nKurz: Beschreibung\nOrt: Bonn\nUnbekannt: {{gibts-nicht}}")
        self.assertNotIn("## Kern", body)                    # die Vorlage ersetzt die Abschnittsliste

    def test_template_wird_auch_im_skill_gesucht(self):
        self.notiz('template = "default.toml"')            # liegt in profiles/ des Skills, nicht neben der wiki.toml
        self.neu()
        self.assertIn("# wiki.toml – Grundprofil", self.text("notiz/n1"))

    def test_template_fehlt(self):
        self.notiz('template = "gibt-es-nicht.md"')
        done = self.core("new", "notiz", "n1", "--title", "N", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("Vorlage gibt-es-nicht.md fehlt", done.stderr)

    def test_defaults(self):
        self.notiz('defaults = {ort = "Berlin", rang = 3}')
        self.neu("n1")
        self.neu("n2", "--set", "ort=Köln")
        self.assertEqual({k: self.fm("notiz/n1")[k] for k in ("ort", "rang")}, {"ort": "Berlin", "rang": 3})
        self.assertEqual(self.fm("notiz/n2")["ort"], "Köln")   # eine Angabe geht vor

    def test_todo_als_untertabelle(self):
        self.toml_dazu('[types.source.todo.abstract]\nkernaussagen = "> [!todo] Eigener Platzhalter."')
        self.core("new", "source", "q1", "--title", "Q", "--description", "D", "--access", "abstract",
                  "--resource", "https://example.org/q1")
        text = self.text("sources/q1")
        self.assertIn("## Kernaussagen\n\n> [!todo] Eigener Platzhalter.", text)
        self.assertIn("## Kurzreferat\n\n> [!todo] Kurzreferat aus dem Abstract", text)   # nicht überschrieben
        self.quelle("q2")                                    # access citation: Standardtext
        self.assertIn("## Kernaussagen\n\n> [!todo] Erst nach Lektüre", self.text("sources/q2"))

    def test_todo_inline(self):
        self.setze("[types.source]", 'todo = {citation = {kurzreferat = "> [!todo] Mein Kurzreferat."}}')
        self.quelle("q1")
        self.assertIn("## Kurzreferat\n\n> [!todo] Mein Kurzreferat.", self.text("sources/q1"))

    def test_subtype(self):
        self.notiz('subtype = "ort"', 'defaults = {ort = "Kiel", art = "x"}')
        self.neu()
        node = self.py("result = [n for n in wiki.dashboard_data()['nodes'] if n['id'] == 'notiz/n1'][0]")
        self.assertEqual((node["subfield"], node["subtype"]), ("ort", "Kiel"))
        # ohne subtype: das erste vorhandene von category, format, evidenz, worktype
        self.konzept("k1", "--set", "format=guide", "--set", "category=praxis")
        node = self.py("result = [n for n in wiki.dashboard_data()['nodes'] if n['id'] == 'concepts/k1'][0]")
        self.assertEqual((node["subfield"], node["subtype"]), ("category", "praxis"))


# ------------------------------------------------------------------------------------------------ Teil 2: Befehle


class BefehlEventUndBody(KonfTest):
    def test_event_mit_by_und_detail(self):
        self.konzept("k1")
        self.core("event", "note", "concepts/k1", "Eine Notiz", "--by", "human:erika")
        self.core("event", "export", "concepts/k1")
        notiz, export = self.ereignisse()[-2:]
        self.assertEqual((notiz["kind"], notiz["page"], notiz["detail"], notiz["by"]),
                         ("note", "concepts/k1", "Eine Notiz", "human:erika"))
        self.assertEqual((export["detail"], export["by"]), ("", "claude-code/claude-opus-5-5"))   # Standard: [wiki] actor
        self.rebuild()
        self.assertIn("* **Notiz**: [Konzept k1](/concepts/k1.md) – Eine Notiz", self.datei_lesen("wiki/log.md"))
        self.assertIn("* **Export**: [Konzept k1]", self.datei_lesen("wiki/log.md"))

    def test_body_detail_steht_im_ereignis(self):
        self.konzept("k1")
        self.core("body", "concepts/k1", "-", "--detail", "Neu gefasst nach Rücksprache", stdin="Neuer Text.\n")
        ereignis = self.ereignisse()[-1]
        self.assertEqual((ereignis["kind"], ereignis["detail"]), ("update", "Neu gefasst nach Rücksprache"))
        self.core("body", "concepts/k1", "-", stdin="Noch ein Text.\n")
        self.assertEqual(self.ereignisse()[-1]["detail"], "Inhalt geschrieben")   # Standard ohne --detail
        # --nachziehen nimmt --detail ebenfalls
        (self.root / "wiki/concepts/k1.md").write_text(self.text("concepts/k1") + "\nVon Hand ergänzt.\n", encoding="utf-8")
        self.core("body", "concepts/k1", "--nachziehen", "--detail", "Edit im Editor")
        self.assertEqual(self.ereignisse()[-1]["detail"], "Edit im Editor")


class BefehlHash(KonfTest):
    def test_hash_und_alias_sha_mit_from(self):
        datei = self.root / "volltexte" / "a.txt"
        datei.parent.mkdir()
        datei.write_text("Volltext\n", encoding="utf-8")
        erwartet = hashlib.sha256(b"Volltext\n").hexdigest()
        for befehl in ("hash", "sha"):
            out = self.core(befehl, "volltexte/a.txt", "--from", "https://example.org/a.txt").stdout
            self.assertRegex(out, rf"^fulltext:\n  path: volltexte/a\.txt\n  sha256: {erwartet}\n"
                                  r"  retrieved_at: '\d{4}-\d\d-\d\dT[\d:]+Z'\n  retrieved_from: https://example.org/a\.txt\n$")

    def test_ohne_from_kein_retrieved_from(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.assertNotIn("retrieved_from", self.core("hash", "a.txt").stdout)

    def test_fehlende_datei_ist_bedienfehler(self):
        done = self.core("hash", "gibt-es-nicht.txt", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("Keine Datei", done.stderr)

    def test_block_auf_karte_wird_vom_lint_geprueft(self):
        datei = self.root / "volltexte" / "a.txt"
        datei.parent.mkdir()
        datei.write_text("Volltext\n", encoding="utf-8")
        sha = re.search(r"sha256: (\w+)", self.core("hash", "volltexte/a.txt").stdout).group(1)
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org/q1", "--volltext", "volltexte/a.txt", "--von", "https://example.org/a.txt")
        block = self.fm("sources/q1")["fulltext"]
        self.assertEqual((block["path"], block["sha256"], block["retrieved_from"]),
                         ("volltexte/a.txt", sha, "https://example.org/a.txt"))
        self.assertNotIn("fulltext_unanchored", self.lint_codes("sources/q1"))
        datei.write_text("verändert\n", encoding="utf-8")
        self.assertIn("fulltext_tampered", self.lint_codes("sources/q1"))


class BefehlRechte(KonfTest):
    def setUp(self):
        super().setUp()
        for slug in ("q1", "q2", "q3"):
            self.quelle(slug)
        self.core("lizenz", "sources/q1", "--name", "MIT")
        self.core("lizenz", "sources/q3", "--name", "Alle Rechte vorbehalten")

    def test_uebersicht(self):
        out = self.core("rechte").stdout
        self.assertIn("Lizenzen und Nutzungsrechte (3 Quellen)", out)
        for klasse in ("Namensnennung (1)", "Alle Rechte vorbehalten (1)", "Unbekannt (1)"):
            self.assertIn(klasse, out)
        self.assertIn("Lizenz ungeklärt (1): sources/q2", out)
        self.assertEqual(self.core("rights").stdout, out)    # Alias

    def test_uebersicht_json(self):
        out = json.loads(self.core("rechte", "--json").stdout)
        self.assertEqual(out["classes"], {"by": 1, "unbekannt": 1, "vorbehalten": 1})
        self.assertEqual(out["matrix"]["by"]["zitieren"], ["ja", ""])
        self.assertEqual(out["matrix"]["vorbehalten"]["abbildung"][0], "fragen")

    def test_eine_seite(self):
        out = self.core("rechte", "sources/q1").stdout
        self.assertIn("Lizenz: MIT\nKlasse: Namensnennung", out)
        self.assertRegex(out, r"Abbildung oder Tabelle übernehmen\s+mit Auflage – mit Namensnennung und Lizenzangabe")
        self.assertEqual(self.core("rights", "sources/q1").stdout, out)
        detail = json.loads(self.core("rechte", "sources/q1", "--json").stdout)
        self.assertEqual((detail["page"], detail["license"]["cls"], detail["stored"]), ("sources/q1", "by", []))
        self.assertEqual(detail["rights"]["bearbeiten"]["value"], "bedingt")

    def test_eine_seite_mit_abgelegtem_volltext(self):
        self.core("capture", "q4", "--url", "https://example.org", "--file", str(self.datei("a.md")), "--license", "MIT")
        self.core("new", "source", "q4", "--title", "Q4", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", "raw_packet=raw/q4")
        self.assertIn("Volltext: raw/q4 (nicht versioniert)", self.core("rechte", "sources/q4").stdout)

    def test_aktion_gruppiert_die_quellen(self):
        out = self.core("rechte", "--aktion", "abbildung").stdout
        self.assertIn("Abbildung oder Tabelle übernehmen (3 Quellen)", out)
        self.assertIn("mit Auflage (1):\n  sources/q1", out)
        self.assertIn("erst fragen (2):\n  sources/q2\n  sources/q3", out)
        groups = json.loads(self.core("rechte", "--aktion", "abbildung", "--json").stdout)
        self.assertEqual(groups, {"ja": [], "bedingt": ["sources/q1"], "nein": [], "fragen": ["sources/q2", "sources/q3"]})

    def test_unbekannte_aktion(self):
        done = self.core("rechte", "--aktion", "fliegen", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--aktion: eine von", done.stderr)

    def test_rights_der_karte_geht_vor_der_klasse(self):
        text = self.text("sources/q3").replace("status: draft\n", "status: draft\nrights: {abbildung: 'ja: Verlag hat zugestimmt'}\n")
        (self.root / "wiki/sources/q3.md").write_text(text, encoding="utf-8")
        gruppen = json.loads(self.core("rechte", "--aktion", "abbildung", "--json").stdout)
        self.assertEqual(gruppen["ja"], ["sources/q3"])


class BefehlNew(KonfTest):
    def test_new_schreibt_ereignis_create(self):
        self.konzept("k1")
        ereignis = self.ereignisse()[-1]
        self.assertEqual((ereignis["kind"], ereignis["page"], ereignis["by"]),
                         ("create", "concepts/k1", "claude-code/claude-opus-5-5"))

    def test_analysis_bekommt_stale_after(self):
        self.core("new", "analysis", "a1", "--title", "A", "--description", "D")
        erwartet = self.py("result = okfw.add_months(okfw.dt.datetime.now(okfw.dt.timezone.utc), 3)"
                           ".strftime('%Y-%m-%dT%H:%M:%SZ')")
        self.assertEqual(self.fm("analyses/a1")["stale_after"], erwartet)
        self.konzept("k1")
        self.assertNotIn("stale_after", self.fm("concepts/k1"))

    def test_manuell_mit_autor_jahr_csl_typ(self):
        self.core("new", "source", "q1", "--manuell", "--titel", "Ein Bericht", "--url", "https://example.org/q1",
                  "--autor", "Institut für Dinge", "--jahr", "2019", "--csl-typ", "report")
        fm = self.fm("sources/q1")
        csl = fm["csl"]
        self.assertEqual((csl["type"], csl["title"], csl["URL"]), ("report", "Ein Bericht", "https://example.org/q1"))
        self.assertEqual(csl["author"], [{"literal": "Institut für Dinge"}])
        self.assertEqual(csl["issued"], {"date-parts": [[2019]]})
        self.assertEqual(len(csl["accessed"]["date-parts"][0]), 3)
        self.assertEqual((fm["title"], fm["resource"], fm["access"]), ("Ein Bericht", "https://example.org/q1", "citation"))
        self.assertEqual(fm["aliases"], ["Institut für Dinge, 2019"])

    def test_manuell_ohne_autor_und_jahr(self):
        self.core("new", "source", "q1", "--manuell", "--titel", "Ohne", "--url", "https://example.org/q1")
        csl = self.fm("sources/q1")["csl"]
        self.assertEqual(csl["author"], [{"literal": "?"}])
        jahr = self.py("result = okfw.dt.date.today().year")
        self.assertEqual(csl["issued"], {"date-parts": [[jahr]]})

    def test_manuell_nimmt_originaldokument_wenn_das_profil_es_kennt(self):
        self.setze("[enums]", 'access = ["fulltext", "abstract", "citation", "originaldokument"]')
        self.core("new", "source", "q1", "--manuell", "--titel", "Gesetz", "--url", "https://example.org/g")
        self.assertEqual(self.fm("sources/q1")["access"], "originaldokument")
        self.core("new", "source", "q2", "--manuell", "--titel", "Gesetz", "--url", "https://example.org/g",
                  "--access", "citation")
        self.assertEqual(self.fm("sources/q2")["access"], "citation")      # eine Angabe geht vor
        self.assertIn("Kurzreferat aus dem Originaldokument", self.text("sources/q1"))

    def test_manuell_braucht_titel_und_url(self):
        done = self.core("new", "source", "q1", "--manuell", "--titel", "Nur Titel", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--manuell braucht --titel und --url", done.stderr)

    def test_nur_eine_metadatenquelle_und_nur_fuer_quellen(self):
        done = self.core("new", "source", "q1", "--manuell", "--doi", "10.1/x", "--titel", "T", "--url", "u", ok=False)
        self.assertIn("nur eine Metadatenquelle", done.stderr)
        done = self.core("new", "concept", "k1", "--title", "K", "--manuell", ok=False)
        self.assertIn("gibt es nur für Quellen", done.stderr)

    def test_deutsche_optionen(self):
        self.quelle("q1")
        self.core("new", "concept", "k1", "--titel", "Deutscher Titel", "--beschreibung", "Eine Beschreibung", "--quellen", "q1")
        fm = self.fm("concepts/k1")
        self.assertEqual((fm["title"], fm["description"], [s["id"] for s in fm["sources"]]),
                         ("Deutscher Titel", "Eine Beschreibung", ["q1"]))

    def test_csl_von_stdin_und_evidenz(self):
        csl = {"id": "wird-verworfen", "type": "book", "title": "Aus CSL", "author": [{"family": "Muster", "given": "Max"}],
               "issued": {"date-parts": [[2021]]}, "URL": "https://example.org/csl"}
        self.setze("[enums]", 'evidenz = ["peer-reviewed", "grau"]')
        self.core("new", "source", "q1", "--csl", "-", "--evidenz", "grau", "--access", "citation", stdin=json.dumps(csl))
        fm = self.fm("sources/q1")
        self.assertEqual((fm["title"], fm["resource"], fm["evidenz"]), ("Aus CSL", "https://example.org/csl", "grau"))
        self.assertNotIn("id", fm["csl"])
        self.assertEqual(fm["aliases"], ["Muster, 2021"])
        self.assertNotIn("enum_invalid", self.lint_codes("sources/q1"))

    def test_new_source_alias(self):
        done = self.core("new-source", "q1", "--manuell", "--titel", "Alias", "--url", "https://example.org/q1")
        self.assertIn("angelegt: wiki/sources/q1.md", done.stdout)
        self.assertEqual(self.fm("sources/q1")["type"], "source")
        self.assertEqual(self.ereignisse()[-1]["page"], "sources/q1")

    def test_new_fundweg_zitatverfolgung_mit_von(self):
        self.quelle("q0")
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "citation",
                  "--resource", "https://example.org/q1", "--fundweg", "zitatverfolgung", "--fundweg-von", "q0")
        self.assertEqual(self.fm("sources/q1")["fundweg"]["von"], "q0")


class BefehlFundweg(KonfTest):
    def setUp(self):
        super().setUp()
        self.quelle("q0")
        self.quelle("q1")

    def eintrag(self):
        return self.fm("sources/q1")["fundweg"]

    def test_nicht_auf_konzeptseiten(self):
        self.konzept("k1")
        vorher = self.text("concepts/k1")
        done = self.core("fundweg", "concepts/k1", "--art", "person", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("keine Quellenkarte", done.stderr)
        self.assertEqual(self.text("concepts/k1"), vorher)

    def test_suche_mit_db_und_query(self):
        done = self.core("fundweg", "sources/q1", "--art", "suche", "--db", "pubmed", "--query", "qualität AND bibliothek")
        self.assertIn("sources/q1: Fundweg Suche", done.stdout)
        eintrag = self.eintrag()
        self.assertEqual({k: eintrag[k] for k in ("art", "db", "query")},
                         {"art": "suche", "db": "pubmed", "query": "qualität AND bibliothek"})
        self.assertEqual(eintrag["by"], "claude-code/claude-opus-5-5")
        self.assertRegex(eintrag["at"], r"^\d{4}-\d\d-\d\dT[\d:]+Z$")
        self.assertEqual(self.ereignisse()[-1]["detail"], "Fundweg: suche (pubmed)")
        self.assertNotIn("fundweg_incomplete", self.lint_codes("sources/q1"))

    def test_ohne_add_ersetzt_add_haengt_an(self):
        self.core("fundweg", "sources/q1", "--art", "person")
        self.core("fundweg", "sources/q1", "--art", "material")
        self.assertEqual(self.eintrag()["art"], "material")
        done = self.core("fundweg", "sources/q1", "--art", "suche", "--db", "crossref", "--query", "x", "--add")
        self.assertIn("(ergänzt)", done.stdout)
        self.assertEqual([e["art"] for e in self.eintrag()], ["material", "suche"])
        # --add auf eine Karte ohne Fundweg legt den ersten an
        self.core("fundweg", "sources/q0", "--art", "person", "--add")
        self.assertEqual(self.fm("sources/q0")["fundweg"]["art"], "person")

    def test_at_und_by_fuer_nachtraege(self):
        self.core("fundweg", "sources/q1", "--art", "person", "--at", "2026-03-01T09:30:00+01:00", "--by", "human:erika")
        eintrag = self.eintrag()
        self.assertEqual((eintrag["at"], eintrag["by"]), ("2026-03-01T09:30:00+01:00", "human:erika"))
        done = self.core("fundweg", "sources/q1", "--art", "person", "--at", "gestern", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--at muss ein ISO-8601-Zeitpunkt", done.stderr)
        done = self.core("fundweg", "sources/q1", "--art", "person", "--by", "erika", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--by folgt der Akteurskonvention", done.stderr)
        self.assertEqual(self.eintrag()["by"], "human:erika")      # die Fehlversuche änderten nichts

    def test_zitatverfolgung_braucht_von(self):
        done = self.core("fundweg", "sources/q1", "--art", "zitatverfolgung", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("fundweg … --von <slug>", done.stderr)
        self.core("fundweg", "sources/q1", "--art", "zitatverfolgung", "--von", "q0")
        self.assertEqual(self.eintrag()["von"], "q0")
        self.assertNotIn("fundweg_invalid", self.lint_codes("sources/q1"))
        self.core("fundweg", "sources/q1", "--art", "zitatverfolgung", "--von", "gibt-es-nicht")
        self.assertIn("fundweg_invalid", self.lint_codes("sources/q1"))     # der Lint prüft, ob die Karte da ist

    def test_unbekannte_art(self):
        self.assertEqual(self.core("fundweg", "sources/q1", "--art", "zufall", ok=False).returncode, 2)


class BefehlLizenz(KonfTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")

    def lizenz(self):
        return self.fm("sources/q1")["license"]

    def test_name_url_class_via(self):
        done = self.core("lizenz", "sources/q1", "--name", "CC BY 4.0", "--url", "https://creativecommons.org/licenses/by/4.0/",
                         "--class", "nd", "--via", "originaldokument")
        self.assertIn("sources/q1: CC BY 4.0 → Keine Bearbeitung", done.stdout)     # --class geht vor dem Muster
        lizenz = self.lizenz()
        self.assertEqual({k: lizenz[k] for k in ("name", "url", "class", "via")},
                         {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/", "class": "nd",
                          "via": "originaldokument"})
        self.assertRegex(lizenz["checked"], r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(self.ereignisse()[-1]["detail"], "Lizenz: CC BY 4.0")

    def test_via_ohne_angabe_ist_manuell(self):
        self.core("lizenz", "sources/q1", "--name", "MIT")
        self.assertEqual(self.lizenz()["via"], "manuell")

    def test_dry_run_schreibt_nichts(self):
        vorher = self.text("sources/q1")
        ereignisse = len(self.ereignisse())
        done = self.core("lizenz", "sources/q1", "--name", "MIT", "--class", "by", "--dry-run")
        self.assertIn("sources/q1: {'name': 'MIT'", done.stdout)
        self.assertIn("'class': 'by'", done.stdout)
        self.assertEqual(self.text("sources/q1"), vorher)
        self.assertEqual(len(self.ereignisse()), ereignisse)

    def test_missing_ohne_fetch_bricht_ab(self):
        done = self.core("lizenz", "--missing", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--name (mit --url, --class) setzen oder --fetch nutzen", done.stderr)
        self.assertEqual(self.lizenz()["name"], "unknown")

    def test_ohne_seite_und_fehlerhafte_angaben(self):
        self.assertIn("Seite angeben oder --missing", self.core("lizenz", "--name", "MIT", ok=False).stderr)
        self.assertIn("--name (mit --url, --class)", self.core("lizenz", "sources/q1", ok=False).stderr)
        done = self.core("lizenz", "sources/q1", "--name", "X", "--class", "frei-erfunden", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--class: eine von", done.stderr)

    def abruf(self, *args: str) -> dict:
        """lizenz --fetch ohne Netz: lookup_license liefert CC BY 4.0; Ergebnis {Ausgabe, Lizenzen je Karte}."""
        return self.py("import contextlib, io\n"
                       "okfw.Wiki.lookup_license = lambda self, *ids: {'name': 'CC BY 4.0', 'url': "
                       "'https://creativecommons.org/licenses/by/4.0/', 'checked': '2026-10-29', 'via': 'crossref (vor)'}\n"
                       "out = io.StringIO()\n"
                       "with contextlib.redirect_stdout(out):\n"
                       f"    code = okfw.main(['--root', {str(self.root)!r}, 'lizenz', *{list(args)!r}])\n"
                       "result = {'code': code, 'out': out.getvalue(), 'lic': {p.id: p.fm.get('license') for p in "
                       "okfw.Wiki.load(root=wiki.root).source_cards()}}")

    def test_fetch_laesst_benannte_lizenz_ohne_force_stehen(self):
        self.quelle("q2", extra=("--set", "doi=10.1000/q2"))
        self.core("lizenz", "sources/q1", "--name", "Hauseigene Nutzungsregel")     # benannt, nicht eingeordnet
        self.core("lizenz", "sources/q1", "--url", "https://doi.org/10.1000/q1", "--name", "Hauseigene Nutzungsregel")
        vorher = self.text("sources/q1")
        einzeln = self.abruf("sources/q1", "--fetch")
        self.assertIn("hat schon eine Lizenz (Hauseigene Nutzungsregel); --force", einzeln["out"])
        alle = self.abruf("--missing", "--fetch")
        self.assertIn("1 von 1 Lizenzen gefunden", alle["out"])                     # nur q2 mit unknown
        self.assertIn("1 benannte Lizenz(en) ohne Klasse übersprungen (`lizenz <seite> --class …` oder `--force`)",
                      alle["out"])
        self.assertEqual(alle["lic"]["sources/q2"]["name"], "CC BY 4.0")
        self.assertEqual(self.text("sources/q1"), vorher)
        mit_force = self.abruf("sources/q1", "--fetch", "--force")
        self.assertEqual(mit_force["lic"]["sources/q1"]["name"], "CC BY 4.0")

    def test_alias_license(self):
        self.core("license", "sources/q1", "--name", "Apache-2.0")
        self.assertEqual(self.lizenz()["name"], "Apache-2.0")


class BefehlSourcesQuellenAlias(KonfTest):
    def test_add_zieht_generated_nach_und_laesst_status_und_verified_stehen(self):
        self.quelle("q1")
        self.konzept("k1")
        self.core("verify", "concepts/k1", "--by", "human:erika", "--status", "stable", "--force")
        pfad = self.root / "wiki/concepts/k1.md"
        pfad.write_text(re.sub(r"generated: \{by: [^,]+, at: '[^']+'\}",
                               "generated: {by: claude-code/alt, at: '2020-01-01T00:00:00Z'}", self.text("concepts/k1")),
                        encoding="utf-8")
        vorher = self.fm("concepts/k1")
        self.assertEqual(vorher["generated"]["by"], "claude-code/alt")
        done = self.core("quellen", "concepts/k1", "--add", "q1")      # Alias
        self.assertIn("concepts/k1: Quellen +q1", done.stdout)
        nachher = self.fm("concepts/k1")
        self.assertEqual(nachher["generated"]["by"], "claude-code/claude-opus-5-5")
        self.assertGreater(nachher["generated"]["at"], "2020-01-01T00:00:00Z")
        self.assertEqual((nachher["status"], nachher["verified"]), ("stable", vorher["verified"]))
        self.assertEqual([s["id"] for s in nachher["sources"]], ["q1"])

    def test_ohne_add_und_remove_ist_bedienfehler(self):
        self.konzept("k1")
        done = self.core("sources", "concepts/k1", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--add a,b oder --remove a,b angeben", done.stderr)


class BefehlRebuildCheck(KonfTest):
    def test_check_meldet_veraltete_projektion_mit_exit_1(self):
        self.konzept("k1")
        done = self.core("rebuild", "--check", ok=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn("veraltet: wiki/concepts/index.md", done.stdout)
        self.assertIn("veraltet: wiki/log.md", done.stdout)
        self.assertFalse((self.root / "wiki/concepts/index.md").exists())     # --check schreibt nichts

    def test_check_ist_exit_0_wenn_alles_aktuell_ist(self):
        self.konzept("k1")
        self.core("rebuild")
        done = self.core("rebuild", "--check")
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        # eine von Hand veränderte Projektion zählt als veraltet
        index = self.root / "wiki/concepts/index.md"
        index.write_text(index.read_text(encoding="utf-8") + "\nvon Hand\n", encoding="utf-8")
        done = self.core("rebuild", "--check", ok=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn("veraltet: wiki/concepts/index.md", done.stdout)

    def test_alias_index(self):
        self.konzept("k1")
        done = self.core("index")
        self.assertIn("Projektionen neu erzeugt: ", done.stdout)
        self.assertTrue((self.root / "wiki/concepts/index.md").is_file())
        self.assertEqual(self.core("index", "--check").returncode, 0)


class BefehlDashboardOut(KonfTest):
    def test_out_absolut_und_relativ(self):
        self.konzept("k1", titel="Dashboard-Probe")
        ziel = self.base / "woanders" / "d.html"
        done = self.core("dashboard", "--out", str(ziel))
        self.assertIn(f"Dashboard: {ziel}", done.stdout)
        text = ziel.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!DOCTYPE html>") or text.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn("Dashboard-Probe", text)
        self.assertFalse((self.root / "build" / "wiki-dashboard.html").exists())    # --out ersetzt [dashboard] path
        self.core("dashboard", "--out", "ausgabe/relativ.html")                      # relativ zum Arbeitsordner
        self.assertTrue((self.root / "ausgabe" / "relativ.html").is_file())

    def test_ohne_out_gilt_dashboard_path(self):
        self.core("dashboard")
        self.assertTrue((self.root / "build" / "wiki-dashboard.html").is_file())


if __name__ == "__main__":
    unittest.main()
