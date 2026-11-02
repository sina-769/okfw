"""api.py (Zugangsweg 2 der Recherche): Wiederholungen, Schlüssel, seitenweiser Abruf, Pfade.

Kein Netz: Die Abrufschicht ist ersetzt, `hole` für die Dienste, `_OPENER.open` für die Wiederholungen.
"""

import argparse
import gzip
import http.client
import importlib.util
import io
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PFAD = Path(__file__).resolve().parent.parent / "skills" / "recherche" / "scripts" / "api.py"
_spec = importlib.util.spec_from_file_location("okfw_api", PFAD)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

SCHLUESSEL = {"NCBI_API_KEY": "ncbi-geheim-4711", "OPENALEX_API_KEY": "oa-geheim-4711", "S2_API_KEY": "s2-geheim-4711"}
EUTILS_HOST = "eutils.ncbi.nlm.nih.gov"


def umgebung(**gesetzt):
    """Umgebung ohne die Schlüssel des Rechners, mit den angegebenen."""
    rest = {k: v for k, v in os.environ.items() if k not in api.GEHEIM}
    return mock.patch.dict(os.environ, {**rest, **gesetzt}, clear=True)


class Antwort:
    def __init__(self, daten=b"{}", fehler=None):
        self.daten, self.fehler = daten, fehler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=None):
        if self.fehler:
            raise self.fehler
        return self.daten if n is None else self.daten[:n]


def http_fehler(code, **kopf):
    return urllib.error.HTTPError("https://api.openalex.org/works", code, "x", kopf, None)


def abfrage(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class Grundtest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="okfw-api-")
        self.addCleanup(self._tmp.cleanup)
        self.lauf = Path(self._tmp.name) / "lauf"
        self.pausen: list[float] = []
        p = mock.patch.object(api.time, "sleep", self.pausen.append)
        p.start()
        self.addCleanup(p.stop)
        api._zuletzt.clear()

    def suche(self, dienst, query="x", n=20):
        a = argparse.Namespace(dienst=dienst, query=query, lauf=str(self.lauf), max=n, by="process:test")
        with redirect_stdout(io.StringIO()) as ausgabe:
            rc = api.cmd_suche(a)
        zeilen = [json.loads(z) for z in (self.lauf / "api.jsonl").read_text(encoding="utf-8").splitlines()]
        return rc, zeilen[-1], ausgabe.getvalue()


REDE = {"id": "1", "aktivitaetsart": "Rede", "typ": "Aktivität", "dokumentart": "Plenarprotokoll", "wahlperiode": 21,
        "datum": "2026-10-15", "titel": "Erika Beispiel, MdB, Fraktion", "person_id": "99",
        "fundstelle": {"id": "7", "dokumentart": "Plenarprotokoll", "herausgeber": "BT", "dokumentnummer": "21/34",
                       "seite": "3456B", "pdf_url": "https://dserver.bundestag.de/btp/21/21034.pdf#P.3456",
                       "xml_url": "https://dserver.bundestag.de/btp/21/21034.xml"},
        "vorgangsbezug": [{"id": "5", "titel": "Haushalt", "vorgangstyp": "Gesetzgebung", "vorgangsposition": "1. Beratung"}]}
DIP_KEY = "dip-geheim-4711"


