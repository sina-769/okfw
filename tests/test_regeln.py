"""Lint-Regeln: jeder Befund-Code wird von mindestens einem echten Test ausgelöst.

Grundlage ist ein sauberes Wiki (Quelle q1, Konzepte k1 und k2, alle Projektionen aktuell, kein Befund).
Ein Test verändert genau eine Stelle, ruft `lint --json` auf und erwartet den Code auf der erwarteten Seite;
dass der Code im sauberen Zustand fehlt, ist die Gegenprobe. Wo die Korrektur mehr ist als das Zurücksetzen
(rebuild, lizenz, fundweg), prüft der Test sie eigens. Die Codes stehen als Zeichenketten im Test, damit die
Abdeckungsprüfung sie unter tests/ findet.
"""

import hashlib
import json
import shutil
import subprocess
import unittest

from .hilfe import WikiTest, clean_env

ACTOR = "claude-code/unknown"   # [wiki] actor im Profil default
GEN = f"generated: {{by: {ACTOR}, at: '2026-10-29T09:00:00Z'}}"

Q1 = f"""---
type: source
title: Quelle q1
description: Testquelle
resource: https://example.org/q1
status: draft
access: abstract
license: {{name: MIT, url: null}}
date: '2026-10-25'
fundweg: {{art: person, at: '2026-10-25T09:00:00Z', by: 'human:pruefende'}}
{GEN}
---

# Quelle q1

## Kurzreferat

Kurzfassung (abstract-basiert).

## Kernaussagen

- Aussage (Abstract)

## Relevanz

Belegt beide Konzepte.

## Verbindungen

[Konzept eins](/concepts/k1.md) und [Konzept zwei](/concepts/k2.md).

## Zugang

Öffentlich abrufbar.
"""

