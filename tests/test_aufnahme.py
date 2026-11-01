"""Aufnahme einer Quelle im Kern: Rückzugsprüfung, Funde aus dem Lauf der Recherche, Fundweg mit echtem Zeitpunkt.

Kein Netz: `http_json` und `http_bytes` des Kerns sind im Probeskript durch eine Wegetabelle ersetzt
(Teil der URL → Antwort; "FEHLER" wirft OSError, unbekannte Adressen scheitern laut).
"""

import json
import time

from .hilfe import WikiTest

CROSSREF = "api.crossref.org/works/10.1/x"
ESEARCH, EFETCH, ESUMMARY = "esearch.fcgi", "efetch.fcgi", "esummary.fcgi"
NETZ = """
import contextlib, io
ROUTES = {routes!r}
def antwort(url):
    for teil, wert in ROUTES.items():
        if teil in url:
            if wert == "FEHLER":
                raise OSError("kein Netz")
            return wert
    raise AssertionError("unerwarteter Abruf: " + url)
okfw.http_json = lambda url, timeout=20, accept="application/json": antwort(url)
okfw.http_bytes = lambda url, timeout=30: antwort(url).encode("utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    rc = okfw.main(["--root", {root!r}, *{args!r}])
result = {{"rc": rc, "err": err.getvalue()}}
"""


def aufsatz(**extra) -> dict:
    return {"message": {"type": "journal-article", "title": ["Ein Aufsatz"], "DOI": "10.1/x",
                        "author": [{"family": "Muster", "given": "Max"}], "issued": {"date-parts": [[2020]]},
                        "container-title": ["Journal"], **extra}}


def pubmed_xml(*verweise: tuple[str, str, str], typen=("Journal Article",)) -> str:
    refs = "".join(f'<CommentsCorrections RefType="{typ}"><RefSource>{quelle}</RefSource><PMID>{pmid}</PMID>'
                   "</CommentsCorrections>" for typ, quelle, pmid in verweise)
    pubtypes = "".join(f"<PublicationType>{t}</PublicationType>" for t in typen)
    return ("<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><PublicationTypeList>"
            f"{pubtypes}</PublicationTypeList></Article><CommentsCorrectionsList>{refs}</CommentsCorrectionsList>"
            "</MedlineCitation></PubmedArticle></PubmedArticleSet>")


KEIN_PUBMED = {ESEARCH: {"esearchresult": {"idlist": []}}}
ESUMMARY_ANTWORT = {"result": {"123": {"title": "Ein Aufsatz.", "pubdate": "2020 Jan", "fulljournalname": "Journal",
                                       "authors": [{"name": "Muster M", "authtype": "Author"}],
                                       "articleids": [{"idtype": "doi", "value": "10.1/x"}]}}}


class Netz(WikiTest):
    def aufnehmen(self, routes: dict, *args: str) -> dict:
        return self.py(NETZ.format(routes=routes, root=str(self.root), args=["new", "source", *args]))

    def karte(self, slug: str = "q1") -> bool:
        return (self.root / "wiki" / "sources" / f"{slug}.md").exists()