class Dip(Grundtest):
    def setUp(self):
        super().setUp()
        api._aus_schluesselbund.clear()
        u = umgebung(DIP_API_KEY=DIP_KEY)
        u.start()
        self.addCleanup(u.stop)

    def namespace(self, **k):
        return argparse.Namespace(lauf=str(self.lauf), by="process:test", **k)

    def protokoll(self, lauf=None):
        return [json.loads(z) for z in ((lauf or self.lauf) / "api.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_schluessel_nur_im_kopf(self):
        with mock.patch.object(api._OPENER, "open", return_value=Antwort(json.dumps(
                {"numFound": 1, "cursor": "a", "documents": [REDE]}).encode())) as o:
            rc, e, ausgabe = self.suche("dip", "aktivitaet?f.person_id=99")
        anfrage = o.call_args[0][0]
        self.assertEqual(rc, 0)
        self.assertEqual(anfrage.get_header("Authorization"), f"ApiKey {DIP_KEY}")
        self.assertNotIn(DIP_KEY, anfrage.full_url)
        self.assertNotIn(DIP_KEY, json.dumps(e) + ausgabe)
        self.assertEqual(e["query"], "aktivitaet?f.person_id=99")
        self.assertEqual(e["db"], "api:dip")

    def test_schluesselbund_haelt_den_aufruf_nicht_an(self):
        """Gefunden in der Prüfung zu 0.7.1: Ohne Zeitlimit wartete der Aufruf endlos auf eine Freigabe im Schlüsselbund."""
        with mock.patch.dict(api.os.environ), mock.patch.object(api.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(api.subprocess, "run", side_effect=api.subprocess.TimeoutExpired("x", 60)) as lauf:
            api.os.environ.pop("DIP_API_KEY")
            rc, e, _ = self.suche("dip", "aktivitaet?f.person_id=99")
        self.assertEqual(rc, 2)
        self.assertIn("timed out", e["fehler"])
        self.assertEqual(lauf.call_args.kwargs["timeout"], 60)

    def fremdes_ziel(self) -> Path:
        self.lauf.mkdir()
        fremd = Path(self._tmp.name) / "fremd"
        fremd.mkdir()
        return fremd

    def test_text_nicht_durch_symlink_im_lauf(self):
        """Gefunden in der Prüfung zu 0.7.1: `LAUF/text` als Link aus einem fremden Wiki lenkte den Text hinaus."""
        fremd = self.fremdes_ziel()
        (self.lauf / "text").symlink_to(fremd)
        a = self.namespace(art="plenarprotokoll", kennung="21/34", out=str(self.lauf / "text" / "a.txt"))
        antwort = {"numFound": 1, "documents": [{"id": "1", "titel": "T", "text": "Wortlaut"}]}
        with mock.patch.object(api, "hole_dip", return_value=antwort), self.assertRaisesRegex(SystemExit, "symbolischer Link"):
            api.cmd_dip_dokument(a)
        self.assertEqual(list(fremd.iterdir()), [])

    def test_nachschlagen_nicht_durch_symlink(self):
        fremd = self.fremdes_ziel()
        (self.lauf / "nachschlagen").symlink_to(fremd)
        a = self.namespace(person_id=None, name="Beispiel Erika", von=None, bis=None, art=None, filter=[], max=5)
        with mock.patch.object(api, "hole_dip") as h, self.assertRaisesRegex(SystemExit, "symbolischer Link"):
            api.cmd_dip_person(a)
        h.assert_not_called()
        self.assertEqual(list(fremd.iterdir()), [])

    def test_archiv_folgt_keinem_symlink_als_nachschlagen(self):
        fremd = self.fremdes_ziel()
        (fremd / "fremd.json").write_text("{}", encoding="utf-8")
        (self.lauf / "nachschlagen").symlink_to(fremd)
        ablage = Path(self._tmp.name) / "ablage"
        with mock.patch.object(api, "protokoll_ziel", return_value=ablage), redirect_stdout(io.StringIO()):
            api.cmd_archivieren(argparse.Namespace(lauf=str(self.lauf)))
        self.assertEqual(list(ablage.rglob("*.json")), [])

    def test_treffer_fehlt_trotz_gemeldeter_person(self):
        a = self.namespace(person_id=None, name="Beispiel Erika", von=None, bis=None, art=None, filter=[], max=5)
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 1, "documents": []}), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(api.cmd_dip_person(a), 1)

    def test_treffer_mit_fundstelle(self):
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 1, "cursor": "a", "documents": [REDE]}):
            _, e, _ = self.suche("dip", "aktivitaet?f.person_id=99")
        t = e["treffer"][0]
        self.assertEqual((t["dip"], t["typ"], t["datum"], t["jahr"]), ("aktivitaet/1", "Rede", "2026-10-15", 2026))
        self.assertEqual(t["fundstelle"], "BT-Plenarprotokoll 21/34, S. 3456B")
        self.assertEqual(t["quelle"], "Deutscher Bundestag/Bundesrat – DIP")
        self.assertEqual(t["url"], REDE["fundstelle"]["pdf_url"])
        self.assertEqual(t["xml"], REDE["fundstelle"]["xml_url"])

    def test_folgeseiten_bis_der_cursor_steht(self):
        folge = [{"numFound": 4, "cursor": "a", "documents": [REDE, REDE]},
                 {"numFound": 4, "cursor": "b", "documents": [REDE]}, {"numFound": 4, "cursor": "b", "documents": []}]
        with mock.patch.object(api, "hole_dip", side_effect=folge) as h:
            _, e, _ = self.suche("dip", "aktivitaet?f.person_id=99")
        self.assertEqual(e["n_geliefert"], 3)
        self.assertEqual(h.call_count, 3)
        self.assertNotIn("cursor", abfrage(h.call_args_list[0][0][0]))
        self.assertEqual(abfrage(h.call_args_list[1][0][0])["cursor"], "a")
        self.assertEqual(e["url"], h.call_args_list[0][0][0])

    def test_nur_filter_und_listen(self):
        for q in ("aktivitaet?apikey=x", "aktivitaet?cursor=x", "aktivitaet?format=xml", "plenarprotokoll-text",
                  "../person", "person/1", "aktivitaet?f.person"):
            with mock.patch.object(api, "hole_dip") as h:
                rc, e, _ = self.suche("dip", q)
            self.assertEqual(rc, 2, q)
            self.assertIn("ValueError", e["fehler"], q)
            h.assert_not_called()
        self.assertNotIn("x&", self.protokoll()[0]["query"])
        self.assertIn("***", self.protokoll()[0]["query"])

    def test_ohne_schluessel_fehler_im_protokoll(self):
        with umgebung(), mock.patch.object(api.subprocess, "run", return_value=mock.Mock(returncode=44, stdout="")), \
                mock.patch.object(api._OPENER, "open") as o:
            rc, e, _ = self.suche("dip", "person?f.person=Beispiel")
        self.assertEqual(rc, 2)
        self.assertIn("DIP-API-Key fehlt", e["fehler"])
        o.assert_not_called()

    def test_schluesselbund_wird_geschwaerzt(self):
        wert = "bund-geheim-0815"
        def antwort(*_a, **_k):
            raise urllib.error.HTTPError("https://search.dip.bundestag.de/", 400, "x", {}, io.BytesIO(f"kaputt {wert}".encode()))
        with umgebung(), mock.patch.object(api.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=wert + "\n")), \
                mock.patch.object(api.shutil, "which", return_value="/usr/bin/security"), \
                mock.patch.object(api._OPENER, "open", side_effect=antwort) as o:
            _, e, ausgabe = self.suche("dip", "person?f.person=Beispiel")
        self.assertEqual(o.call_args[0][0].get_header("Authorization"), f"ApiKey {wert}")
        self.assertNotIn(wert, json.dumps(e) + ausgabe)
        self.assertIn("***", e["fehler"])

    def test_schluessel_geht_nicht_an_andere_hosts(self):
        self.assertIn("authorization", [t for t in api.KOPF_NUR_SELBER_HOST])
        with self.assertRaises(api.Abgelehnt):
            api.pruefe_host("https://search.dip.bundestag.de.evil.example/api/v1/person")

    def test_person_und_zeitraum_mit_artfilter(self):
        antrag = {**REDE, "id": "2", "aktivitaetsart": "Antrag"}
        a = self.namespace(person_id=99, name=None, von="2026-10-01", bis="2026-10-31", art="Rede", filter=["f.zuordnung=BT"], max=20)
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 2, "cursor": "a", "documents": [antrag, REDE]}) as h, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_dip_person(a), 0)
        e = self.protokoll()[-1]
        self.assertEqual(abfrage(h.call_args[0][0]), {"f.person_id": "99", "f.datum.start": "2026-10-01",
                                                      "f.datum.end": "2026-10-31", "f.zuordnung": "BT", "format": "json"})
        self.assertEqual(e["query"], "aktivitaet?f.person_id=99&f.datum.start=2026-10-01&f.datum.end=2026-10-31&f.zuordnung=BT")
        self.assertEqual([t["dip"] for t in e["treffer"]], ["aktivitaet/1"])
        self.assertEqual((e["n_gesamt"], e["n_geliefert"]), (2, 1))
        self.assertEqual(e["parameter"]["art_hier_gefiltert"], "Rede")
        self.assertEqual(e["parameter"]["n_gesichtet"], 2)

    def test_name_muss_genau_eine_person_treffen(self):
        person = {"id": "99", "titel": "Erika Beispiel, MdB", "typ": "Person", "wahlperiode": [20, 21], "datum": "2026-10-15"}
        a = self.namespace(person_id=None, name="Beispiel", von=None, bis=None, art=None, filter=[], max=20)
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 2, "cursor": "a", "documents": [person, person]}) as h, \
                redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(api.cmd_dip_person(a), 1)
        h.assert_called_once()
        self.assertFalse((self.lauf / "api.jsonl").exists())
        self.assertEqual(self.protokoll(self.lauf / "nachschlagen")[0]["n_gesamt"], 2)
        folge = [{"numFound": 1, "cursor": "a", "documents": [person]}, {"numFound": 1, "cursor": "a", "documents": [REDE]}]
        with mock.patch.object(api, "hole_dip", side_effect=folge), redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_dip_person(a), 0)
        self.assertEqual(self.protokoll()[-1]["query"], "aktivitaet?f.person_id=99")

    def test_reden_einer_person_bleiben_getrennte_kandidaten(self):
        zweite = {**REDE, "id": "2", "fundstelle": {**REDE["fundstelle"], "seite": "3470A"}}
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 2, "cursor": "a", "documents": [REDE, zweite]}):
            self.suche("dip", "aktivitaet?f.person_id=99")
        kandidaten, fehler = api.zusammenfuehren(self.lauf)
        self.assertEqual(fehler, [])
        self.assertEqual([k["dip"] for k in kandidaten], ["aktivitaet/1", "aktivitaet/2"])
        self.assertEqual(kandidaten[1]["fundstelle"], "BT-Plenarprotokoll 21/34, S. 3470A")

    def dokument(self, kennung, antwort, art="plenarprotokoll"):
        ziel = self.lauf.parent / "text.txt"
        a = self.namespace(art=art, kennung=kennung, out=str(ziel))
        with mock.patch.object(api, "hole_dip", return_value=antwort) as h, redirect_stdout(io.StringIO()):
            rc = api.cmd_dip_dokument(a)
        return rc, h, ziel, self.protokoll()[-1]

    def test_dokument_ueber_id(self):
        d = {**REDE, "id": "7", "titel": "Protokoll", "fundstelle": {k: v for k, v in REDE["fundstelle"].items() if k != "seite"},
             "pdf_hash": "abc", "text": "Wortlaut  bleibt\nunverändert"}
        rc, h, ziel, e = self.dokument("7", d)
        self.assertEqual(rc, 0)
        self.assertTrue(h.call_args[0][0].endswith("/plenarprotokoll-text/7?format=json"))
        self.assertIn("Wortlaut  bleibt\nunverändert", ziel.read_text(encoding="utf-8"))
        self.assertNotIn("Wortlaut", json.dumps(e))
        self.assertEqual(e["treffer"][0]["dip"], "plenarprotokoll/7")
        self.assertEqual(e["parameter"]["zeichen"], 28)
        self.assertEqual(len(e["parameter"]["sha256_text"]), 64)

    def test_dokumentnummer_muss_eindeutig_sein(self):
        d = {**REDE, "id": "7", "text": "x"}
        rc, h, ziel, e = self.dokument("21/34", {"numFound": 2, "cursor": "a", "documents": [d, {**d, "id": "8"}]}, art="drucksache")
        self.assertEqual(rc, 2)
        self.assertEqual(abfrage(h.call_args[0][0])["f.dokumentnummer"], "21/34")
        self.assertIn("/drucksache-text?", h.call_args[0][0])
        self.assertFalse(ziel.exists())
        self.assertEqual(len(e["treffer"]), 2)

    def test_dokument_ohne_text_ist_fehler(self):
        rc, _, ziel, e = self.dokument("7", {**REDE, "id": "7"})
        self.assertEqual(rc, 2)
        self.assertIn("keinen Volltext", e["fehler"])
        self.assertFalse(ziel.exists())

    def test_vorlaeufiger_text_wird_benannt(self):
        d = {**REDE, "id": "7", "text": f"Rede\n{api.DIP_VORLAEUFIG} der Sitzung wird später veröffentlicht.\n"}
        with mock.patch("sys.stderr", io.StringIO()) as fehler:
            rc, _, ziel, e = self.dokument("7", d)
        self.assertEqual(rc, 0)
        self.assertTrue(e["parameter"]["vorlaeufig"])
        self.assertIn("ACHTUNG", ziel.read_text(encoding="utf-8"))
        self.assertIn("vorläufig", fehler.getvalue())
        _, _, ziel, e = self.dokument("7", {**REDE, "id": "7", "text": "Rede"})
        self.assertNotIn("vorlaeufig", e["parameter"])
        self.assertNotIn("ACHTUNG", ziel.read_text(encoding="utf-8"))

    def test_geholter_text_ist_kein_kandidat(self):
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 1, "cursor": "a", "documents": [REDE]}):
            self.suche("dip", "aktivitaet?f.person_id=99")
        self.dokument("7", {**REDE, "id": "7", "text": "Wortlaut"})
        self.assertEqual([k["dip"] for k in api.zusammenfuehren(self.lauf)[0]], ["aktivitaet/1"])

    def test_archiv_enthaelt_nachschlagen(self):
        person = {"id": "99", "titel": "Erika Beispiel, MdB", "typ": "Person"}
        a = self.namespace(person_id=None, name="Beispiel", von=None, bis=None, art=None, filter=[], max=20)
        with mock.patch.object(api, "hole_dip", return_value={"numFound": 1, "cursor": "a", "documents": [person]}), \
                redirect_stdout(io.StringIO()):
            api.cmd_dip_person(a)
        ziel = self.lauf.parent / "archiv"
        with mock.patch.object(api, "protokoll_ziel", return_value=ziel), redirect_stdout(io.StringIO()):
            api.cmd_archivieren(argparse.Namespace(lauf=str(self.lauf)))
        self.assertTrue((ziel / "api.jsonl").is_file())
        self.assertTrue((ziel / "nachschlagen" / "api.jsonl").is_file())

    def test_systemwurzeln_schwaechen_die_pruefung_nicht(self):
        with mock.patch.object(api.sys, "platform", "darwin"), mock.patch.object(api.shutil, "which", return_value="/usr/bin/security"), \
                mock.patch.object(api.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="kein Zertifikat")):
            k = api.tls_kontext()
        self.assertEqual(k.verify_mode, api.ssl.CERT_REQUIRED)
        self.assertTrue(k.check_hostname)
        with mock.patch.object(api.sys, "platform", "darwin"), mock.patch.object(api.shutil, "which", return_value="/usr/bin/security"), \
                mock.patch.object(api.subprocess, "run", side_effect=OSError("kein security")):
            self.assertEqual(api.tls_kontext().verify_mode, api.ssl.CERT_REQUIRED)

    def test_dokument_kennung_wird_geprueft(self):
        a = self.namespace(art="drucksache", kennung="../person/1", out=str(self.lauf.parent / "t.txt"))
        with self.assertRaises(SystemExit), mock.patch.object(api, "hole_dip") as h:
            api.cmd_dip_dokument(a)
        h.assert_not_called()