KONZEPT = """---
type: concept
title: Konzept {nr}
description: Testkonzept {nr}
status: draft
domain: test
aliases: [k-{nr}]
{gen}
sources:
- {{id: q1, resource: /sources/q1.md}}
---

# Konzept {nr}

Aussage.[^q1] Siehe [das andere Konzept](/concepts/{anderes}.md).

[^q1]: [Quelle q1](/sources/q1.md), Abschn. 4
"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RegelTest(WikiTest):
    """Sauberes Wiki ohne Befund; Hilfen zum Verändern einzelner Stellen."""

    def setUp(self):
        super().setUp()
        self.schreibe("sources/q1", Q1)
        self.schreibe("concepts/k1", KONZEPT.format(nr="eins", anderes="k2", gen=GEN))
        self.schreibe("concepts/k2", KONZEPT.format(nr="zwei", anderes="k1", gen=GEN))
        self.core("rebuild", "--quiet")

    # --- Hilfen
    def schreibe(self, page: str, text: str) -> None:
        pfad = self.root / "wiki" / f"{page}.md"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(text, encoding="utf-8")

    def aendere(self, page: str, alt, neu: str = "") -> None:
        """Ersetzt `alt` durch `neu` (oder wendet die Funktion `alt` auf den Text an) und prüft, dass etwas geschah."""
        text = self.text(page)
        if callable(alt):
            ergebnis = alt(text)
        else:
            self.assertIn(alt, text, f"{page}: Textstelle für die Änderung fehlt")
            ergebnis = text.replace(alt, neu, 1)
        self.assertNotEqual(ergebnis, text)
        self.schreibe(page, ergebnis)

    def toml(self, alt: str, neu: str) -> None:
        pfad = self.root / "wiki.toml"
        text = pfad.read_text(encoding="utf-8")
        self.assertIn(alt, text)
        pfad.write_text(text.replace(alt, neu, 1), encoding="utf-8")

    def befunde(self, page: str | None = None, ok=None) -> list[dict]:
        out = json.loads(self.core("lint", "--json", ok=ok).stdout)
        return [f for f in out["findings"] if page is None or f["path"] == f"wiki/{page}.md"]

    def tabelle(self, zeilen):
        """Je Zeile (code, seite, alt, neu[, seite_des_befunds]): verändern, linten, Code erwarten, zurücksetzen."""
        basis = set(self.lint_codes())
        for code, seite, alt, neu, *auf in zeilen:
            with self.subTest(code=code):
                self.assertNotIn(code, basis, "im sauberen Wiki darf der Code nicht vorkommen")
                original = self.text(seite)
                try:
                    self.aendere(seite, alt, neu)
                    self.assertIn(code, self.lint_codes(auf[0] if auf else seite))
                finally:
                    self.schreibe(seite, original)

    def test_sauberes_wiki_ohne_befund(self):
        self.assertEqual(self.befunde(), [])


def ohne_datum(text: str) -> str:
    return text.replace("date: '2026-10-25'\n", "")


class Konformitaet(RegelTest):
    """OKF §4, §8, §9, §11: Bundle, Frontmatter, reservierte Dateien."""

    def test_bundle_fehlt(self):
        shutil.rmtree(self.root / "wiki")
        self.assertEqual(self.befunde(ok=None)[0]["code"], "bundle_missing")
        self.assertEqual(self.core("lint", ok=None).returncode, 1)
        (self.root / "wiki").mkdir()
        self.assertNotIn("bundle_missing", self.lint_codes())

    def test_seiten(self):
        self.tabelle([
            ("frontmatter_invalid", "concepts/k1", "title: Konzept eins\n", "title: [\n"),
            ("frontmatter_invalid", "concepts/k1", lambda t: t.split("---\n", 2)[2], ""),
            ("missing_type", "concepts/k1", "type: concept\n", ""),
        ])

    def test_reservierte_dateien(self):
        vorn = lambda t: "---\ntitle: nicht erlaubt\n---\n" + t
        self.tabelle([
            ("okf_version_missing", "index", '---\nokf_version: "0.2"\n---\n', ""),
            ("root_index_frontmatter", "index", 'okf_version: "0.2"', 'okf_version: "0.1"'),
            ("index_frontmatter", "concepts/index", vorn, ""),
            ("log_frontmatter", "log", vorn, ""),
            ("log_date_heading", "log", lambda t: t + "\n## gestern\n", ""),
            ("log_order", "log", lambda t: t + "\n## 2026-01-01\n\n## 2026-02-01\n", ""),
        ])

    def test_rebuild_repariert_reservierte_dateien(self):
        vorn = lambda t: "---\ntitle: nicht erlaubt\n---\n" + t
        self.aendere("index", '---\nokf_version: "0.2"\n---\n', "")
        self.aendere("concepts/index", vorn)
        self.aendere("log", lambda t: vorn(t) + "\n## gestern\n\n## 2026-01-01\n\n## 2026-02-01\n")
        codes = set(self.lint_codes())
        self.assertLessEqual({"okf_version_missing", "index_frontmatter", "log_frontmatter", "log_date_heading",
                              "log_order", "projection_stale"}, codes)
        self.core("rebuild", "--quiet")
        self.assertEqual(self.befunde(), [])


class HerkunftVertrauen(RegelTest):
    """OKF §5, §7: Status, generated, verified, Zeitpunkte, sources[] und Fußnoten."""

    K1 = "concepts/k1"

    def test_lebenszyklus(self):
        verified = "status: draft\nverified:\n- {by: human:x}\n"
        self.tabelle([
            ("status_invalid", self.K1, "status: draft", "status: fertig"),
            ("generated_invalid", self.K1, GEN, "generated: {at: '2026-10-29T09:00:00Z'}"),
            ("generated_invalid", self.K1, GEN, "generated: nachts"),
            ("actor_format", self.K1, f"by: {ACTOR}", "by: Claude"),
            ("actor_format", self.K1, "status: draft\n",
             "status: draft\nverified:\n- {by: Claude, at: '2026-10-29T09:00:00Z'}\n"),
            ("timestamp_invalid", self.K1, "at: '2026-10-29T09:00:00Z'", "at: gestern"),
            ("timestamp_invalid", self.K1, "status: draft\n", "status: draft\nstale_after: irgendwann\n"),
            ("verified_invalid", self.K1, "status: draft\n", verified),
            ("verified_invalid", self.K1, "status: draft\n",
             "status: draft\nverified:\n- {by: human:x, at: heute}\n"),
            ("stale", self.K1, "status: draft\n", "status: draft\nstale_after: '2020-01-01T00:00:00Z'\n"),
        ])

    def test_stale_nur_in_der_vergangenheit(self):
        self.aendere(self.K1, "status: draft\n", "status: draft\nstale_after: '2099-01-01T00:00:00Z'\n")
        self.assertNotIn("stale", self.lint_codes(self.K1))

    def test_sources_und_fussnoten(self):
        eintrag = "- {id: q1, resource: /sources/q1.md}\n"
        self.tabelle([
            ("source_invalid", self.K1, eintrag, "- {id: q1}\n"),
            ("source_duplicate_id", self.K1, eintrag, eintrag + eintrag),
            ("source_missing", self.K1, "resource: /sources/q1.md}", "resource: /sources/fehlt.md}"),
            ("footnote_undefined", self.K1, "[^q1]: [Quelle q1](/sources/q1.md), Abschn. 4\n", ""),
            ("source_uncited", self.K1, "Aussage.[^q1]", "Aussage."),
        ])


class Profil(RegelTest):
    """Regeln aus wiki.toml: Pflichtfelder, Typen, Ordner, Dateinamen, Abschnitte, Fußnoten."""

    def test_felder_und_typen(self):
        k1 = "concepts/k1"
        self.tabelle([
            ("missing_required", k1, "description: Testkonzept eins\n", ""),
            ("missing_recommended", k1, "domain: test\n", ""),
            ("missing_recommended", "sources/q1", "license: {name: MIT, url: null}\n", ""),
            ("type_unknown", k1, "type: concept", "type: widget"),
            ("type_folder_mismatch", k1, "type: concept", "type: entity"),
            ("no_footnotes", k1, "Aussage.[^q1]", "Aussage."),
            ("sections_missing", "sources/q1", "## Zugang", "## Anderes"),
        ])

    def test_dateiname_nicht_kebab(self):
        self.schreibe("concepts/Konzept_Drei", self.text("concepts/k1"))
        self.assertIn("path_not_kebab", self.lint_codes("concepts/Konzept_Drei"))
        self.assertNotIn("path_not_kebab", self.lint_codes("concepts/k1"))

    def test_fussnoten_gegenprobe(self):
        self.aendere("concepts/k1", "Aussage.[^q1]", "Aussage.")
        self.assertIn("no_footnotes", self.lint_codes("concepts/k1"))
        self.aendere("concepts/k1", "Aussage.", "Aussage.[^q1]")
        self.assertNotIn("no_footnotes", self.lint_codes("concepts/k1"))

    def test_neue_seite_aus_new_meldet_empfehlungen(self):
        self.core("new", "concept", "k3", "--title", "Drei", "--description", "D", "--sources", "q1")
        codes = self.lint_codes("concepts/k3")
        self.assertIn("missing_recommended", codes)
        self.assertIn("no_footnotes", codes)
        self.assertIn("source_uncited", codes)


class Graph(RegelTest):
    """OKF §6: Links, Verwaisung, Projektionen."""

    def test_links_und_waisen(self):
        k1 = "concepts/k1"
        self.tabelle([
            ("broken_link", k1, "Aussage.", "Aussage. [x](/concepts/gibtsnicht.md)"),
            ("link_leaves_bundle", k1, "Aussage.", "Aussage. [x](../../README.md)"),
            ("few_links", k1, "[das andere Konzept](/concepts/k2.md)", "das andere Konzept"),
        ])

    def nimm_k1_alle_eingehenden_links(self):
        self.aendere("concepts/k2", "[das andere Konzept](/concepts/k1.md)", "das andere Konzept")
        self.aendere("sources/q1", "[Konzept eins](/concepts/k1.md)", "Konzept eins")

    def test_seite_ohne_eingehende_links_ist_waise(self):
        self.assertNotIn("orphan", self.lint_codes())
        self.nimm_k1_alle_eingehenden_links()
        self.assertIn("orphan", self.lint_codes("concepts/k1"))
        self.aendere("concepts/k2", "das andere Konzept", "[das andere Konzept](/concepts/k1.md)")
        self.assertNotIn("orphan", self.lint_codes("concepts/k1"))

    def test_orphans_isolated_zaehlt_ausgehende_links(self):
        self.nimm_k1_alle_eingehenden_links()
        self.assertIn("orphan", self.lint_codes("concepts/k1"))
        self.toml('orphans = "inbound"', 'orphans = "isolated"')
        self.assertNotIn("orphan", self.lint_codes("concepts/k1"))

    def test_projektion_veraltet_rebuild_behebt(self):
        self.aendere("concepts/k1", "title: Konzept eins", "title: Ganz anderer Titel")
        self.assertIn("projection_stale", self.lint_codes("concepts/index"))
        self.core("rebuild", "--quiet")
        self.assertNotIn("projection_stale", self.lint_codes())

    def test_bibliographie_ohne_pandoc_nicht_erzeugbar(self):
        # Der Kern sucht pandoc über shutil.which; im Probe-Skript antwortet es mit None, unabhängig von der Maschine.
        self.toml("[lint]", '[export.bibliography]\npath = "build/literatur.md"\n\n[lint]')
        self.aendere("sources/q1", "date: '2026-10-25'\n",
                     "csl: {title: T, author: [{family: Muster, given: M}], issued: {date-parts: [[2020]]}, "
                     "DOI: 10.1000/x}\n")
        codes = self.py("okfw.shutil.which = lambda *a, **k: None\n"
                        "result = [f.code for f in wiki.lint() if f.path == 'build/literatur.md']")
        self.assertEqual(codes, ["projection_unavailable"])

    @unittest.skipIf(shutil.which("pandoc") is None, "pandoc fehlt: ohne pandoc ist die Gegenprobe nicht möglich")
    def test_bibliographie_mit_pandoc_erzeugbar(self):
        self.toml("[lint]", '[export.bibliography]\npath = "build/literatur.md"\n\n[lint]')
        self.aendere("sources/q1", "date: '2026-10-25'\n",
                     "csl: {title: T, author: [{family: Muster, given: M}], issued: {date-parts: [[2020]]}, "
                     "DOI: 10.1000/x}\n")
        self.core("rebuild", "--quiet")
        self.assertNotIn("projection_unavailable", self.lint_codes())


class QuellenUndRohquellen(RegelTest):
    """Quellenkarten: Datum, CSL, Kernaussagen, Rohquellen-Pakete und Volltext-Blöcke."""

    Q1 = "sources/q1"

    def test_karten(self):
        csl = "csl: {title: T, issued: {date-parts: [[2020]]}}\n"
        self.tabelle([
            ("source_undated", self.Q1, "date: '2026-10-25'\n", ""),
            ("csl_incomplete", self.Q1, "date: '2026-10-25'\n", csl),
            ("citation_with_claims", self.Q1, "access: abstract", "access: citation"),
            ("claim_on_citation_only", self.Q1, "access: abstract", "access: citation", "concepts/k1"),
        ])

    def test_citation_kernaussagen_fliesstext_ja_kasten_nein(self):
        self.aendere(self.Q1, "access: abstract", "access: citation")
        self.aendere(self.Q1, "- Aussage (Abstract)", "> [!todo] Erst nach Lektüre.\n> Zweite Zeile des Kastens.")
        self.assertNotIn("citation_with_claims", self.lint_codes(self.Q1))
        self.aendere(self.Q1, "## Relevanz", "[^x]: Fußnote am Ende eines Abschnitts\n\n## Relevanz")
        self.assertNotIn("citation_with_claims", self.lint_codes(self.Q1))
        self.aendere(self.Q1, "> Zweite Zeile des Kastens.", "> Zweite Zeile des Kastens.\n\nDie Studie zeigt X.")
        self.assertIn("citation_with_claims", self.lint_codes(self.Q1))

    def test_fussnote_samt_folgezeilen_ist_keine_aussage(self):
        self.assertEqual(self.py(r"result = okfw.claim_lines('[^a]: def\n    Fortsetzung\n')"), [])
        self.assertEqual(self.py(r"result = okfw.claim_lines('[^a]: def\n\n    Absatz\n\nAussage\n    Einzug\n')"),
                         ["Aussage", "Einzug"])

    def test_undatiert_bewusst_vermerkt(self):
        self.aendere(self.Q1, "date: '2026-10-25'", "date: o. J.")
        self.assertNotIn("source_undated", self.lint_codes(self.Q1))

    def test_csl_mit_doi_braucht_kein_abrufdatum(self):
        csl = "csl: {title: T, author: [{family: M}], issued: {date-parts: [[2020]]}, DOI: 10.1000/x}\n"
        self.aendere(self.Q1, "date: '2026-10-25'\n", csl)
        self.assertNotIn("csl_incomplete", self.lint_codes(self.Q1))
        self.aendere(self.Q1, ", DOI: 10.1000/x}", "}")
        self.assertIn("csl_incomplete", self.lint_codes(self.Q1))
        self.aendere(self.Q1, "issued: {date-parts: [[2020]]}}", "issued: {date-parts: [[2020]]}, accessed: 2026-10-25}")
        self.assertNotIn("csl_incomplete", self.lint_codes(self.Q1))

    def test_prioritaet_nur_citation(self):
        self.toml('orphans = "inbound"', 'orphans = "inbound"\npriority_field = "prio"')
        self.aendere(self.Q1, "access: abstract", "access: citation\nprio: true")
        self.assertIn("priority_citation_only", self.lint_codes(self.Q1))
        self.aendere(self.Q1, "access: citation", "access: abstract")
        self.assertNotIn("priority_citation_only", self.lint_codes(self.Q1))

    def erfasse(self, slug: str, text: str = "Rohtext\n") -> None:
        self.core("capture", slug, "--url", "https://example.org", "--file", str(self.datei(f"{slug}.md", text)),
                  "--license", "MIT")

    def paket_karte(self, slug: str) -> None:
        self.erfasse(slug)
        self.core("new", "source", slug, "--title", slug, "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", f"raw_packet=raw/{slug}", "--set", "date=2026-10-25",
                  "--fundweg", "person")

    def test_rohquelle_ohne_karte(self):
        self.erfasse("q8")
        befund = [f for f in self.befunde() if f["code"] == "raw_uningested"]
        self.assertEqual([(f["path"], f["severity"]) for f in befund], [("raw/q8", "info")])
        self.core("new", "source", "q8", "--title", "Q8", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", "raw_packet=raw/q8")
        self.assertNotIn("raw_uningested", self.lint_codes())

    def test_paket_unversehrt_dann_veraendert(self):
        self.paket_karte("q2")
        codes = self.lint_codes("sources/q2")
        self.assertFalse({"raw_missing", "raw_tampered", "fulltext_unanchored"} & set(codes), codes)
        datei = self.root / "raw" / "q2" / "original" / "q2.md"
        datei.chmod(0o644)
        datei.write_text("Heimlich geändert\n", encoding="utf-8")
        self.assertIn("raw_tampered", self.lint_codes("sources/q2"))

    def test_paket_ohne_manifest(self):
        self.paket_karte("q2")
        self.aendere("sources/q2", "raw_packet: raw/q2", "raw_packet: raw/gibtsnicht")
        self.assertIn("raw_missing", self.lint_codes("sources/q2"))

    def test_fulltext_ohne_anker(self):
        self.core("new", "source", "q3", "--title", "Q3", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", "date=2026-10-25", "--fundweg", "person")
        befund = [f for f in self.befunde("sources/q3") if f["code"] == "fulltext_unanchored"]
        self.assertEqual(len(befund), 1)
        self.assertIn("weder raw_packet noch fulltext-Block", befund[0]["message"])

    def test_fulltext_block(self):
        text = "Volltext der Quelle\n"
        pfad = self.root / "volltexte" / "q1.txt"
        pfad.parent.mkdir()
        pfad.write_text(text, encoding="utf-8")
        block = f"access: fulltext\nfulltext:\n- {{path: volltexte/q1.txt, sha256: {sha(text)}}}"
        self.aendere(self.Q1, "access: abstract", block)
        codes = set(self.lint_codes(self.Q1))
        self.assertFalse({"fulltext_tampered", "fulltext_absent", "fulltext_unanchored"} & codes, codes)
        pfad.write_text("Anderer Inhalt\n", encoding="utf-8")
        self.assertIn("fulltext_tampered", self.lint_codes(self.Q1))
        pfad.unlink()
        self.assertIn("fulltext_absent", self.lint_codes(self.Q1))
        self.aendere(self.Q1, f", sha256: {sha(text)}", "")
        self.assertIn("fulltext_unanchored", self.lint_codes(self.Q1))


class Lizenzen(RegelTest):
    """Lizenzangabe, Einordnung und Volltext-Rechte."""

    Q1 = "sources/q1"

    def test_lizenzangabe(self):
        self.tabelle([
            ("license_missing", self.Q1, "license: {name: MIT, url: null}", "license: {name: null, url: null}"),
            ("license_unclassified", self.Q1, "name: MIT", "name: Sonderlizenz XY"),
        ])

    def test_fehlende_lizenz_eine_meldung_je_profil(self):
        self.aendere(self.Q1, "license: {name: MIT, url: null}\n", "")
        lizenz = lambda: [(f["code"], f["message"]) for f in self.befunde(self.Q1)
                          if "license" in f["code"] or "license" in f["message"]]
        # Profil default: license empfohlen
        befund = lizenz()
        self.assertEqual(len(befund), 1, befund)
        [(code, meldung)] = befund
        self.assertEqual(code, "missing_recommended")
        self.assertIn("lizenz <seite> --fetch", meldung)
        # wie im Profil dokumentation: license Pflicht
        self.toml('recommended = ["license"]', 'required = ["title", "description", "resource", "access", "status", '
                  '"generated", "license"]\nrecommended = []')
        self.toml('required = ["title", "description", "resource", "access", "status", "generated"]\n', "")
        befund = lizenz()
        self.assertEqual(len(befund), 1, befund)
        [(code, meldung)] = befund
        self.assertEqual(code, "missing_required")
        self.assertIn("lizenz <seite> --fetch", meldung)
        # Profil ohne license: license_missing
        self.toml('"generated", "license"]', '"generated"]')
        self.assertEqual([c for c, _ in lizenz()], ["license_missing"])

    def test_unklassifizierte_lizenz_mit_class_behoben(self):
        self.aendere(self.Q1, "name: MIT", "name: Sonderlizenz XY")
        self.assertIn("license_unclassified", self.lint_codes(self.Q1))
        self.core("lizenz", self.Q1, "--class", "by")
        self.assertNotIn("license_unclassified", self.lint_codes(self.Q1))

    def paket(self, lizenz: str) -> None:
        datei = self.datei("q2.md", "Rohtext\n")
        self.core("capture", "q2", "--url", "https://example.org", "--file", str(datei), "--license", lizenz)
        self.core("new", "source", "q2", "--title", "Q2", "--description", "D", "--access", "fulltext",
                  "--resource", "https://example.org", "--set", "raw_packet=raw/q2", "--set", "date=2026-10-25",
                  "--fundweg", "person")

    def test_volltext_bei_ungeklaertem_recht(self):
        self.paket("MIT")
        self.assertNotIn("fulltext_rights", self.lint_codes("sources/q2"))
        self.aendere("sources/q2", "name: MIT", "name: unknown")
        self.assertIn("fulltext_rights", self.lint_codes("sources/q2"))
        self.core("lizenz", "sources/q2", "--name", "MIT", "--via", "originaldokument")
        self.assertNotIn("fulltext_rights", self.lint_codes("sources/q2"))

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=t@example.org",
                               "-c", "commit.gpgsign=false", *args], capture_output=True, text=True,
                              env=clean_env(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1"), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    @unittest.skipIf(shutil.which("git") is None, "git fehlt")
    def test_volltext_im_git_ohne_recht_zur_weitergabe(self):
        self.paket("All rights reserved")
        self.git("init", "-q")
        self.assertNotIn("fulltext_shared", self.lint_codes("sources/q2"))   # nichts versioniert
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Start")
        self.assertIn("fulltext_shared", self.lint_codes("sources/q2"))
        self.assertNotIn("fulltext_rights", self.lint_codes("sources/q2"))   # Ablegen ist erlaubt, Weitergeben nicht
        self.git("rm", "-r", "-q", "--cached", "raw")
        self.assertNotIn("fulltext_shared", self.lint_codes("sources/q2"))

    @unittest.skipIf(shutil.which("git") is None, "git fehlt")
    def test_volltext_im_git_bei_freier_lizenz_ohne_befund(self):
        self.paket("MIT")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Start")
        self.assertNotIn("fulltext_shared", self.lint_codes("sources/q2"))


class UmfangSichtbarkeitFundweg(RegelTest):
    """[scope], [visibility] und der Fundweg der Quellenkarten."""

    Q1 = "sources/q1"

    def test_umfang_fehlt(self):
        self.toml('access = ["fulltext", "abstract", "citation"]',
                  'access = ["fulltext", "abstract", "citation"]\numfang = ["kern", "vertiefung"]')
        self.toml("[lint]", '[scope]\nfield = "umfang"\ndefault = "kern"\n\n[lint]')
        befunde = [f for f in self.befunde() if f["code"] == "scope_missing"]
        self.assertEqual(sorted(f["path"] for f in befunde),
                         ["wiki/concepts/k1.md", "wiki/concepts/k2.md", "wiki/sources/q1.md"])
        self.assertIn("lässt sich nicht ableiten", [f["message"] for f in befunde if "q1" in f["path"]][0])
        for k in ("k1", "k2"):
            self.aendere(f"concepts/{k}", "status: draft\n", "status: draft\numfang: kern\n")
        # die Quelle erbt den Umfang der Konzepte, die sie nutzen
        self.assertNotIn("scope_missing", self.lint_codes())

    def test_oeffentliche_seite_verweist_auf_verborgene(self):
        self.toml("[lint]", '[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\n'
                  'hidden = ["intern"]\n\n[lint]')
        self.assertNotIn("visibility_leak", self.lint_codes())
        self.aendere("concepts/k2", "status: draft\n", "status: draft\nsichtbarkeit: intern\n")
        befund = [f for f in self.befunde() if f["code"] == "visibility_leak"]
        # k1 und die Quellenkarte q1 sind öffentlich und verweisen auf das verborgene k2
        self.assertEqual([f["path"] for f in befund], ["wiki/concepts/k1.md", "wiki/sources/q1.md"])
        self.assertIn("/concepts/k2.md", befund[0]["message"])
        self.aendere("concepts/k1", "status: draft\n", "status: draft\nsichtbarkeit: intern\n")
        self.assertNotIn("visibility_leak", self.lint_codes())

    def test_fundweg_fehlt_und_cli_setzt_ihn(self):
        self.aendere(self.Q1, lambda t: "".join(z for z in t.splitlines(True) if not z.startswith("fundweg:")))
        self.assertIn("fundweg_missing", self.lint_codes(self.Q1))
        self.core("fundweg", self.Q1, "--art", "person")
        self.assertNotIn("fundweg_missing", self.lint_codes(self.Q1))

    def test_fundweg_suche_unvollstaendig_cli_ergaenzt(self):
        self.core("fundweg", self.Q1, "--art", "suche")
        self.assertIn("fundweg_incomplete", self.lint_codes(self.Q1))
        self.core("fundweg", self.Q1, "--art", "suche", "--db", "pubmed", "--query", "okf wiki")
        self.assertNotIn("fundweg_incomplete", self.lint_codes(self.Q1))

    def test_fundweg_ungueltig(self):
        self.tabelle([
            ("fundweg_invalid", self.Q1, "art: person", "art: zufall"),
            ("fundweg_invalid", self.Q1, "art: person", "art: zitatverfolgung"),
            ("fundweg_invalid", self.Q1, "art: person", "art: zitatverfolgung, von: gibtsnicht"),
            ("fundweg_invalid", self.Q1, "art: person", "art: person, von: gibtsnicht"),
        ])

    def test_zitatverfolgung_mit_vorhandener_quelle_gueltig(self):
        self.aendere(self.Q1, "art: person", "art: zitatverfolgung, von: q1")
        self.assertNotIn("fundweg_invalid", self.lint_codes(self.Q1))


class LintVertrag(RegelTest):
    """Exit-Code, --strict, --summary und [lint.severity]."""

    def test_fehler_bricht_mit_exit_1(self):
        self.assertEqual(self.core("lint").returncode, 0)
        self.aendere("concepts/k1", "status: draft", "status: fertig")
        done = self.core("lint", ok=None)
        self.assertEqual(done.returncode, 1)
        self.assertIn("status_invalid", done.stdout)
        self.assertEqual(self.core("lint", "--json", ok=None).returncode, 1)

    def test_nur_warnungen_exit_0_streng_exit_1(self):
        self.aendere("concepts/k1", "domain: test\n", "")
        befunde = self.befunde("concepts/k1")
        self.assertEqual([(f["code"], f["severity"]) for f in befunde], [("missing_recommended", "warn")])
        self.assertEqual(self.core("lint", ok=None).returncode, 0)
        self.assertEqual(self.core("lint", "--strict", ok=None).returncode, 1)

    def test_hinweise_aendern_den_exit_code_nie(self):
        self.aendere("concepts/k1", "Aussage.[^q1]", "Aussage.[^q1]\n\n[^q1]: [Quelle q1](/sources/q1.md)")
        self.aendere("sources/q1", lambda t: "".join(z for z in t.splitlines(True) if not z.startswith("fundweg:")))
        self.assertEqual({f["severity"] for f in self.befunde()}, {"info"})
        self.assertEqual(self.core("lint", "--strict", ok=None).returncode, 0)

    def test_summary_listet_die_codes(self):
        self.aendere("concepts/k1", "status: draft", "status: fertig")
        self.aendere("concepts/k2", "domain: test\n", "")
        self.aendere("concepts/k2", "aliases: [k-zwei]\n", "")
        out = self.core("lint", "--summary", ok=None).stdout
        self.assertRegex(out, r"error\s+status_invalid\s+1\n")
        self.assertRegex(out, r"warn\s+missing_recommended\s+2\n")
        self.assertNotIn("concepts/k1.md", out)   # --summary nennt keine Seiten
        self.assertRegex(out, r"3 Seiten · 1 Fehler · 2 Warnungen · 0 Hinweise")

    def test_schwere_off_blendet_code_aus(self):
        self.aendere("concepts/k1", "domain: test\n", "")
        self.assertIn("missing_recommended", self.lint_codes("concepts/k1"))
        self.toml("[lint.severity]", '[lint.severity]\nmissing_recommended = "off"')
        self.assertNotIn("missing_recommended", self.lint_codes())
        self.assertEqual(self.core("lint", "--strict", ok=None).returncode, 0)

    def test_schwere_error_hebt_code_an(self):
        self.aendere("concepts/k1", "domain: test\n", "")
        self.toml("[lint.severity]", '[lint.severity]\nmissing_recommended = "error"')
        befund = self.befunde("concepts/k1", ok=None)
        self.assertEqual([(f["code"], f["severity"]) for f in befund], [("missing_recommended", "error")])
        self.assertEqual(self.core("lint", ok=None).returncode, 1)

    def test_schwere_warn_senkt_fehler_ab(self):
        self.aendere("concepts/k1", "status: draft", "status: fertig")
        self.toml("[lint.severity]", '[lint.severity]\nstatus_invalid = "warn"')
        self.assertEqual(self.core("lint", ok=None).returncode, 0)
        self.assertEqual(self.core("lint", "--strict", ok=None).returncode, 1)


if __name__ == "__main__":
    unittest.main()