class Rueckzug(Netz):
    def test_crossref_rueckzug_bricht_ab_ohne_karte(self):
        r = self.aufnehmen({CROSSREF: aufsatz(**{"updated-by": [{"type": "retraction", "DOI": "10.1/r"}]}), **KEIN_PUBMED},
                           "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 3)
        self.assertIn("retraction laut crossref updated-by, Hinweis-DOI 10.1/r", r["err"])
        self.assertIn("--trotz-rueckzug", r["err"])
        self.assertFalse(self.karte())

    def test_trotz_rueckzug_legt_mit_vermerk_an(self):
        r = self.aufnehmen({CROSSREF: aufsatz(**{"updated-by": [{"type": "retraction", "DOI": "10.1/r"}]}), **KEIN_PUBMED},
                           "q1", "--doi", "10.1/x", "--trotz-rueckzug")
        self.assertEqual(r["rc"], 0, r["err"])
        fm = self.fm("sources/q1")
        self.assertEqual(fm["rueckzug"]["ergebnis"], "zurueckgezogen")
        self.assertIs(fm["rueckzug"]["trotz"], True)
        self.assertEqual(fm["rueckzug"]["via"], ["crossref"])
        self.assertEqual(fm["rueckzug"]["hinweise"], [{"art": "retraction", "doi": "10.1/r", "quelle": "crossref updated-by"}])
        self.assertRegex(fm["rueckzug"]["geprueft"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(fm["crossref_hinweise"], ["retraction"])
        self.assertIn("⚠ Crossref meldet: retraction", self.text("sources/q1"))

    def test_ohne_befund_keiner(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        rueckzug = self.fm("sources/q1")["rueckzug"]
        self.assertEqual((rueckzug["ergebnis"], rueckzug["via"]), ("keiner", ["crossref"]))
        self.assertNotIn("hinweise", rueckzug)
        self.assertNotIn("trotz", rueckzug)

    def test_pubmed_verweise(self):
        crossref = {CROSSREF: aufsatz()}
        gefunden = {ESEARCH: {"esearchresult": {"idlist": ["123"]}}}
        for art, erwartet in (("RetractionIn", 3), ("ExpressionOfConcernIn", 3), ("ErratumIn", 0)):
            with self.subTest(art):
                r = self.aufnehmen({**crossref, **gefunden, EFETCH: pubmed_xml((art, "Nature. 2020. doi: 10.1038/abc.", "999"))},
                                   f"q-{art.lower()}", "--doi", "10.1/x")
                self.assertEqual(r["rc"], erwartet, r["err"])
                if erwartet == 3:
                    self.assertIn(f"{art} laut pubmed, Hinweis-DOI 10.1038/abc, Hinweis-PMID 999", r["err"])
                else:
                    fm = self.fm(f"sources/q-{art.lower()}")
                    self.assertEqual((fm["rueckzug"]["ergebnis"], fm["rueckzug"]["via"]), ("korrektur", ["crossref", "pubmed"]))

    def test_publikationstyp_retracted(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), ESEARCH: {"esearchresult": {"idlist": ["123"]}},
                            EFETCH: pubmed_xml(typen=("Journal Article", "Retracted Publication"))}, "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 3)
        self.assertIn("Retracted Publication laut pubmed", r["err"])

    def test_pubmed_hinweis_steht_auch_im_text(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), ESEARCH: {"esearchresult": {"idlist": ["123"]}},
                            EFETCH: pubmed_xml(("RetractionIn", "Nature", "999"))}, "q1", "--doi", "10.1/x", "--trotz-rueckzug")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertIn("⚠ PubMed meldet: RetractionIn", self.text("sources/q1"))

    def test_korrektur_bricht_nicht_ab(self):
        r = self.aufnehmen({CROSSREF: aufsatz(**{"updated-by": [{"type": "correction", "DOI": "10.1/c"}]}), **KEIN_PUBMED},
                           "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertEqual(self.fm("sources/q1")["rueckzug"]["ergebnis"], "korrektur")

    def test_die_doi_ist_selbst_ein_rueckzugshinweis(self):
        r = self.aufnehmen({CROSSREF: aufsatz(**{"update-to": [{"type": "retraction", "DOI": "10.1/orig"}]}), **KEIN_PUBMED},
                           "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 3)
        self.assertIn("diese DOI ist selbst ein Hinweis zu 10.1/orig", r["err"])

    def test_pmid_prueft_pubmed_und_crossref(self):
        routes = {ESUMMARY: ESUMMARY_ANTWORT, CROSSREF: aufsatz(**{"updated-by": [{"type": "withdrawal", "DOI": "10.1/w"}]}),
                  EFETCH: pubmed_xml()}
        r = self.aufnehmen(routes, "q1", "--pmid", "123")
        self.assertEqual(r["rc"], 3)
        self.assertIn("withdrawal laut crossref updated-by, Hinweis-DOI 10.1/w", r["err"])
        r = self.aufnehmen({**routes, EFETCH: pubmed_xml(("RetractionIn", "x", "5"))}, "q2", "--pmid", "123", "--trotz-rueckzug")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertEqual(self.fm("sources/q2")["rueckzug"]["via"], ["crossref", "pubmed"])

    def test_netzfehler_ist_nicht_geprueft_mit_warnung(self):
        routes = {ESEARCH: "FEHLER", CROSSREF: "FEHLER", EFETCH: "FEHLER"}
        r = self.py("""
okfw.Wiki.fetch_source = lambda self, doi='', pmid='', arxiv='': (
    {'type': 'article-journal', 'title': 'Ein Aufsatz', 'author': [{'family': 'Muster', 'given': 'Max'}],
     'issued': {'date-parts': [[2020]]}, 'DOI': '10.1/x'}, 'https://doi.org/10.1/x',
    {'by': 'process:crossref', 'at': '2026-01-01T00:00:00Z'}, [])
""" + NETZ.format(routes=routes, root=str(self.root), args=["new", "source", "q1", "--doi", "10.1/x"]))
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertIn("Crossref nicht erreichbar", r["err"])
        self.assertIn("PubMed nicht erreichbar", r["err"])
        self.assertIn("nicht geprüft", r["err"])
        rueckzug = self.fm("sources/q1")["rueckzug"]
        self.assertEqual((rueckzug["ergebnis"], rueckzug["via"]), ("nicht_geprueft", []))

    def test_pubmed_ausgefallen_crossref_antwortet(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), ESEARCH: "FEHLER"}, "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertIn("PubMed nicht erreichbar", r["err"])
        rueckzug = self.fm("sources/q1")["rueckzug"]
        self.assertEqual((rueckzug["ergebnis"], rueckzug["via"]), ("keiner", ["crossref"]))

    def test_crossref_kennt_die_doi_nicht_datacite(self):
        r = self.py("""
import urllib.error
def fehler404(url, timeout=20, accept="application/json"):
    if "crossref" in url:
        raise urllib.error.HTTPError(url, 404, "nicht gefunden", {}, None)
    if "datacite" in url:
        return {"type": "dataset", "title": "Daten", "author": [{"family": "A", "given": "B"}], "issued": {"date-parts": [[2021]]}}
    return {"esearchresult": {"idlist": []}}
okfw.http_json = fehler404
import contextlib, io
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    rc = okfw.main(["--root", %r, "new", "source", "q1", "--doi", "10.5281/zenodo.1"])
result = {"rc": rc, "err": err.getvalue()}
""" % str(self.root))
        self.assertEqual(r["rc"], 0, r["err"])
        rueckzug = self.fm("sources/q1")["rueckzug"]
        # weder Crossref noch PubMed kennen die DOI: „keiner“ wäre nicht belegt
        self.assertEqual((rueckzug["ergebnis"], rueckzug["via"]), ("nicht_geprueft", []))
        self.assertNotIn("Crossref nicht erreichbar", r["err"])
        self.assertIn("nicht geprüft", r["err"])

    def test_arxiv_wird_nicht_geprueft(self):
        r = self.py("""
okfw.Wiki.fetch_source = lambda self, doi='', pmid='', arxiv='': (
    {'type': 'article', 'title': 'Vorabdruck', 'author': [{'family': 'A', 'given': 'B'}], 'issued': {'date-parts': [[2021]]},
     'DOI': '10.48550/arXiv.2101.00001'}, 'https://arxiv.org/abs/2101.00001', {'by': 'process:arxiv', 'at': '2026-01-01T00:00:00Z'}, [])
def kein_netz(*a, **k):
    raise AssertionError("arXiv wird nicht auf Rückzug geprüft")
okfw.http_json = okfw.http_bytes = kein_netz
import contextlib, io
with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
    rc = okfw.main(["--root", %r, "new", "source", "q1", "--arxiv", "2101.00001"])
result = {"rc": rc}
""" % str(self.root))
        self.assertEqual(r["rc"], 0)
        self.assertNotIn("rueckzug", self.fm("sources/q1"))

    def test_trotz_rueckzug_braucht_doi_oder_pmid(self):
        done = self.core("new", "source", "q1", "--manuell", "--titel", "T", "--url", "https://example.org", "--trotz-rueckzug",
                         ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--trotz-rueckzug", done.stderr)

    def test_eigener_feldname(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[sources]\nretraction_field = "retraction_check"\n')
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        fm = self.fm("sources/q1")
        self.assertEqual(fm["retraction_check"]["ergebnis"], "keiner")
        self.assertNotIn("rueckzug", fm)
        self.assertNotIn("config_unknown", self.lint_codes())


def kandidat(nr, funde, **felder) -> dict:
    return {"nr": nr, "titel": "Ein Aufsatz", "jahr": 2020, "funde": funde,
            "wege": sorted({f["weg"] for f in funde}), "aufgeloest": True, "nur_websuche": False, "nur_modell": False, **felder}


def fund(weg, db, query, at, by, rang=1, **extra) -> dict:
    return {"weg": weg, "db": db, "query": query, "at": at, "by": by, "rang": rang, "ort": f"{weg}.jsonl:1", **extra}


class AusLauf(Netz):
    def setUp(self):
        super().setUp()
        self.lauf = self.base / "lauf"
        self.lauf.mkdir()
        (self.lauf / "kandidaten.json").write_text(json.dumps([
            kandidat(1, [fund("api", "pubmed", "qm[ti]", "2026-10-01T09:30:00Z", "claude-code/fund-api"),
                         fund("tooluniverse", "tooluniverse:PubMed_search", "qm", "2026-10-01T11:31:05+02:00",
                              "claude-code/fund-tooluniverse", 3, at_aus_auftrag=True)], doi="10.1/x", pmid="123"),
            kandidat(2, [fund("websuche", "websuche:x", "qm", "2026-10-01T09:30:00Z", "claude-code/fund-websuche")],
                     url="https://example.org/a", wege=["websuche"], aufgeloest=False, nur_websuche=True),
            kandidat(3, [fund("modellvorschlag", "modell", "Vorschlag", "2026-10-02T08:00:00Z", "claude-code/claude-opus-5-5")],
                     doi="10.1/x", nur_modell=True, wege=["modellvorschlag"]),
            kandidat(4, [fund("api", "pubmed", "qm", "2026-10-01T09:30:00Z", "claude-code/fund-api")], pmcid="PMC1"),
        ]), encoding="utf-8")

    def test_new_source_setzt_je_fund_einen_fundweg(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "1")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertIn("Startzeit laut Auftrag", r["err"])
        fm = self.fm("sources/q1")
        self.assertEqual(fm["fundweg"], [
            {"art": "suche", "db": "pubmed", "query": "qm[ti]", "at": "2026-10-01T09:30:00Z", "by": "claude-code/fund-api"},
            {"art": "suche", "db": "tooluniverse:PubMed_search", "query": "qm", "at": "2026-10-01T11:31:05+02:00",
             "by": "claude-code/fund-tooluniverse"}])
        self.assertEqual(fm["resource"], "https://doi.org/10.1/x")       # Kennung kommt vom Kandidaten
        self.assertEqual(fm["rueckzug"]["ergebnis"], "keiner")
        self.assertNotIn("fundweg_incomplete", self.lint_codes("sources/q1"))

    def test_ein_fund_ist_eine_zuordnung_und_modellvorschlag(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "3")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertEqual(self.fm("sources/q1")["fundweg"], {
            "art": "modellvorschlag", "db": "modell", "query": "Vorschlag", "at": "2026-10-02T08:00:00Z",
            "by": "claude-code/claude-opus-5-5"})

    def test_kandidat_ueber_doi_waehlen(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "https://doi.org/10.1/X")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertEqual(len(self.fm("sources/q1")["fundweg"]), 2)    # der erste Treffer mit dieser DOI

    def test_websuche_ohne_aufloesung_und_fehlende_kennung(self):
        for nr, text in (("2", "nur aus der Websuche"), ("4", "weder DOI noch PMID"), ("9", "nicht in")):
            with self.subTest(nr):
                done = self.core("new", "source", f"q{nr}", "--aus-lauf", str(self.lauf), "--kandidat", nr, ok=False)
                self.assertEqual(done.returncode, 2)
                self.assertIn(text, done.stderr)
                self.assertFalse(self.karte(f"q{nr}"))

    def test_lauf_und_kandidat_gehoeren_zusammen(self):
        self.assertEqual(self.core("new", "source", "q1", "--aus-lauf", str(self.lauf), ok=False).returncode, 2)
        self.assertEqual(self.core("new", "source", "q1", "--kandidat", "1", ok=False).returncode, 2)
        self.assertEqual(self.core("new", "source", "q1", "--aus-lauf", str(self.base / "leer"), "--kandidat", "1",
                                   ok=False).returncode, 2)

    def test_fundweg_befehl_ersetzt_und_ergaenzt(self):
        self.quelle("q2")
        done = self.core("fundweg", "sources/q2", "--aus-lauf", str(self.lauf), "--kandidat", "1")
        self.assertIn("2 Fundweg(e) aus Lauf", done.stdout)
        fundweg = self.fm("sources/q2")["fundweg"]
        self.assertEqual([f["db"] for f in fundweg], ["pubmed", "tooluniverse:PubMed_search"])
        self.assertEqual(fundweg[1]["at"], "2026-10-01T11:31:05+02:00")
        self.core("fundweg", "sources/q2", "--aus-lauf", str(self.lauf), "--kandidat", "3", "--add")
        self.assertEqual([f["art"] for f in self.fm("sources/q2")["fundweg"]], ["suche", "suche", "modellvorschlag"])
        self.core("fundweg", "sources/q2", "--aus-lauf", str(self.lauf), "--kandidat", "3")
        self.assertEqual(self.fm("sources/q2")["fundweg"]["art"], "modellvorschlag")

    def test_fundweg_befehl_regeln(self):
        self.quelle("q2")
        for args in (("--aus-lauf", str(self.lauf), "--kandidat", "1", "--art", "person"), ("--aus-lauf", str(self.lauf)),
                     ("--aus-lauf", str(self.lauf), "--kandidat", "2"), ()):
            with self.subTest(args):
                self.assertEqual(self.core("fundweg", "sources/q2", *args, ok=False).returncode, 2)


class AusLaufAbgleich(Netz):
    """Die Funde eines Kandidaten gehören zu seiner Arbeit: Kennungen vergleichen, nichts doppelt, keine falsche Art."""

    def setUp(self):
        super().setUp()
        self.lauf = self.base / "lauf"
        self.lauf.mkdir()
        zeit, von = "2026-10-03T08:00:00Z", "claude-code/fund-api"
        (self.lauf / "kandidaten.json").write_text(json.dumps([
            kandidat(1, [fund("api", "pubmed", "qm[ti]", zeit, von), fund("tooluniverse", "tu", "qm", zeit, von)],
                     doi="10.1/x", pmid="123"),
            kandidat(3, [fund("modellvorschlag", "modell", "Vorschlag", zeit, von)], doi="10.1/x"),
            kandidat(5, [fund("zitatverfolgung", "crossref", "x", zeit, von)], doi="10.1/z"),
            kandidat(6, [fund("zitatverfolgung", "crossref", "x", zeit, von, von="q0")], doi="10.1/z"),
            kandidat(7, [fund("api", "arxiv", "x", zeit, von)], arxiv="2401.00001v2"),
            kandidat(8, [fund("api", "pubmed", "x", zeit, von)], pmid="123"),
        ]), encoding="utf-8")

    def karte_mit(self, slug: str, *felder: str) -> None:
        self.core("new", "source", slug, "--title", "T", "--description", "D", "--access", "citation", *felder)

    def uebernehmen(self, slug: str, nr: str, *extra: str, ok=True):
        return self.core("fundweg", f"sources/{slug}", "--aus-lauf", str(self.lauf), "--kandidat", nr, *extra, ok=ok)

    def test_fundweg_fremde_arbeit_exit_2(self):
        self.karte_mit("q2", "--resource", "https://doi.org/10.9/anders")
        vorher = self.text("sources/q2")
        done = self.uebernehmen("q2", "1", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertIn("andere Arbeit", done.stderr)
        self.assertIn("10.1/x", done.stderr)
        self.assertEqual(self.text("sources/q2"), vorher)

    def test_fundweg_passende_kennung_auch_anders_geschrieben(self):
        self.karte_mit("q2", "--resource", "https://doi.org/10.1/X")
        done = self.uebernehmen("q2", "1")
        self.assertNotIn("Zuordnung nicht geprüft", done.stderr)
        self.assertEqual(len(self.fm("sources/q2")["fundweg"]), 2)
        self.karte_mit("q3", "--resource", "https://arxiv.org/abs/2401.00001")
        self.uebernehmen("q3", "7")
        self.karte_mit("q4", "--set", "csl={type: report, title: T, DOI: '10.48550/arXiv.2401.00001v1'}")
        self.uebernehmen("q4", "7")
        self.karte_mit("q5", "--set", "pmid=999")
        self.assertEqual(self.uebernehmen("q5", "8", ok=False).returncode, 2)
        self.karte_mit("q6", "--resource", "https://pubmed.ncbi.nlm.nih.gov/123/")
        self.uebernehmen("q6", "8")

    def test_fundweg_eine_passende_kennung_genuegt(self):
        self.karte_mit("q2", "--set", "csl={type: report, title: T, DOI: 10.9/preprint, PMID: '123'}")
        self.uebernehmen("q2", "1")
        self.assertEqual(len(self.fm("sources/q2")["fundweg"]), 2)

    def test_fundweg_ohne_vergleichbare_kennung_ist_ein_hinweis(self):
        self.quelle("q2")
        done = self.uebernehmen("q2", "1")
        self.assertIn("Zuordnung nicht geprüft", done.stderr)
        self.assertEqual(len(self.fm("sources/q2")["fundweg"]), 2)

    def test_new_fremde_kennung_exit_2_ohne_abruf(self):
        for args in (("--doi", "10.9/anders"), ("--pmid", "999")):
            with self.subTest(args):
                r = self.aufnehmen({}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "1", *args)   # jeder Abruf schlüge fehl
                self.assertEqual(r["rc"], 2, r["err"])
                self.assertIn("andere Arbeit", r["err"])
                self.assertFalse(self.karte("q1"))

    def test_new_fremde_kennung_aus_dem_abruf(self):
        r = self.aufnehmen({ESUMMARY: ESUMMARY_ANTWORT, CROSSREF: aufsatz(), EFETCH: pubmed_xml()},
                           "q1", "--aus-lauf", str(self.lauf), "--kandidat", "6", "--pmid", "123")
        self.assertEqual(r["rc"], 2, r["err"])
        self.assertIn("10.1/z", r["err"])
        self.assertFalse(self.karte("q1"))

    def test_new_passende_kennung_und_csl(self):
        r = self.aufnehmen({CROSSREF: aufsatz(), **KEIN_PUBMED}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertNotIn("Zuordnung nicht geprüft", r["err"])
        fremd = self.datei("fremd.json", json.dumps({"type": "report", "title": "T", "DOI": "10.9/anders"}))
        done = self.core("new", "source", "q2", "--csl", str(fremd), "--aus-lauf", str(self.lauf), "--kandidat", "1", ok=False)
        self.assertEqual(done.returncode, 2)
        self.assertFalse(self.karte("q2"))
        gleich = self.datei("gleich.json", json.dumps({"type": "report", "title": "T"}))
        done = self.core("new", "source", "q3", "--csl", str(gleich), "--aus-lauf", str(self.lauf), "--kandidat", "1")
        self.assertIn("Zuordnung nicht geprüft", done.stderr)

    def test_add_haengt_denselben_eintrag_nicht_doppelt_an(self):
        self.quelle("q2")
        self.uebernehmen("q2", "1", "--add")
        ereignisse = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").count("Fundweg")
        done = self.uebernehmen("q2", "1", "--add")
        self.assertIn("standen schon da", done.stdout)
        self.assertEqual(len(self.fm("sources/q2")["fundweg"]), 2)
        self.assertEqual((self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").count("Fundweg"), ereignisse)
        self.core("fundweg", "sources/q2", "--art", "person", "--at", "2026-10-01T09:30:00Z", "--by", "human:t", "--add")
        self.core("fundweg", "sources/q2", "--art", "person", "--at", "2026-10-01T09:30:00Z", "--by", "human:t", "--add")
        self.assertEqual(len(self.fm("sources/q2")["fundweg"]), 3)

    def test_add_nimmt_neue_auf(self):
        self.core("new", "source", "q2", "--title", "T", "--description", "D", "--access", "citation",
                  "--resource", "https://doi.org/10.1/x")
        self.uebernehmen("q2", "1", "--add")
        done = self.uebernehmen("q2", "3", "--add")
        self.assertIn("1 Fundweg(e)", done.stdout)
        self.assertEqual([f["art"] for f in self.fm("sources/q2")["fundweg"]], ["suche", "suche", "modellvorschlag"])

    def test_zitatverfolgung_ohne_von_klare_meldung(self):
        r = self.aufnehmen({}, "q1", "--aus-lauf", str(self.lauf), "--kandidat", "5")
        self.assertEqual(r["rc"], 2, r["err"])
        self.assertIn("zitatverfolgung ohne von", r["err"])
        self.assertFalse(self.karte("q1"))
        self.quelle("q2")
        self.assertIn("zitatverfolgung ohne von", self.uebernehmen("q2", "5", ok=False).stderr)
        self.assertNotIn("fundweg", self.fm("sources/q2"))

    def test_zitatverfolgung_mit_von_behaelt_art_und_von(self):
        self.quelle("q2")
        self.uebernehmen("q2", "6")
        fundweg = self.fm("sources/q2")["fundweg"]
        self.assertEqual((fundweg["art"], fundweg["von"]), ("zitatverfolgung", "q0"))


class Rueckzugsnotiz(Netz):
    """Die DOI ist selbst der Hinweis auf eine andere Arbeit (`update-to`)."""

    def aufnehmen_notiz(self, **crossref) -> dict:
        r = self.aufnehmen({CROSSREF: aufsatz(**crossref), **KEIN_PUBMED}, "q1", "--doi", "10.1/x", "--trotz-rueckzug")
        self.assertEqual(r["rc"], 0, r["err"])
        return self.fm("sources/q1")

    def test_rueckzugsnotiz(self):
        fm = self.aufnehmen_notiz(**{"update-to": [{"type": "retraction", "DOI": "10.1/orig"}]})
        self.assertEqual(fm["rueckzug"]["ergebnis"], "rueckzugsnotiz")
        self.assertIs(fm["rueckzug"]["trotz"], True)
        self.assertRegex(self.text("sources/q1"), r"⚠ Crossref meldet: Diese Karte ist selbst ein Hinweis \(retraction\) zu 10\.1/orig")

    def test_bedenkennotiz(self):
        fm = self.aufnehmen_notiz(**{"update-to": [{"type": "expression_of_concern", "DOI": "10.1/orig"}]})
        self.assertEqual(fm["rueckzug"]["ergebnis"], "bedenkennotiz")
        self.assertIn("⚠ Crossref meldet: Diese Karte ist selbst ein Hinweis (expression_of_concern)", self.text("sources/q1"))

    def test_selbst_zurueckgezogen_geht_vor(self):
        fm = self.aufnehmen_notiz(**{"update-to": [{"type": "retraction", "DOI": "10.1/orig"}],
                                     "updated-by": [{"type": "retraction", "DOI": "10.1/r"}]})
        self.assertEqual(fm["rueckzug"]["ergebnis"], "zurueckgezogen")
        text = self.text("sources/q1")
        self.assertIn("⚠ Crossref meldet: retraction", text)
        self.assertIn("selbst ein Hinweis (retraction) zu 10.1/orig", text)

    def test_korrekturhinweis_bricht_nicht_ab_und_hat_die_zeile(self):
        r = self.aufnehmen({CROSSREF: aufsatz(**{"update-to": [{"type": "correction", "DOI": "10.1/orig"}]}), **KEIN_PUBMED},
                           "q1", "--doi", "10.1/x")
        self.assertEqual(r["rc"], 0, r["err"])
        self.assertEqual(self.fm("sources/q1")["rueckzug"]["ergebnis"], "korrektur")
        self.assertIn("selbst ein Hinweis (correction) zu 10.1/orig", self.text("sources/q1"))


class FundwegMitZeitpunkt(Netz):
    def test_new_source_mit_at_und_by(self):
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "citation", "--resource",
                  "https://example.org/q1", "--fundweg", "suche", "--db", "pubmed", "--query", "qm",
                  "--at", "2026-10-01T09:30:00Z", "--by", "claude-code/fund-api")
        self.assertEqual(self.fm("sources/q1")["fundweg"], {"art": "suche", "db": "pubmed", "query": "qm",
                                                             "at": "2026-10-01T09:30:00Z", "by": "claude-code/fund-api"})

    def test_ohne_at_gilt_jetzt(self):
        self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--access", "citation", "--resource",
                  "https://example.org/q1", "--fundweg", "person")
        at = self.fm("sources/q1")["fundweg"]["at"]
        self.assertAlmostEqual(time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone, time.time(), delta=300)

    def test_at_und_by_nur_mit_fundweg_und_gueltig(self):
        for args in (("--at", "2026-10-01T09:30:00Z"), ("--by", "claude-code/fund-api")):
            done = self.core("new", "source", "q1", "--title", "Q1", "--description", "D", *args, ok=False)
            self.assertEqual(done.returncode, 2)
            self.assertIn("--fundweg", done.stderr)
        for args in (("--at", "gestern"), ("--by", "irgendwer")):
            done = self.core("new", "source", "q1", "--title", "Q1", "--description", "D", "--fundweg", "person", *args, ok=False)
            self.assertEqual(done.returncode, 2)
        self.assertFalse(self.karte())


class BodyZiehtGeneratedNach(WikiTest):
    def test_body_setzt_generated_at_neu(self):
        self.quelle("q1")
        self.py("wiki._write_frontmatter(wiki.page_path('sources/q1'), "
                "{'generated': {'by': 'claude-code/alt', 'at': '2020-01-01T00:00:00Z', 'extra': 'bleibt'}})")
        self.core("body", "sources/q1", "-", stdin="# Neu\n\nInhalt.\n")
        generated = self.fm("sources/q1")["generated"]
        self.assertGreater(generated["at"], "2026-01-01T00:00:00Z")
        self.assertNotEqual(generated["by"], "claude-code/alt")
        self.assertEqual(generated["extra"], "bleibt")

    def test_gleicher_inhalt_aendert_nichts(self):
        self.quelle("q1")
        self.core("body", "sources/q1", "-", stdin="# Neu\n\nInhalt.\n")
        vorher = self.fm("sources/q1")["generated"]
        done = self.core("body", "sources/q1", "-", stdin="# Neu\n\nInhalt.\n")
        self.assertIn("unverändert", done.stdout)
        self.assertEqual(self.fm("sources/q1")["generated"], vorher)

    def test_nachziehen_nach_edit(self):
        self.quelle("q1")
        pfad = self.root / "wiki" / "sources" / "q1.md"
        pfad.write_text(pfad.read_text(encoding="utf-8") + "\nNachtrag.\n", encoding="utf-8")
        self.py("wiki._write_frontmatter(wiki.page_path('sources/q1'), {'generated': {'by': 'x/y', 'at': '2020-01-01T00:00:00Z'}})")
        self.core("body", "sources/q1", "--nachziehen")
        self.assertGreater(self.fm("sources/q1")["generated"]["at"], "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    import unittest
    unittest.main()