class Wiederholung(Grundtest):
    def test_incompleteread_einmal_wiederholt(self):
        folge = [Antwort(fehler=http.client.IncompleteRead(b"abc", 10)), Antwort(b'{"ok": 1}')]
        with mock.patch.object(api._OPENER, "open", side_effect=folge) as o:
            self.assertEqual(api.hole_json("https://api.openalex.org/works"), {"ok": 1})
        self.assertEqual(o.call_count, 2)

    def test_abbruch_dauerhaft_ist_fehler_im_protokoll(self):
        fehler = http.client.IncompleteRead(b"abc", 10)
        with mock.patch.object(api._OPENER, "open", side_effect=lambda *a, **k: Antwort(fehler=fehler)) as o:
            rc, e, _ = self.suche("openalex")
        self.assertEqual(o.call_count, 1 + api.ABBRUCH_WIEDERHOLUNGEN)
        self.assertEqual(rc, 2)
        self.assertIn("IncompleteRead", e["fehler"])
        self.assertEqual((e["n_geliefert"], e["treffer"]), (0, []))

    def test_gzip_wird_entpackt(self):
        antwort = Antwort(gzip.compress(b'{"ok": 1}'))
        antwort.headers = {"Content-Encoding": "gzip"}
        with mock.patch.object(api._OPENER, "open", return_value=antwort) as o:
            self.assertEqual(api.hole_json("https://api.openalex.org/works"), {"ok": 1})
        self.assertEqual(o.call_args[0][0].get_header("Accept-encoding"), "gzip")

    def test_verbindung_zurueckgesetzt(self):
        folge = [ConnectionResetError("reset"), Antwort(b"[]")]
        with mock.patch.object(api._OPENER, "open", side_effect=folge):
            self.assertEqual(api.hole_json("https://api.crossref.org/works"), [])

    def test_429_mit_retry_after(self):
        folge = [http_fehler(429, **{"Retry-After": "7"}), Antwort(b'{"ok": 1}')]
        with mock.patch.object(api._OPENER, "open", side_effect=folge) as o:
            self.assertEqual(api.hole_json("https://api.openalex.org/works"), {"ok": 1})
        self.assertEqual(o.call_count, 2)
        self.assertIn(7, self.pausen)

    def test_5xx_ohne_retry_after_mit_pause(self):
        folge = [http_fehler(503), Antwort(b'{"ok": 1}')]
        with mock.patch.object(api._OPENER, "open", side_effect=folge):
            api.hole_json("https://api.openalex.org/works")
        self.assertIn(3.0, self.pausen)

    def test_retry_after_gedeckelt(self):
        folge = [http_fehler(429, **{"Retry-After": "9999"}), Antwort(b"{}")]
        with mock.patch.object(api._OPENER, "open", side_effect=folge):
            api.hole_json("https://api.openalex.org/works")
        self.assertIn(30, self.pausen)
        self.assertNotIn(9999, self.pausen)

    def test_429_bleibt_ein_fehler_nach_den_wiederholungen(self):
        def immer_429(*a, **k):
            raise http_fehler(429)
        with mock.patch.object(api._OPENER, "open", side_effect=immer_429) as o:
            rc, e, _ = self.suche("openalex")
        self.assertEqual(o.call_count, 1 + api.HTTP_WIEDERHOLUNGEN)
        self.assertEqual(rc, 2)
        self.assertIn("429", e["fehler"])

    def test_404_wird_nicht_wiederholt(self):
        with mock.patch.object(api._OPENER, "open", side_effect=http_fehler(404)) as o:
            with self.assertRaises(urllib.error.HTTPError):
                api.hole("https://api.openalex.org/works")
        self.assertEqual(o.call_count, 1)

    def test_fremder_host_abgelehnt(self):
        with self.assertRaises(api.Abgelehnt):
            api.hole("https://example.org/")


class Schluessel(Grundtest):
    def test_abstand_mit_und_ohne_ncbi_schluessel(self):
        with umgebung():
            self.assertEqual(api.abstand(EUTILS_HOST), 0.34)
        with umgebung(NCBI_API_KEY="k" * 10):
            self.assertEqual(api.abstand(EUTILS_HOST), 0.11)
            self.assertEqual(api.abstand("www.ebi.ac.uk"), 0.1)

    def pubmed_attrappe(self, urls):
        def hole(url, **kw):
            urls.append(url)
            if "esearch" in url:
                return json.dumps({"esearchresult": {"count": "2", "idlist": ["11", "12"]}}).encode()
            return json.dumps({"result": {"11": {"title": "A"}, "12": {"title": "B"}}}).encode()
        return hole

    def test_ncbi_schluessel_wird_gesendet_aber_nicht_protokolliert(self):
        urls: list[str] = []
        with umgebung(**SCHLUESSEL), mock.patch.object(api, "hole", self.pubmed_attrappe(urls)):
            rc, e, ausgabe = self.suche("pubmed")
        self.assertEqual(rc, 0)
        self.assertTrue(all(abfrage(u).get("api_key") == SCHLUESSEL["NCBI_API_KEY"] for u in urls), urls)
        text = (self.lauf / "api.jsonl").read_text(encoding="utf-8")
        for wert in SCHLUESSEL.values():
            self.assertNotIn(wert, text)
            self.assertNotIn(wert, ausgabe)
        self.assertNotIn("api_key", e["url"])
        self.assertNotIn("api_key", json.dumps(e["parameter"]))
        self.assertEqual(e["n_geliefert"], 2)

    def test_ohne_schluessel_kein_parameter(self):
        urls: list[str] = []
        with umgebung(), mock.patch.object(api, "hole", self.pubmed_attrappe(urls)):
            self.suche("pubmed")
        self.assertTrue(all("api_key" not in abfrage(u) for u in urls))

    def test_openalex_schluessel_nicht_im_protokoll(self):
        def hole(url, **kw):
            self.assertEqual(abfrage(url)["api_key"], SCHLUESSEL["OPENALEX_API_KEY"])
            return json.dumps({"meta": {"count": 1, "next_cursor": None}, "results": [{"id": "https://openalex.org/W1"}]}).encode()
        with umgebung(**SCHLUESSEL), mock.patch.object(api, "hole", hole):
            rc, e, _ = self.suche("openalex")
        self.assertEqual(rc, 0)
        self.assertNotIn(SCHLUESSEL["OPENALEX_API_KEY"], (self.lauf / "api.jsonl").read_text(encoding="utf-8"))

    def test_s2_schluessel_als_kopfzeile(self):
        gesehen = []

        def hole(url, headers=None, **kw):
            gesehen.append((url, headers))
            return json.dumps({"total": 1, "data": [{"title": "T", "paperId": "p"}]}).encode()
        with umgebung(**SCHLUESSEL), mock.patch.object(api, "hole", hole):
            rc, e, ausgabe = self.suche("semanticscholar")
        self.assertEqual(gesehen[0][1], {"x-api-key": SCHLUESSEL["S2_API_KEY"]})
        self.assertNotIn(SCHLUESSEL["S2_API_KEY"], gesehen[0][0])
        text = (self.lauf / "api.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SCHLUESSEL["S2_API_KEY"], text + ausgabe)

    def test_alter_s2_name_gilt_weiter(self):
        gesehen = []
        with umgebung(SEMANTIC_SCHOLAR_API_KEY="alt-geheim-4711"), mock.patch.object(
                api, "hole", lambda url, headers=None, **kw: gesehen.append(headers) or b'{"data": []}'):
            self.suche("semanticscholar")
        self.assertEqual(gesehen, [{"x-api-key": "alt-geheim-4711"}])

    def test_schluessel_in_fehlermeldung_wird_geschwaerzt(self):
        def hole(url, **kw):
            raise RuntimeError(f"kaputt bei {url}")
        with umgebung(**SCHLUESSEL), mock.patch.object(api, "hole", hole):
            rc, e, _ = self.suche("pubmed")
        self.assertEqual(rc, 2)
        self.assertIn("RuntimeError", e["fehler"])
        text = (self.lauf / "api.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SCHLUESSEL["NCBI_API_KEY"], text)
        self.assertNotIn("api_key=ncbi", text)

    def test_ohne_schluessel_entfernt_parameter(self):
        self.assertEqual(api.ohne_schluessel("https://x.org/a?q=1&api_key=geheim&apikey=g2&key=g3&z=%2A"),
                         "https://x.org/a?q=1&z=%2A")

    def test_eintrag_entfernt_auch_access_token_sig_und_signature(self):
        """GEHEIME_PARAMETER deckt mehr als api_key/apikey/key; q bleibt in der .jsonl."""
        a = argparse.Namespace(lauf=str(self.lauf), weg="websuche", db="db-x",
                               query="q", url="https://x.example/?access_token=GEHEIM1&sig=GEHEIM2&q=ok",
                               treffer="[]", by="process:test", at=None, werkzeug=None, n_gesamt=None)
        with umgebung(), redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_eintrag(a), 0)
        eintrag = json.loads((self.lauf / "websuche.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(eintrag["url"], "https://x.example/?q=ok")
        text = json.dumps(eintrag)
        self.assertNotIn("GEHEIM1", text)
        self.assertNotIn("GEHEIM2", text)
        self.assertIn("q=ok", eintrag["url"])

    def test_eintrag_entfernt_auch_client_secret_und_password(self):
        """GEHEIME_PARAMETER deckt auch client_secret und password (mit pwd, passwd, auth, secret,
        credential(s)); q bleibt in der .jsonl."""
        a = argparse.Namespace(lauf=str(self.lauf), weg="websuche", db="db-x",
                               query="q", url="https://x.example/?client_secret=GEHEIM1&password=GEHEIM2&q=ok",
                               treffer="[]", by="process:test", at=None, werkzeug=None, n_gesamt=None)
        with umgebung(), redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_eintrag(a), 0)
        eintrag = json.loads((self.lauf / "websuche.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(eintrag["url"], "https://x.example/?q=ok")
        text = json.dumps(eintrag)
        self.assertNotIn("GEHEIM1", text)
        self.assertNotIn("GEHEIM2", text)
        self.assertIn("q=ok", eintrag["url"])

    def test_eintrag_schwaerzt_query_url_und_treffer(self):
        """cmd_eintrag schreibt den Eintrag wie cmd_suche geschwärzt; Schlüssel nie in die .jsonl."""
        a = argparse.Namespace(lauf=str(self.lauf), weg="websuche", db="db-x",
                               query=f"suche {SCHLUESSEL['NCBI_API_KEY']}",
                               url=f"https://api.openalex.org/works?search=x&api_key={SCHLUESSEL['OPENALEX_API_KEY']}",
                               treffer=json.dumps([{"titel": f"T {SCHLUESSEL['S2_API_KEY']}", "url":
                                                    f"https://example.org/a?api_key={SCHLUESSEL['OPENALEX_API_KEY']}"}]),
                               by="process:test", at=None, werkzeug=None, n_gesamt=None)
        with umgebung(**SCHLUESSEL), redirect_stdout(io.StringIO()):
            self.assertEqual(api.cmd_eintrag(a), 0)
        text = (self.lauf / "websuche.jsonl").read_text(encoding="utf-8")
        for wert in SCHLUESSEL.values():
            self.assertNotIn(wert, text)
        self.assertNotIn("api_key=oa", text)
        eintrag = json.loads(text)
        self.assertEqual(eintrag["url"], "https://api.openalex.org/works?search=x")   # ohne_schluessel entfernt api_key
        self.assertIn("***", eintrag["query"])
        self.assertIn("***", eintrag["treffer"][0]["titel"])
        self.assertIn("***", eintrag["treffer"][0]["url"])


class Seiten(Grundtest):
    def setUp(self):
        super().setUp()
        for dienst, groesse in (("pubmed", 40), ("esummary", 15), ("europepmc", 40), ("crossref", 40),
                                ("openalex", 40), ("semanticscholar", 40), ("arxiv", 40)):
            p = mock.patch.dict(api.SEITE, {dienst: groesse})
            p.start()
            self.addCleanup(p.stop)
        self.anfragen: list[str] = []
        self.p = umgebung()
        self.p.start()
        self.addCleanup(self.p.stop)

    def mit(self, fn):
        def hole(url, **kw):
            self.anfragen.append(url)
            return fn(url, abfrage(url))
        return mock.patch.object(api, "hole", hole)

    def pruefe(self, e, anfragen, geliefert, gesamt):
        self.assertIsNone(e["fehler"])
        self.assertEqual(len(self.anfragen), anfragen)
        self.assertEqual(e["n_geliefert"], geliefert)
        self.assertEqual(len(e["treffer"]), geliefert)
        self.assertEqual(e["n_gesamt"], gesamt)
        self.assertEqual([t["rang"] for t in e["treffer"]], list(range(1, geliefert + 1)))

    def test_pubmed_retstart_und_esummary_in_teilen(self):
        def fn(url, q):
            if "esearch" in url:
                start, anz = int(q["retstart"]), int(q["retmax"])
                ids = [str(1000 + i) for i in range(start, min(start + anz, 100))]
                return json.dumps({"esearchresult": {"count": "100", "idlist": ids}}).encode()
            return json.dumps({"result": {i: {"title": f"T{i}"} for i in q["id"].split(",")}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("pubmed", n=90)
        # esearch: 40 + 40 + 10; esummary: 3 + 3 + 1 Teile zu 15
        self.pruefe(e, 3 + 3 + 3 + 1, 90, 100)
        self.assertEqual(e["treffer"][-1]["pmid"], "1089")
        self.assertEqual(len({t["pmid"] for t in e["treffer"]}), 90)
        self.assertEqual(e["parameter"]["seiten"], 3)

    def test_pubmed_ende_vor_max(self):
        def fn(url, q):
            if "esearch" in url:
                ids = [str(i) for i in range(int(q["retstart"]), min(int(q["retstart"]) + int(q["retmax"]), 55))]
                return json.dumps({"esearchresult": {"count": "55", "idlist": ids}}).encode()
            return json.dumps({"result": {i: {} for i in q["id"].split(",")}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("pubmed", n=500)
        self.assertEqual(e["n_geliefert"], 55)
        self.assertEqual(e["n_gesamt"], 55)
        self.assertEqual(sum("esearch" in u for u in self.anfragen), 2)

    def test_europepmc_cursormark(self):
        def fn(url, q):
            seite = 0 if q["cursorMark"] == "*" else int(q["cursorMark"])
            anz = int(q["pageSize"])
            ids = list(range(seite, min(seite + anz, 100)))
            return json.dumps({"hitCount": 100, "nextCursorMark": str(seite + anz),
                               "resultList": {"result": [{"id": str(i), "source": "MED", "pmid": str(i)} for i in ids]}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("europepmc", n=100)
        self.pruefe(e, 3, 100, 100)
        self.assertEqual(abfrage(self.anfragen[1])["cursorMark"], "40")

    def test_europepmc_cursor_unveraendert_beendet(self):
        def fn(url, q):
            return json.dumps({"hitCount": 3, "nextCursorMark": q["cursorMark"],
                               "resultList": {"result": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("europepmc", n=100)
        self.pruefe(e, 1, 3, 3)

    def test_crossref_cursor(self):
        def fn(url, q):
            zahl = 0 if q["cursor"] == "*" else int(q["cursor"])
            anz = int(q["rows"])
            items = [{"DOI": f"10.1/{i}", "title": [f"T{i}"]} for i in range(zahl, min(zahl + anz, 70))]
            return json.dumps({"message": {"total-results": 70, "next-cursor": str(zahl + anz), "items": items}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("crossref", n=200)
        self.pruefe(e, 3, 70, 70)  # dritte Seite hat 0 neue, die zweite ist voll: 40 + 30, dann leer
        self.assertEqual(e["treffer"][-1]["doi"], "10.1/69")

    def test_openalex_cursor_mit_schluessel(self):
        def fn(url, q):
            zahl = 0 if q["cursor"] == "*" else int(q["cursor"])
            anz = int(q["per-page"])
            ende = min(zahl + anz, 85)
            return json.dumps({"meta": {"count": 85, "next_cursor": str(ende) if ende < 85 else None},
                               "results": [{"id": f"https://openalex.org/W{i}", "title": f"T{i}"} for i in range(zahl, ende)]}).encode()
        with umgebung(OPENALEX_API_KEY=SCHLUESSEL["OPENALEX_API_KEY"]), self.mit(fn):
            rc, e, _ = self.suche("openalex", n=120)
        self.pruefe(e, 3, 85, 85)

    def test_openalex_page_ohne_schluessel(self):
        def fn(url, q):
            self.assertNotIn("cursor", q)  # ohne Schlüssel antwortet OpenAlex auf Cursor-Seite 2 mit 401
            seite, anz = int(q["page"]), int(q["per-page"])
            ids = range((seite - 1) * anz, min(seite * anz, 85))
            return json.dumps({"meta": {"count": 85},
                               "results": [{"id": f"https://openalex.org/W{i}", "title": f"T{i}"} for i in ids]}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("openalex", n=120)
        self.pruefe(e, 3, 85, 85)
        self.assertEqual(e["treffer"][-1]["openalex"], "W84")

    def test_openalex_page_schneidet_auf_max(self):
        def fn(url, q):
            seite, anz = int(q["page"]), int(q["per-page"])
            return json.dumps({"meta": {"count": 500}, "results": [
                {"id": f"https://openalex.org/W{i}"} for i in range((seite - 1) * anz, seite * anz)]}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("openalex", n=70)
        self.pruefe(e, 2, 70, 500)

    def test_semanticscholar_offset(self):
        def fn(url, q):
            start, anz = int(q["offset"]), int(q["limit"])
            ende = min(start + anz, 90)
            antwort = {"total": 90, "offset": start, "data": [{"paperId": f"p{i}", "title": f"T{i}"} for i in range(start, ende)]}
            if ende < 90:
                antwort["next"] = ende
            return json.dumps(antwort).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("semanticscholar", n=100)
        self.pruefe(e, 3, 90, 90)

    def test_semanticscholar_obergrenze(self):
        def fn(url, q):
            start, anz = int(q["offset"]), int(q["limit"])
            return json.dumps({"total": 5000, "next": start + anz,
                               "data": [{"paperId": f"p{i}"} for i in range(start, start + anz)]}).encode()
        with mock.patch.dict(api.SEITE, {"semanticscholar": 100}), self.mit(fn):
            rc, e, _ = self.suche("semanticscholar", n=1500)
        self.assertEqual(e["n_geliefert"], api.S2_MAX)
        self.assertEqual(e["n_gesamt"], 5000)
        self.assertIn("hinweis", e["parameter"])

    def test_arxiv_start(self):
        def fn(url, q):
            start, anz = int(q["start"]), int(q["max_results"])
            ende = min(start + anz, 75)
            eintraege = "".join(f"<entry><id>http://arxiv.org/abs/2401.{i:05d}v1</id><title>T{i}</title>"
                                f"<published>2024-01-01T00:00:00Z</published></entry>" for i in range(start, ende))
            return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
                    f"<opensearch:totalResults>75</opensearch:totalResults>{eintraege}</feed>").encode()
        with self.mit(fn):
            rc, e, _ = self.suche("arxiv", n=100)
        self.pruefe(e, 2, 75, 75)
        self.assertEqual(e["treffer"][0]["arxiv"], "2401.00000")

    def test_obergrenze_max(self):
        def fn(url, q):
            zahl = 0 if q["cursor"] == "*" else int(q["cursor"])
            anz = int(q["rows"])
            items = [{"DOI": f"10.1/{i}"} for i in range(zahl, zahl + anz)]
            return json.dumps({"message": {"total-results": 99999, "next-cursor": str(zahl + anz), "items": items}}).encode()
        with mock.patch.dict(api.SEITE, {"crossref": 1000}), self.mit(fn):
            rc, e, _ = self.suche("crossref", n=50000)
        self.assertEqual(e["n_geliefert"], api.MAX_TREFFER)
        self.assertEqual(e["n_gesamt"], 99999)
        self.assertEqual(e["parameter"]["max_begrenzt_auf"], api.MAX_TREFFER)

    def test_fehler_mitten_im_abruf_behaelt_treffer(self):
        def fn(url, q):
            if q["cursor"] != "*":
                raise http.client.IncompleteRead(b"x", 5)
            items = [{"DOI": f"10.1/{i}"} for i in range(int(q["rows"]))]
            return json.dumps({"message": {"total-results": 500, "next-cursor": "40", "items": items}}).encode()
        with self.mit(fn):
            rc, e, _ = self.suche("crossref", n=200)
        self.assertEqual(rc, 2)
        self.assertEqual(e["n_geliefert"], 40)
        self.assertEqual(len(e["treffer"]), 40)
        self.assertEqual(e["n_gesamt"], 500)
        self.assertIn("IncompleteRead", e["fehler"])

    def test_anzeige_gekuerzt_protokoll_vollstaendig(self):
        def fn(url, q):
            return json.dumps({"hitCount": 80, "nextCursorMark": "*",
                               "resultList": {"result": [{"id": str(i)} for i in range(80)]}}).encode()
        with mock.patch.dict(api.SEITE, {"europepmc": 1000}), self.mit(fn):
            rc, e, ausgabe = self.suche("europepmc", n=80)
        self.assertEqual(e["n_geliefert"], 80)
        angezeigt = json.loads(ausgabe)
        self.assertEqual(len(angezeigt["treffer"]), api.ANZEIGE)
        self.assertIn("hinweis", angezeigt)


class Haertung(Grundtest):
    def test_lade_ueberspringt_falsche_typen_mit_hinweis(self):
        gut = {"v": 1, "weg": "api", "db": "api:pubmed", "query": "x", "at": "2026-01-01T00:00:00Z",
               "by": "process:t", "treffer": [{"rang": 1, "doi": "10.1/a", "titel": "Ein Titel"}]}
        zeilen = [json.dumps(gut), json.dumps([1, 2]), json.dumps({**gut, "query": 5}),
                  json.dumps({**gut, "treffer": [1, "x"]}), json.dumps({**gut, "db": ["a"]})]
        self.lauf.mkdir()
        (self.lauf / "api.jsonl").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            eintraege, fehler = api.lade(self.lauf)
            kandidaten, _ = api.zusammenfuehren(self.lauf)
        self.assertEqual(len(eintraege), 1)
        self.assertEqual(len(fehler), 4)
        self.assertEqual(len(kandidaten), 1)
        for ort in ("api.jsonl:2", "api.jsonl:3", "api.jsonl:4", "api.jsonl:5"):
            self.assertIn(f"{ort} übersprungen", err.getvalue())

    def test_by_standard_ist_neutral(self):
        with umgebung():
            os.environ.pop("RECHERCHE_BY", None)
            p = api.baue_parser()
        for argv in (["suche", "pubmed", "x", "--lauf", "l"], ["aufloesen", "l", "1"]):
            self.assertEqual(p.parse_args(argv).by, "claude-code/recherche")
        with umgebung(RECHERCHE_BY="process:x"):
            self.assertEqual(api.baue_parser().parse_args(["aufloesen", "l", "1"]).by, "process:x")
        quelle = PFAD.read_text(encoding="utf-8")
        self.assertNotIn("claude-opus", quelle)

    def test_abstract_normalisiert_doi_und_pmid(self):
        gesehen = []

        def hole_json(url, **kw):
            gesehen.append(abfrage(url)["query"])
            return {"resultList": {"result": [{"title": "T", "abstractText": "Text"}]}}
        with mock.patch.object(api, "hole_json", hole_json), redirect_stdout(io.StringIO()):
            for doi, pmid in (("https://doi.org/10.1/ABC", None), ("doi:10.1/abc", None), (None, "https://pubmed.ncbi.nlm.nih.gov/123/")):
                a = argparse.Namespace(doi=doi, pmid=pmid, out=str(Path(self._tmp.name) / "a.txt"))
                self.assertEqual(api.cmd_abstract(a), 0)
        self.assertEqual(gesehen, ['DOI:"10.1/abc"', 'DOI:"10.1/abc"', "EXT_ID:123 AND SRC:MED"])

    def test_schwaerzen_auch_url_kodiert(self):
        with umgebung(NCBI_API_KEY="a b/c+d&e=1"):
            roh = "x " + urllib.parse.quote("a b/c+d&e=1", safe="") + " y " + urllib.parse.quote_plus("a b/c+d&e=1") + " z a b/c+d&e=1"
            aus = api.schwaerzen({"fehler": roh, "liste": [roh]})
        self.assertNotIn("c%2Bd", json.dumps(aus))
        self.assertNotIn("a+b", json.dumps(aus))
        self.assertNotIn("a b/c", json.dumps(aus))

    def test_weiterleitung_nimmt_schluesselkoepfe_nur_zum_selben_host(self):
        h = api._NurErlaubte()
        req = urllib.request.Request("https://api.semanticscholar.org/a", headers={"x-api-key": "geheim", "User-Agent": "u"})
        req.add_unredirected_header("Authorization", "Bearer x")
        fremd = h.redirect_request(req, None, 302, "Found", {}, "https://api.crossref.org/b")
        self.assertEqual({k.lower() for k in fremd.headers}, {"user-agent"})
        self.assertEqual(dict(fremd.unredirected_hdrs), {})
        selber = h.redirect_request(req, None, 302, "Found", {}, "https://api.semanticscholar.org/c")
        self.assertIn("geheim", selber.headers.values())
        with self.assertRaises(api.Abgelehnt):
            h.redirect_request(req, None, 302, "Found", {}, "https://example.org/b")

    def test_port_nur_standard_oder_ausdruecklich_erlaubt(self):
        self.assertEqual(api.pruefe_host("https://api.crossref.org/works"), "api.crossref.org")
        self.assertEqual(api.pruefe_host("https://api.crossref.org:443/works"), "api.crossref.org")
        for url in ("https://api.crossref.org:8443/works", "https://api.crossref.org:abc/works"):
            with self.assertRaises(api.Abgelehnt):
                api.pruefe_host(url)
        with mock.patch.dict(api.PORTS, {"api.crossref.org": {8443}}):
            self.assertEqual(api.pruefe_host("https://api.crossref.org:8443/works"), "api.crossref.org")
        with self.assertRaises(api.Abgelehnt):
            api.pruefe_host("http://api.crossref.org/works")

    def test_antwort_zu_gross_ist_fehler(self):
        with mock.patch.object(api, "MAX_ANTWORT", 100):
            with mock.patch.object(api._OPENER, "open", return_value=Antwort(b"x" * 101)) as o:
                with self.assertRaises(api.ZuGross):
                    api.hole("https://api.crossref.org/works")
            self.assertEqual(o.call_count, 1)  # kein Wiederholen
            with mock.patch.object(api._OPENER, "open", return_value=Antwort(b"x" * 100)):
                self.assertEqual(len(api.hole("https://api.crossref.org/works")), 100)

    def test_gzip_bombe_ist_fehler(self):
        antwort = Antwort(gzip.compress(b"0" * 10_000))
        antwort.headers = {"Content-Encoding": "gzip"}
        with mock.patch.object(api, "MAX_ANTWORT", 5000), mock.patch.object(api._OPENER, "open", return_value=antwort):
            with self.assertRaises(api.ZuGross):
                api.hole("https://api.crossref.org/works")
        rc, e, _ = self._suche_mit_zu_grosser_antwort()
        self.assertEqual(rc, 2)
        self.assertIn("ZuGross", e["fehler"])

    def _suche_mit_zu_grosser_antwort(self):
        with mock.patch.object(api, "MAX_ANTWORT", 10), mock.patch.object(api._OPENER, "open", return_value=Antwort(b"x" * 50)):
            return self.suche("crossref")


class Pfade(unittest.TestCase):
    def test_kern_liegt_neben_dem_skill(self):
        self.assertTrue(api.KERN_PFAD.is_file(), api.KERN_PFAD)
        self.assertEqual(api.KERN_PFAD, PFAD.parents[2] / "okfw" / "okfw.py")
        self.assertIn(f'"{api.KERN_PFAD}"', api.KERN)
        self.assertNotIn("~", api.KERN)

    def test_fundweg_zeigt_den_vollen_pfad(self):
        with tempfile.TemporaryDirectory() as t:
            lauf = Path(t)
            (lauf / "kandidaten.json").write_text(json.dumps([{
                "nr": 1, "doi": "10.1/x", "nur_websuche": False, "aufgeloest": True, "wege": ["api"],
                "funde": [{"weg": "api", "db": "api:pubmed", "query": "x", "at": "2026-01-01T00:00:00Z",
                           "by": "process:t", "rang": 1}]}]), encoding="utf-8")
            a = argparse.Namespace(lauf=str(lauf), kandidat="1", seite="sources/x", root=None)
            with redirect_stdout(io.StringIO()) as aus:
                api.cmd_fundweg(a)
        self.assertTrue(aus.getvalue().startswith(f'uv run --no-config --quiet "{api.KERN_PFAD}" fundweg sources/x'), aus.getvalue())

    def fundweg_ausgabe(self, inhalt: str) -> str:
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "kandidaten.json").write_text(inhalt, encoding="utf-8")
            a = argparse.Namespace(lauf=t, kandidat="1", seite="sources/x", root=None)
            with redirect_stdout(io.StringIO()) as aus:
                api.cmd_fundweg(a)
        return aus.getvalue()

    def test_fremdwerte_bleiben_ein_argument(self):
        boese = api.KERN + "; echo x"
        fund = self.fund("api", db=boese, query=boese, aufgeloest_via="x\nrm -rf y\n# ")
        aus = self.fundweg_ausgabe(json.dumps([{"nr": 1, "doi": "10.1/x", "nur_websuche": False, "aufgeloest": True,
                                                "funde": [fund]}]))
        zeilen = aus.splitlines()
        self.assertEqual([z[:1] for z in zeilen], ["#", "u"], aus)
        teile = shlex.split(zeilen[1])
        self.assertEqual(teile[teile.index("--db") + 1], boese)
        self.assertEqual(teile[teile.index("--query") + 1], boese)
        self.assertTrue(zeilen[1].startswith(api.KERN + " fundweg "))

    def test_fehlformen_enden_mit_text(self):
        for inhalt in ('{"a": 1}', '["x"]', '[{"doi": "1"}]', '[{"nr": 1, "funde": [{"db": "d"}]}]',
                       '[{"nr": 1, "funde": "x", "nur_websuche": false}]'):
            with self.subTest(inhalt=inhalt), self.assertRaises(SystemExit) as cm:
                self.fundweg_ausgabe(inhalt)
            self.assertIsInstance(cm.exception.code, str)

    def test_ohne_angabe_gilt_nur_websuche(self):
        with self.assertRaisesRegex(SystemExit, "nur aus der Websuche"):
            self.fundweg_ausgabe(json.dumps([{"nr": 1, "funde": [self.fund("api")]}]))

    def test_aufloesungen_fehlform(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "aufloesungen.json").write_text('{"a": 1}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                api.lade_aufloesungen(Path(t))

    def test_symlinks_im_lauf_ordner(self):
        with tempfile.TemporaryDirectory() as t:
            lauf, fremd = Path(t) / "lauf", Path(t) / "fremd.txt"
            lauf.mkdir()
            fremd.write_text("alt\n", encoding="utf-8")
            (lauf / "api.jsonl").symlink_to(fremd)
            with self.assertRaises(SystemExit):
                api.anhaengen(lauf / "api.jsonl", {"a": 1})
            self.assertEqual(fremd.read_text(encoding="utf-8"), "alt\n")

    def fundweg_befehle(self, funde: list[dict]) -> list[str]:
        with tempfile.TemporaryDirectory() as t:
            lauf = Path(t)
            (lauf / "kandidaten.json").write_text(json.dumps([{
                "nr": 1, "doi": "10.1/x", "nur_websuche": False, "aufgeloest": True, "funde": funde}]), encoding="utf-8")
            a = argparse.Namespace(lauf=str(lauf), kandidat="1", seite="sources/x", root=None)
            with redirect_stdout(io.StringIO()) as aus:
                api.cmd_fundweg(a)
        return [z for z in aus.getvalue().splitlines() if not z.startswith("#")]

    @staticmethod
    def fund(weg: str, **mehr) -> dict:
        return {"weg": weg, "db": f"db-{weg}", "query": "q", "at": "2026-01-01T00:00:00Z", "by": "process:t", "rang": 1, **mehr}

    def test_fundweg_behaelt_die_art_des_wegs(self):
        """Gefunden: Außer modellvorschlag wurde jede Art zur suche; der Kern behält eine Fundweg-Art."""
        zeilen = self.fundweg_befehle([self.fund("api"), self.fund("modellvorschlag"), self.fund("material"),
                                       self.fund("person"), self.fund("websuche")])
        arten = [z.split(" --art ")[1].split(" ")[0] for z in zeilen]
        self.assertEqual(arten, ["suche", "modellvorschlag", "material", "person", "suche"])
        self.assertNotIn("--add", zeilen[0])
        self.assertTrue(all(z.endswith("--add") for z in zeilen[1:]), zeilen)

    def test_fundweg_zitatverfolgung_nimmt_von_aus_dem_fund(self):
        zeilen = self.fundweg_befehle([self.fund("zitatverfolgung", von="mueller2020"), self.fund("api")])
        self.assertIn("--art zitatverfolgung", zeilen[0])
        self.assertIn("--von mueller2020", zeilen[0])
        self.assertNotIn("--von", zeilen[1])

    def test_fundweg_zitatverfolgung_ohne_von_bricht_ab(self):
        with redirect_stdout(io.StringIO()) as aus, self.assertRaises(SystemExit) as fehler:
            self.fundweg_befehle([self.fund("api"), self.fund("zitatverfolgung")])
        self.assertIn("zitatverfolgung ohne von", str(fehler.exception))
        self.assertEqual(aus.getvalue(), "")   # nichts halb ausgegeben

    def test_fundweg_arten_wie_im_kern(self):
        kern = (Path(api.KERN_PFAD).parent / "okfw_kern" / "konfig.py").read_text(encoding="utf-8")
        ausschnitt = kern[kern.index("FUNDWEG_ARTEN = {"):].split("}", 1)[0]
        self.assertEqual(tuple(sorted(api.FUNDWEG_ARTEN)), tuple(sorted(
            re.findall(r'"([a-z]+)":', ausschnitt))))

    def test_archiv_ordner_ueber_umgebungsvariable(self):
        with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as wo:
            alt = os.getcwd()
            os.chdir(wo)
            self.addCleanup(os.chdir, alt)
            nicht_ignoriert = mock.Mock(returncode=1, stdout="")
            with umgebung(OKFW_RECHERCHE_PROTOKOLL=t), mock.patch.object(api.subprocess, "run", return_value=nicht_ignoriert):
                ziel = api.protokoll_ziel("lauf-1")
            self.assertEqual(ziel, Path(t) / Path(wo).name / "lauf-1")
            os.environ.pop("OKFW_RECHERCHE_PROTOKOLL", None)
            with umgebung(), mock.patch.object(api.subprocess, "run", return_value=nicht_ignoriert):
                ziel = api.protokoll_ziel("lauf-1")
            self.assertEqual(ziel, Path.home() / ".claude" / "recherche-protokoll" / Path(wo).name / "lauf-1")


if __name__ == "__main__":
    unittest.main()
