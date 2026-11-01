"""okfw serve: Bearbeiten im Dashboard. Echter Server auf einem freien Port, Wiki und Einstellungen unter $TMPDIR.

Der Server läuft im Testprozess (Kern und okfw_serve werden importiert), die Anfragen gehen über http.client.
Das braucht PyYAML und markdown (`uv run --with pyyaml --with markdown`). Bindet die Sandbox keinen Port (EPERM),
die Tests außerhalb der Sandbox laufen lassen.
"""

import contextlib
import http.client
import http.server
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest import mock

from .hilfe import CORE, SKILL, UV, clean_env

sys.path.insert(0, str(SKILL))
try:
    import okfw
    import okfw_serve
except ImportError as exc:   # PyYAML oder markdown fehlen
    raise unittest.SkipTest(f"okfw lässt sich nicht importieren: {exc}")

TOKEN = "test-token-123"
GEHEIM = "sk-geheim-nie-ausgeben-4711"
PROFIL = SKILL / "profiles" / "default.toml"


def still(funktion, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return funktion(*args)


class ServeTest(unittest.TestCase):
    kennung = "tester"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="okfw-serve-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "projekt"
        self.root.mkdir()
        self.einstellungen = self.base / "einstellungen.toml"
        env = {"OKFW_EINSTELLUNGEN": str(self.einstellungen), "OKFW_ROLLEN_PROTOKOLL": str(self.base / "rollen.jsonl"),
               "OPENAI_API_KEY": GEHEIM}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("WIKI_ACTOR", "ANTHROPIC_BASE_URL"):
            os.environ.pop(name, None)
        for name, wert in (("schluesselbund_da", False), ("schluesselbund_holen", "")):   # nie der echte Schlüsselbund
            patcher = mock.patch.object(okfw_serve.rollen, name, return_value=wert)
            patcher.start()
            self.addCleanup(patcher.stop)
        if self.kennung:
            okfw_serve.rollen.setze_person(self.kennung)
        okfw_serve.rollen.setze_rolle("bearbeiten", "direkt", "openai", "test-modell")
        self.kern("init")
        self.kern("new", "concept", "kern", "--title", "Kern", "--description", "Der Kern.")
        self.kern("new", "source", "q1", "--title", "Quelle 1", "--description", "Testquelle", "--access", "citation",
                  "--resource", "https://example.org/q1")
        self.server = okfw_serve.bauen(okfw, self.root / "wiki.toml", 0, TOKEN)
        self.port = self.server.server_address[1]
        self.praefix = self.server.sitzung.praefix
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.cookie = ""
        self.anmelden()

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    # ---------------------------------------------------------------- Hilfen

    def kern(self, *args):
        code = still(okfw.main, ["--root", str(self.root), *args])
        self.assertEqual(code, 0, f"okfw {' '.join(args)}")

    def wiki(self):
        return okfw.Wiki.load(config=self.root / "wiki.toml")

    def seite(self, pid):
        return next(p for p in self.wiki().pages() if p.id == pid)

    def datei(self, pid):
        return self.root / "wiki" / f"{pid}.md"

    def anfrage(self, methode, pfad, body=None, *, cookie=True, host=None, origin="auto", kopf=None, typ="application/json", roh=None,
                fremd=False):
        """Pfade gelten hinter dem Präfix dieses Serverstarts; fremd=True schickt den Pfad, wie er ist."""
        pfad = pfad if fremd else f"/{self.praefix}{pfad}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        k = {"Host": host or f"127.0.0.1:{self.port}"}
        if cookie and self.cookie:
            k["Cookie"] = self.cookie
        if methode == "POST":
            k["Content-Type"] = typ
            k["X-OKFW"] = "1"
            if origin == "auto":
                k["Origin"] = f"http://127.0.0.1:{self.port}"
            elif origin:
                k["Origin"] = origin
        elif origin not in ("auto", None):
            k["Origin"] = origin
        k.update(kopf or {})
        if roh is None and body is not None:
            roh = json.dumps(body)
        conn.request(methode, pfad, body=roh, headers=k)
        antwort = conn.getresponse()
        text = antwort.read().decode("utf-8")
        conn.close()
        try:
            daten = json.loads(text) if "json" in (antwort.getheader("Content-Type") or "") else text
        except ValueError:
            daten = text
        return antwort.status, antwort, daten

    def anmelden(self):
        status, antwort, _ = self.anfrage("GET", f"/?t={TOKEN}", cookie=False)
        self.assertEqual(status, 302)
        self.cookie = antwort.getheader("Set-Cookie").split(";")[0]

    def post(self, pfad, body, **kw):
        return self.anfrage("POST", pfad, body, **kw)

    def gegenpruefen(self, pid):
        self.kern("verify", pid, "--by", "claude-code/pruefer")


class Zugang(ServeTest):
    def test_ohne_token_403(self):
        self.assertEqual(self.anfrage("GET", "/", cookie=False)[0], 403)
        self.assertEqual(self.anfrage("GET", "/api/einstellungen", cookie=False)[0], 403)
        status, _, daten = self.anfrage("GET", "/api/seite?id=concepts/kern", cookie=False)
        self.assertEqual(status, 403)
        self.assertIn("fehler", daten)

    def test_falscher_token_403(self):
        self.assertEqual(self.anfrage("GET", "/?t=falsch", cookie=False)[0], 403)

    def test_start_url_setzt_cookie_und_leitet_um(self):
        status, antwort, _ = self.anfrage("GET", f"/?t={TOKEN}", cookie=False)
        self.assertEqual(status, 302)
        self.assertEqual(antwort.getheader("Location"), f"/{self.praefix}/")
        gesetzt = antwort.getheader("Set-Cookie")
        self.assertIn(f"Path=/{self.praefix}/", gesetzt)
        self.assertIn("HttpOnly", gesetzt)
        self.assertIn("SameSite=Strict", gesetzt)
        self.assertNotIn(TOKEN, gesetzt)

    def test_seite_mit_csp_und_serve_js(self):
        status, antwort, html = self.anfrage("GET", "/")
        self.assertEqual(status, 200)
        csp = antwort.getheader("Content-Security-Policy")
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("http", csp)
        self.assertIn("window.OKFW_SERVE", html)
        self.assertIn('"api/einstellungen"', html)
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'<script nonce="{nonce}">', html)
        self.assertNotRegex(html, r"<script(?![^>]*nonce=)")

    def test_serve_js_hat_kein_script_ende(self):
        self.assertNotIn("</script", (SKILL / "templates" / "serve.js").read_text(encoding="utf-8").lower())

    def test_falscher_host_403(self):
        self.assertEqual(self.anfrage("GET", "/", host="boese.example:80")[0], 403)
        self.assertEqual(self.anfrage("GET", "/", host="127.0.0.1:1")[0], 403)
        self.assertEqual(self.post("/api/freigabe", {"ids": ["concepts/kern"]}, host="boese.example")[0], 403)

    def test_falsche_origin_403(self):
        self.assertEqual(self.anfrage("GET", "/api/einstellungen", origin="http://boese.example")[0], 403)
        self.assertEqual(self.post("/api/freigabe", {"ids": ["concepts/kern"]}, origin="http://boese.example")[0], 403)
        self.assertEqual(self.post("/api/freigabe", {"ids": ["concepts/kern"]}, origin=None)[0], 403)

    def test_post_braucht_kopf_cookie_json_und_maximale_groesse(self):
        ids = {"ids": ["concepts/kern"]}
        self.assertEqual(self.post("/api/freigabe", ids, cookie=False)[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", f"/{self.praefix}/api/freigabe", json.dumps(ids), {"Cookie": self.cookie, "Content-Type": "application/json",
                                                              "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(conn.getresponse().status, 403)   # X-OKFW fehlt
        conn.close()
        self.assertEqual(self.post("/api/freigabe", ids, typ="text/plain")[0], 415)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)   # nur die Köpfe: der Server liest nichts
        conn.putrequest("POST", f"/{self.praefix}/api/freigabe")
        for name, wert in (("Cookie", self.cookie), ("Content-Type", "application/json"), ("X-OKFW", "1"),
                           ("Origin", f"http://127.0.0.1:{self.port}"), ("Content-Length", str(okfw_serve.MAX_ANFRAGE + 1))):
            conn.putheader(name, wert)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        self.assertEqual(self.post("/api/freigabe", None, roh="kein json")[0], 400)

    def test_seiten_id_mit_pfad_404(self):
        for pid in ("../wiki.toml", "..%2Fwiki", "%2e%2e/%2e%2e/etc/passwd", "concepts/../../wiki.toml", "concepts/kern.md"):
            self.assertEqual(self.anfrage("GET", f"/api/seite?id={pid}")[0], 404, pid)
        status, _, daten = self.post("/api/seite", {"id": "../wiki.toml", "hash": "x", "body": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(self.post("/api/freigabe", {"ids": ["../wiki"]})[0], 404)
        self.assertIn("wiki.toml", (self.root / "wiki.toml").name)


class Praefix(ServeTest):
    """Cookies kennen keine Ports: Ohne eigenen Pfad bekam sie jeder Prozess auf derselben Adresse (gesehen auf localhost:18901)."""

    def test_ausserhalb_gibt_es_nichts(self):
        for pfad in ("/", f"/?t={TOKEN}", "/api/einstellungen", f"/{self.praefix}", f"/{self.praefix}x/", "/anders/api/einstellungen", "//"):
            with self.subTest(pfad=pfad):
                self.assertEqual(self.anfrage("GET", pfad, fremd=True)[0], 404)
                self.assertEqual(self.anfrage("GET", pfad, fremd=True, cookie=False)[0], 404)
        status, _, _ = self.anfrage("POST", "/api/einstellungen/person", {"kennung": "boese"}, fremd=True)
        self.assertEqual(status, 404)
        self.assertEqual(okfw_serve.rollen.person(), "tester")

    def test_innerhalb_braucht_es_weiter_die_anmeldung(self):
        self.assertEqual(self.anfrage("GET", "/", cookie=False)[0], 403)
        self.assertEqual(self.anfrage("GET", "/api/gibtsnicht")[0], 404)

    def test_cookie_geht_nicht_an_einen_anderen_port(self):
        gesehen = []

        class Fremd(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                gesehen.append(self.headers.get("Cookie"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        fremd = http.server.HTTPServer(("127.0.0.1", 0), Fremd)
        thread = threading.Thread(target=fremd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(fremd.server_close)
        self.addCleanup(fremd.shutdown)
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(f"http://127.0.0.1:{self.port}/{self.praefix}/?t={TOKEN}").read()
        self.assertEqual(len(jar), 1)
        opener.open(f"http://127.0.0.1:{fremd.server_address[1]}/").read()
        opener.open(f"http://127.0.0.1:{fremd.server_address[1]}/api/einstellungen").read()
        self.assertEqual(gesehen, [None, None])

    def test_zwei_server_melden_sich_nicht_ab(self):
        zweiter = okfw_serve.bauen(okfw, self.root / "wiki.toml", 0, TOKEN)
        self.addCleanup(zweiter.server_close)
        a, b = self.server.sitzung, zweiter.sitzung
        self.assertNotEqual(a.praefix, b.praefix)
        self.assertNotEqual(a.cookie, b.cookie)
        self.assertTrue(a.cookie.startswith(okfw_serve.COOKIE))
        self.assertNotEqual(a.session, b.session)

    def test_serve_js_ruft_nur_relative_pfade(self):
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        self.assertIn('"api/', js)
        self.assertNotRegex(js, r"[\"'`]/api/")
        self.assertNotRegex(js, r"location\.(href|pathname|assign|replace)\b")


class Seiten(ServeTest):
    def lesen(self, pid="concepts/kern"):
        status, _, daten = self.anfrage("GET", f"/api/seite?id={pid}")
        self.assertEqual(status, 200)
        return daten

    def test_lesen_nennt_hash_felder_und_geschuetztes(self):
        d = self.lesen()
        self.assertEqual(d["id"], "concepts/kern")
        self.assertEqual(d["hash"], okfw.sha256_of(self.datei("concepts/kern")))
        self.assertIn("title: Kern", d["frontmatter"])
        self.assertEqual(d["felder"]["title"], "Kern")
        self.assertEqual(set(d["geschuetzt"]), {"status", "verified", "generated"})
        self.assertEqual(d["pruefwertfeld"], "body_sha256")

    def test_hash_konflikt_409_ohne_ueberschreiben(self):
        d = self.lesen()
        pfad = self.datei("concepts/kern")
        pfad.write_text(pfad.read_text(encoding="utf-8") + "\nAndere Änderung.\n", encoding="utf-8")
        vorher = pfad.read_text(encoding="utf-8")
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "Mein Text"})
        self.assertEqual(status, 409)
        self.assertIn("Andere Änderung.", antwort["aktuell"]["body"])
        self.assertEqual(antwort["aktuell"]["hash"], okfw.sha256_of(pfad))
        self.assertEqual(pfad.read_text(encoding="utf-8"), vorher)

    def test_geschuetzte_felder_422(self):
        d = self.lesen()
        pfad = self.datei("concepts/kern")
        vorher = pfad.read_text(encoding="utf-8")
        faelle = {
            "status": d["frontmatter"].replace("status: draft", "status: stable"),
            "generated": d["frontmatter"].split("generated:")[0],
            "verified": d["frontmatter"] + "verified:\n- by: human:x\n  at: 2026-01-01T00:00:00Z\n",
        }
        for feld, text in faelle.items():
            self.assertNotEqual(text, d["frontmatter"], feld)
            status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "frontmatter": text})
            self.assertEqual(status, 422, (feld, antwort))
            self.assertIn("Geschützte Felder", antwort["fehler"])
            self.assertIn(feld, antwort["fehler"])
            self.assertEqual(pfad.read_text(encoding="utf-8"), vorher)

    def test_ungueltiges_yaml_und_fremde_trennzeile_422(self):
        d = self.lesen()
        for text in (d["frontmatter"] + "kaputt: [offen\n", d["frontmatter"] + "---\nx: 1\n", "- liste\n"):
            status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "frontmatter": text})
            self.assertEqual(status, 422, text)

    def test_yaml_das_nicht_passt_ist_422_nicht_500(self):
        """Gefunden: Tiefes YAML endete als RecursionError, ein Feldname ohne Text als TypeError beim Speichern."""
        d = self.lesen()
        pfad = self.datei("concepts/kern")
        vorher = pfad.read_bytes()
        for name, zusatz in (("tief", "x: " + "[" * 5000 + "]" * 5000 + "\n"), ("Zahl als Feld", "1: a\n"), ("nichts als Feld", "~: a\n")):
            with self.subTest(name):
                status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "frontmatter": d["frontmatter"] + zusatz})
                self.assertEqual(status, 422, antwort)
                self.assertNotIn("Interner Fehler", antwort["fehler"])
                self.assertEqual(pfad.read_bytes(), vorher)
        self.lesen()

    def test_nur_schreibweise_des_frontmatters_nennt_keinen_text(self):
        """Gefunden: Das Ereignis sagte „Eigenschaften: Text“, wenn sich kein Feld geändert hatte."""
        d = self.lesen()
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "frontmatter": d["frontmatter"] + "# nur ein Kommentar\n"})
        self.assertEqual(status, 200, antwort)
        detail = [e for e in self.wiki().read_events() if e["kind"] == "update"][-1]["detail"]
        self.assertIn("nur Schreibweise oder Kommentare", detail)
        self.assertNotIn("Eigenschaften: Text", detail)

    def test_unbekannte_felder_bleiben_zeichen_fuer_zeichen(self):
        d = self.lesen()
        eigen = "eigen:   {a: 1,  b: [x,   y]}  # bleibt so\nzweites: >\n  gefaltet\n  text\n"
        text = d["frontmatter"].replace("title: Kern", "title: Kern, neu") + eigen
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "frontmatter": text})
        self.assertEqual(status, 200, antwort)
        gespeichert = self.seite("concepts/kern")
        # erst nach dem Schreiben zieht der Kern generated nach; alles andere ist byte-genau, was gesendet wurde
        ohne = lambda s: "".join(z for z in s.splitlines(keepends=True) if not z.startswith(("generated:", "  by:", "  at:")))
        self.assertEqual(ohne(gespeichert.raw_fm), ohne(text))
        self.assertIn("eigen:   {a: 1,  b: [x,   y]}  # bleibt so\n", gespeichert.raw_fm)
        self.assertEqual(gespeichert.title, "Kern, neu")
        ereignisse = [e for e in self.wiki().read_events() if e["page"] == "concepts/kern" and e["kind"] == "update"]
        self.assertEqual(ereignisse[-1]["by"], "human:tester")

    def test_body_speichern_ereignis_und_befunde(self):
        d = self.lesen()
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "# Kern\n\nNeuer Text.\n"})
        self.assertEqual(status, 200, antwort)
        self.assertIn("Neuer Text.", self.seite("concepts/kern").body)
        self.assertEqual(antwort["stand"]["hash"], okfw.sha256_of(self.datei("concepts/kern")))
        self.assertIn("befunde", antwort)
        events = [e for e in self.wiki().read_events() if e["page"] == "concepts/kern" and e["kind"] == "update"]
        self.assertEqual(events[-1]["by"], "human:tester")
        self.assertIn("Dashboard", events[-1]["detail"])
        self.assertEqual(self.seite("concepts/kern").fm["generated"]["by"], "human:tester")
        # Der Zustand, den der Kern nachträglich erzeugt, stimmt mit dem Stand überein (rebuild lief)
        self.assertEqual(still(okfw.main, ["--root", str(self.root), "rebuild", "--check"]), 0)

    def test_ohne_kennung_422_mit_hinweis(self):
        self.einstellungen.write_text("", encoding="utf-8")
        d = self.lesen()
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "x"})
        self.assertEqual(status, 422)
        self.assertIn("Einstellungen", antwort["fehler"])
        self.assertIn("Kennung", antwort["fehler"])
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"]})
        self.assertEqual(status, 422)
        self.assertIn("Einstellungen", antwort["fehler"])

    def test_freigabe_ohne_gegenpruefung_abgelehnt(self):
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"]})
        self.assertEqual(status, 422)
        self.assertIn("Gegenprüfung", antwort["fehler"])
        self.assertNotIn("--force", antwort["fehler"])
        self.assertIn("das Dashboard", antwort["fehler"])
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")

    def test_stable_faellt_nach_bearbeiten_auf_draft(self):
        self.gegenpruefen("concepts/kern")
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"]})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "stable")
        verified = self.seite("concepts/kern").fm["verified"]
        self.assertEqual(verified[-1]["by"], "human:tester")
        d = self.lesen()
        self.assertEqual(d["status"], "stable")
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "Anderer Inhalt\n"})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")
        self.assertTrue(any("stable" in h for h in antwort["hinweise"]))
        self.assertIn("verfallen", [e for e in self.wiki().read_events() if e["kind"] == "update"][-1]["detail"])

    def test_neue_karte(self):
        status, _, antwort = self.post("/api/neu", {"typ": "concept", "slug": "zweite", "titel": "Zweite", "beschreibung": "Beschr."})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(antwort["id"], "concepts/zweite")
        self.assertEqual(self.seite("concepts/zweite").title, "Zweite")
        self.assertEqual([e for e in self.wiki().read_events() if e["kind"] == "create"][-1]["by"], "human:tester")
        for fehler in ({"typ": "concept", "slug": "zweite", "titel": "Z"}, {"typ": "gibtsnicht", "slug": "x", "titel": "X"},
                       {"typ": "concept", "slug": "Gross Klein", "titel": "X"}, {"typ": "concept", "slug": "y", "titel": ""}):
            self.assertIn(self.post("/api/neu", fehler)[0], (400, 422), fehler)

    def test_abbildung(self):
        if "abbildung" not in okfw.build_parser().format_help():
            self.skipTest("der Kern hat den Befehl abbildung noch nicht")
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'
        status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": svg})
        self.assertEqual(status, 200, antwort)
        self.assertTrue((self.root / "wiki" / "abbildungen" / "kasten.svg").is_file())
        self.assertEqual(self.post("/api/abbildung", {"name": "../x", "svg": svg})[0], 422)
        self.assertEqual(self.post("/api/abbildung", {"name": "boese", "svg": "<svg><script>1</script></svg>"})[0], 422)
        self.assertNotIn("WIKI_ACTOR", os.environ)

    def test_abbildung_laeuft_als_eigener_prozess(self):
        """Gefunden: os.environ im laufenden Server geändert; ein SystemExit des Kerns hieß fälschlich 501."""
        gesehen = {}

        def lauf(befehl, **kw):
            gesehen.update(befehl=befehl, env=kw["env"], datei=Path(befehl[befehl.index("--file") + 1]).read_text(encoding="utf-8"))
            self.assertNotIn("WIKI_ACTOR", os.environ)
            return subprocess.CompletedProcess(befehl, gesehen.get("rc", 0), "abgelegt", "")
        with mock.patch.object(okfw_serve.subprocess, "run", lauf):
            status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": "<svg/>"})
        self.assertEqual((status, antwort["ausgabe"]), (200, "abgelegt"), antwort)
        self.assertEqual(gesehen["befehl"][:6], [sys.executable, okfw.__file__, "--config", str(self.root / "wiki.toml"), "abbildung", "kasten"])
        self.assertEqual((gesehen["env"]["WIKI_ACTOR"], gesehen["datei"]), ("human:tester", "<svg/>"))
        self.assertEqual(gesehen["env"]["OKFW_EINSTELLUNGEN"], str(self.einstellungen))
        for rc, fehler, code, text in ((2, "okfw: kaputtes SVG", 422, "kaputtes SVG"),
                                       (2, "usage: okfw.py\nokfw.py: error: invalid choice: 'abbildung'", 422, "invalid choice"),
                                       (1, "Traceback (most recent call last):\n  File x\nKeyError: 'y'", 500, "KeyError: 'y'")):
            with self.subTest(rc=rc, fehler=fehler), mock.patch.object(okfw_serve.subprocess, "run",
                                                                       lambda b, **kw: subprocess.CompletedProcess(b, rc, "", fehler)):
                status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": "<svg/>"})
                self.assertEqual(status, code, antwort)
                self.assertIn(text, antwort["fehler"])
                self.assertNotIn("Traceback", antwort["fehler"])


class Ausgabe(ServeTest):
    def test_weitergegebene_dashboards_ohne_serve(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        normal, oeffentlich = self.base / "normal.html", self.base / "oeffentlich.html"
        self.kern("dashboard", "--out", str(normal))
        self.kern("dashboard", "--oeffentlich", "--out", str(oeffentlich))
        for pfad in (normal, oeffentlich):
            text = pfad.read_text(encoding="utf-8")
            for spur in ("OKFW_SERVE", "X-OKFW", "api/seite", "Neue Karte"):
                self.assertNotIn(spur, text, f"{pfad.name}: {spur}")
        status, _, html = self.anfrage("GET", "/")
        self.assertIn("OKFW_SERVE", html)


class Einstellungen(ServeTest):
    def test_antwort_enthaelt_keinen_key(self):
        status, _, daten = self.anfrage("GET", "/api/einstellungen")
        self.assertEqual(status, 200)
        text = json.dumps(daten)
        self.assertNotIn(GEHEIM, text)
        self.assertEqual(daten["anbieter"]["openai"]["schluessel"], "umgebung")
        self.assertEqual(daten["person"], "tester")
        self.assertEqual(daten["rollen"]["bearbeiten"]["modell"], "test-modell")
        self.assertIn("harnesse_info", daten)
        self.assertEqual(daten["daten"], "intern")
        for pfad in ("/api/design", "/?x=1"):
            self.assertNotIn(GEHEIM, json.dumps(self.anfrage("GET", pfad)[2]))

    def test_rolle_setzen_und_pruefen(self):
        status, _, daten = self.post("/api/einstellungen/rolle", {"rolle": "lesen", "harness": "direkt", "anbieter": "openai", "modell": "m1"})
        self.assertEqual(status, 200, daten)
        self.assertEqual(daten["rollen"]["lesen"]["modell"], "m1")
        self.assertIn("lesen", self.einstellungen.read_text(encoding="utf-8"))
        for falsch in ({"rolle": "gibts", "harness": "direkt", "anbieter": "openai", "modell": "m"},
                       {"rolle": "lesen", "harness": "gibts", "anbieter": "openai", "modell": "m"},
                       {"rolle": "lesen", "harness": "direkt", "anbieter": "gibts", "modell": "m"}):
            self.assertEqual(self.post("/api/einstellungen/rolle", falsch)[0], 422, falsch)

    def test_gleiches_modell_warnt(self):
        for rolle in ("lesen", "gegenpruefen"):
            self.post("/api/einstellungen/rolle", {"rolle": rolle, "harness": "direkt", "anbieter": "openai", "modell": "gleich"})
        daten = self.anfrage("GET", "/api/einstellungen")[2]
        self.assertTrue(any("dasselbe Modell" in w for w in daten["warnungen"]))

    def test_kennung_und_schluesselnamen_ueber_die_oberflaeche(self):
        """Nachgestellte Angriffe: Vorbefehl über den TOML-Pfad, Eingebautes kapern, beliebige Variable an fremde Adresse."""
        anbieter = "/api/einstellungen/anbieter"
        url = "https://api.example.org/v1"
        for falsch in ({"id": "eigen.vorbefehl.okfwprobe", "openai": url}, {"id": "anthropic.x", "anthropic": url},
                       {"id": "anthropic", "anthropic": url}, {"id": "boese", "openai": url, "schluessel_env": "GEHEIME_DB"},
                       {"id": "boese", "openai": url, "schluessel_env": "OPENAI_API_KEY"},
                       {"id": "boese", "openai": url, "schluessel": "openai-api-key"},
                       {"id": "boese", "openai": url, "schluessel": "ein-eintrag"}, {"id": "nur-name", "name": "x"}):
            with self.subTest(falsch=falsch):
                self.assertEqual(self.post(anbieter, falsch)[0], 422)
        self.assertNotIn("anbieter", okfw_serve.rollen.toml_lesen(self.einstellungen))
        self.assertEqual(self.anfrage("GET", "/api/einstellungen")[2]["anbieter_formen"]["anthropic"], {"anthropic": "https://api.anthropic.com"})

    def test_leere_datenklassen_bleiben_leer(self):
        anbieter = "/api/einstellungen/anbieter"
        status, _, daten = self.post(anbieter, {"id": "ohne", "openai": "https://x.example/v1", "daten": []})
        self.assertEqual(status, 200, daten)
        self.assertEqual(daten["anbieter_eigen_daten"]["ohne"], [])
        self.assertTrue(daten["anbieter"]["ohne"]["gesperrt"])
        status, _, daten = self.post(anbieter, {"id": "ohne", "openai": "https://x.example/v1"})   # fehlt: bleibt, wie es war
        self.assertEqual((status, daten["anbieter_eigen_daten"]["ohne"]), (200, []))
        status, _, daten = self.post(anbieter, {"id": "standard", "openai": "https://x.example/v1"})
        self.assertEqual((status, daten["anbieter_eigen_daten"]["standard"]), (200, ["oeffentlich", "intern"]))

    def test_sitzung_mit_anderem_anbieter_zeigt_die_sperre(self):
        status, _, daten = self.post("/api/einstellungen/rolle", {"rolle": "lesen", "harness": "sitzung", "anbieter": "ollama", "modell": "m"})
        self.assertEqual(status, 200, daten)
        self.assertIn("opencode", daten["rollen"]["lesen"]["gesperrt"])
        self.assertEqual(daten["sitzung"], "anthropic")
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        self.assertIn("S.sitzung", js)   # die Auswahl bietet die Kombination nicht an

    def test_falsche_typen_sind_nie_500(self):
        """Grundregel: Fehler von Eingaben sind 4xx mit Text."""
        d = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        routen = {"/api/seite": ({"id": "concepts/kern", "hash": d["hash"], "body": "x", "frontmatter": d["frontmatter"]}, ("id", "hash", "body", "frontmatter")),
                  "/api/neu": ({"typ": "concept", "slug": "neu-x", "titel": "T", "beschreibung": "B"}, ("typ", "slug", "titel", "beschreibung")),
                  "/api/freigabe": ({"ids": ["concepts/kern"]}, ("ids",)),
                  "/api/abbildung": ({"name": "n", "svg": "<svg/>"}, ("name", "svg")),
                  "/api/einstellungen/rolle": ({"rolle": "lesen", "harness": "direkt", "anbieter": "openai", "modell": "m"}, ("rolle", "harness", "anbieter", "modell")),
                  "/api/einstellungen/anbieter": ({"id": "typ", "openai": "https://x.example/v1", "daten": ["intern"]}, ("id", "name", "openai", "anthropic", "schluessel", "schluessel_env", "hinweis", "daten")),
                  "/api/einstellungen/person": ({"kennung": "tester"}, ("kennung",)),
                  "/api/design": ({"css": "a{}"}, ("css",)),
                  "/api/vorschlag": ({"id": "concepts/kern", "anweisung": "x"}, ("id", "anweisung", "body"))}
        with mock.patch.object(okfw_serve, "rollen_aufruf", lambda *a: (0, json.dumps({"text": "t"}), "")):
            for pfad, (gut, felder) in routen.items():
                for feld in felder:
                    for wert in (1, 1.5, True, [], {}, [1], {"a": 1}, [[]]):
                        with self.subTest(pfad=pfad, feld=feld, wert=wert):
                            aktuell = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]["hash"]
                            koerper = {**gut, feld: wert}
                            if pfad == "/api/seite" and feld != "hash":
                                koerper["hash"] = aktuell
                            status, _, antwort = self.post(pfad, koerper)
                            self.assertLess(status, 500, antwort)
                            if status >= 400:
                                self.assertTrue(antwort["fehler"])
        for wert in ("\ud800",):   # lässt sich nicht als UTF-8 speichern
            roh = json.dumps({"id": "concepts/kern", "hash": self.anfrage("GET", "/api/seite?id=concepts/kern")[2]["hash"], "body": wert})
            status, _, antwort = self.post("/api/seite", None, roh=roh)
            self.assertEqual(status, 422, antwort)
            status, _, antwort = self.post("/api/freigabe", None, roh=json.dumps({"ids": [wert]}))
            self.assertEqual(status, 404)
            status, _, antwort = self.post("/api/einstellungen/anbieter", None, roh=json.dumps({"id": "ersatz", "openai": "https://x.example/v1", "name": wert}))
            self.assertEqual(status, 422, antwort)
        self.assertEqual(self.post("/api/design", None, roh="[" * 200000)[0], 400)

    def test_modellliste_als_array_ist_ein_lesbarer_fehler(self):
        """Gefunden: Ein Anbieter, der eine Liste schickt, führte zu 500 mit AttributeError."""
        with mock.patch.object(urllib.request, "build_opener", lambda *h: mock.Mock(open=mock.Mock(return_value=io.BytesIO(b"[1, 2]")))):
            status, _, daten = self.anfrage("GET", "/api/modelle?anbieter=openai")
        self.assertEqual(status, 502)
        self.assertIn("unerwartete Antwort", daten["fehler"])

    def test_fehlertext_der_modellliste_ohne_key(self):
        """Nachgestellt: Der Anbieter spiegelt den Key, einmal genau an der Grenze der Kürzung."""
        for koerper in (GEHEIM.encode(), b"x" * 590 + GEHEIM.encode(), urllib.parse.quote(GEHEIM).encode()):
            with mock.patch.object(urllib.request, "build_opener", lambda *h: mock.Mock(open=mock.Mock(side_effect=urllib.error.HTTPError(
                    "https://api.openai.com/v1/models", 401, "nein", {}, io.BytesIO(koerper))))):
                status, _, daten = self.anfrage("GET", "/api/modelle?anbieter=openai")
            self.assertEqual(status, 502)
            self.assertNotIn(GEHEIM[:8], daten["fehler"])

    def test_anbieter_ohne_vorbefehl_und_nur_eigene(self):
        eintrag = {"id": "eigener", "name": "Eigener", "openai": "https://api.example.org/v1", "schluessel_env": "EIGENER_KEY",
                   "daten": ["oeffentlich"], "vorbefehl": ["rm", "-rf", "x"]}
        status, _, daten = self.post("/api/einstellungen/anbieter", eintrag)
        self.assertEqual(status, 200, daten)
        self.assertIn("eigener", daten["eigene"])
        self.assertNotIn("vorbefehl", self.einstellungen.read_text(encoding="utf-8"))
        self.assertEqual(self.post("/api/einstellungen/anbieter", {"id": "openai", "name": "X", "openai": "https://x.example/v1"})[0], 422)
        self.assertEqual(self.post("/api/einstellungen/anbieter", {"id": "x1", "name": "X", "openai": "http://fremd.example/v1"})[0], 422)
        self.assertEqual(self.post("/api/einstellungen/anbieter", {"id": "x2", "name": "X", "openai": "https://x.example/v1", "schluessel_env": "sk-abc def"})[0], 422)
        self.assertEqual(self.post("/api/einstellungen/anbieter", {"id": "openai", "entfernen": True})[0], 422)
        status, _, daten = self.post("/api/einstellungen/anbieter", {"id": "eigener", "entfernen": True})
        self.assertEqual(status, 200)
        self.assertNotIn("eigener", daten["eigene"])

    def test_person(self):
        self.assertEqual(self.post("/api/einstellungen/person", {"kennung": "mit leerzeichen"})[0], 422)
        status, _, daten = self.post("/api/einstellungen/person", {"kennung": "jdoe"})
        self.assertEqual((status, daten["person"]), (200, "jdoe"))

    def test_modelle_unbekannter_anbieter_404(self):
        self.assertEqual(self.anfrage("GET", "/api/modelle?anbieter=gibtsnicht")[0], 404)

    def test_modelle_ueber_rollen(self):
        with mock.patch.object(okfw_serve.rollen, "modelle", return_value=["a", "b"]) as m:
            status, _, daten = self.anfrage("GET", "/api/modelle?anbieter=openai")
        self.assertEqual((status, daten["modelle"]), (200, ["a", "b"]))
        self.assertEqual(m.call_args[0][1], "openai")

    def test_design_wiki_toml_bleibt_unberuehrt(self):
        toml = (self.root / "wiki.toml").read_bytes()
        status, _, d = self.anfrage("GET", "/api/design")
        self.assertEqual(status, 200)
        self.assertFalse(d["vorhanden"])
        self.assertIn("--accent", [v["name"] for v in d["variablen"]])
        self.assertEqual(self.post("/api/design", {"css": "@import url(https://x.example/a.css);"})[0], 422)
        self.assertEqual(self.post("/api/design", {"css": ".a { background: url(https://x.example/a.png) }"})[0], 422)
        self.assertEqual(self.post("/api/design", {"css": "a{} </style><script>"})[0], 422)
        status, _, antwort = self.post("/api/design", {"css": ":root { --accent: #123456; }\n"})
        self.assertEqual(status, 200, antwort)
        self.assertTrue((self.root / "design.css").is_file())
        self.assertIn("#123456", okfw.Wiki.load(config=self.root / "wiki.toml").design_css())
        self.assertEqual((self.root / "wiki.toml").read_bytes(), toml)

    def test_keine_nativen_dialoge(self):
        # confirm() unterdrücken eingebettete Browser; der Knopf täte dann still nichts
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"\b(confirm|alert|prompt)\(")

    def test_design_pfad_ausserhalb_abgelehnt(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[dashboard]\ndesign = "../aussen.css"\n')
        self.assertEqual(self.anfrage("GET", "/api/design")[0], 422)
        self.assertEqual(self.post("/api/design", {"css": "a{}"})[0], 422)
        self.assertFalse((self.base / "aussen.css").exists())

    def test_design_folgt_keinem_link(self):
        """Gefunden: design.css.tmp folgte einem Symlink und überschrieb dessen Ziel."""
        opfer = self.base / "opfer.txt"
        opfer.write_text("unberührt", encoding="utf-8")
        (self.root / "design.css.tmp").symlink_to(opfer)
        status, _, antwort = self.post("/api/design", {"css": "a{}"})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(opfer.read_text(encoding="utf-8"), "unberührt")
        self.assertEqual((self.root / "design.css").read_text(encoding="utf-8"), "a{}")
        self.assertEqual(oct((self.root / "design.css").stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".design")], [])

    def test_design_als_link_wird_abgelehnt(self):
        anderes = self.root / "anderes.css"
        anderes.write_text("b{}", encoding="utf-8")
        (self.root / "design.css").symlink_to(anderes)
        status, _, antwort = self.post("/api/design", {"css": "a{}"})
        self.assertEqual(status, 422, antwort)
        self.assertIn("symbolischer Link", antwort["fehler"])
        self.assertEqual(anderes.read_text(encoding="utf-8"), "b{}")
        self.assertEqual(self.anfrage("GET", "/api/design")[0], 422)

    def test_design_in_fehlendem_ordner_422(self):
        """Gefunden: [dashboard] design in einem Ordner, den es nicht gibt, endete als 500 statt klarer Meldung."""
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[dashboard]\ndesign = "styles/eigen.css"\n')
        status, _, antwort = self.post("/api/design", {"css": "a{}"})
        self.assertEqual(status, 422, antwort)
        self.assertIn("styles", antwort["fehler"])
        self.assertIn("Ordner", antwort["fehler"])
        self.assertFalse((self.root / "styles").exists())
        status, _, antwort = self.anfrage("GET", "/api/design")
        self.assertEqual((status, antwort["css"]), (200, ""))   # Lesen bleibt möglich

    def test_dashboard_fehler_ist_eine_lesbare_seite(self):
        """Gefunden: Scheiterte der Bau der Seite, sah die Person JSON oder eine leere Antwort. WikiError und die
        eigene Fehler-Klasse sind für die Person geschrieben und stehen maskiert im Klartext."""
        for fehler, text in ((None, "Daten-Marke"), (okfw.WikiError("Karte kaputt <b>"), "Karte kaputt &lt;b&gt;")):
            art = {"return_value": "<html>ohne Marke</html>"} if fehler is None else {"side_effect": fehler}
            with self.subTest(fehler=fehler), mock.patch.object(okfw.Wiki, "render_dashboard", **art):
                status, antwort, html = self.anfrage("GET", "/")
                self.assertEqual(status, 500)
                self.assertIn("text/html", antwort.getheader("Content-Type"))
                self.assertIn("ließ sich nicht bauen", html)
                self.assertIn(text, html)
                self.assertNotIn("Traceback", html)
                self.assertNotIn("<b>", html)

    def test_dashboard_fehler_ohne_wikierror_nennt_nur_den_typnamen(self):
        """Gefunden: str() einer OSError konnte den Pfad der Wiki-Datei zeigen; unerwartete Ausnahmen nennen nur den Typ."""
        for fehler in (OSError(2, "Datei fehlt", "/privat/pfad/wiki/concepts/kern.md"),
                       KeyError("geheimer wert")):
            with self.subTest(fehler=fehler), mock.patch.object(
                    okfw.Wiki, "render_dashboard", side_effect=fehler), contextlib.redirect_stderr(io.StringIO()) as err:
                status, antwort, html = self.anfrage("GET", "/")
                self.assertEqual(status, 500)
                self.assertIn("ließ sich nicht bauen", html)
                self.assertIn(type(fehler).__name__, html)          # der Typname steht im Klartext
                self.assertIn("Einzelheiten stehen im Terminal", html)
                self.assertNotIn("Traceback", html)
                self.assertNotIn("Datei fehlt", html)               # kein Text der Ausnahme
                self.assertNotIn("geheimer wert", html)
                self.assertNotIn("/privat/pfad", html)
                self.assertIn(type(fehler).__name__ + ": ", err.getvalue())   # stderr bleibt vollständig
                self.assertIn(str(fehler), err.getvalue())
        with mock.patch.object(okfw.Wiki, "render_dashboard", side_effect=okfw.WikiError("Karte kaputt <b>")):
            status, antwort, html = self.anfrage("GET", "/")
        self.assertIn("Karte kaputt &lt;b&gt;", html)   # die WikiError bleibt Klartext und maskiert
        self.assertNotIn("<b>", html)


class Vorschlag(ServeTest):
    ANTWORT = {"harness": "direkt", "anbieter": "openai", "modell": "test-modell", "dauer_s": 0.1, "rolle": "bearbeiten"}

    def aufruf(self, text):
        gesehen = {}

        def attrappe(argv, ordner):
            gesehen["argv"] = argv
            gesehen["ordner"] = ordner
            gesehen["modus"] = oct(os.stat(ordner).st_mode & 0o777)
            anhaenge = [argv[i + 1] for i, a in enumerate(argv) if a == "--anhang"]
            gesehen["anhaenge"] = {Path(p).name: Path(p).read_text(encoding="utf-8") for p in anhaenge}
            gesehen["auftrag"] = Path(argv[argv.index("--datei") + 1]).read_text(encoding="utf-8")
            return 0, json.dumps({**self.ANTWORT, "text": text}), ""

        return attrappe, gesehen

    def test_vorschlag_speichert_nichts_und_sendet_nur_die_seite(self):
        pfad = self.datei("concepts/kern")
        pfad.write_text(pfad.read_text(encoding="utf-8").rstrip() + "\n\nBeleg.[^q1]\n\n[^q1]: [Quelle 1](/sources/q1.md)\n", encoding="utf-8")
        vorher = pfad.read_bytes()
        ereignisse = len(self.wiki().read_events())
        neu = "# Kern\n\nKürzer, aber mit neuer Behauptung.[^erfunden]\n"
        attrappe, gesehen = self.aufruf(neu)
        with mock.patch.object(okfw_serve, "rollen_aufruf", attrappe):
            status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "Kürze die Seite."})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(antwort["vorschlag"], neu.strip())
        self.assertEqual(pfad.read_bytes(), vorher)
        self.assertEqual(len(self.wiki().read_events()), ereignisse)
        warnungen = " ".join(antwort["warnungen"])
        self.assertIn("[^q1]", warnungen)            # verschwunden
        self.assertIn("[^erfunden]", warnungen)      # neu
        self.assertIn("sources-Eintrag", warnungen)  # ohne Eintrag in den Eigenschaften
        # Aufruf: rollen.py über die Befehlszeile, nur die Seite als Anhang, privater Ordner, danach aufgeräumt
        argv = gesehen["argv"]
        self.assertIn("--json", argv)
        self.assertEqual(argv[argv.index("--rolle") + 1], "bearbeiten")
        self.assertEqual(argv[argv.index("--wiki") + 1], str(self.root / "wiki.toml"))
        self.assertEqual(list(gesehen["anhaenge"]), ["seite.md"])
        self.assertIn("Beleg.[^q1]", gesehen["anhaenge"]["seite.md"])
        self.assertNotIn("title: Kern", gesehen["anhaenge"]["seite.md"])   # kein Frontmatter
        self.assertIn("nur Markdown, ohne Frontmatter", gesehen["auftrag"])
        self.assertEqual(gesehen["modus"], "0o700")
        self.assertFalse(Path(gesehen["ordner"]).exists())
        self.assertEqual(antwort["gesendet"], {"seite": "concepts/kern", "zeichen": len(self.seite("concepts/kern").body.strip("\n")), "anhaenge": 1})

    def test_speichern_nennt_rolle_und_modell(self):
        text = "# Kern\n\nVorgeschlagener Text.\n"
        attrappe, _ = self.aufruf(text)
        with mock.patch.object(okfw_serve, "rollen_aufruf", attrappe):
            self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "Mach neu."})
        d = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text, "vorschlag": True})
        self.assertEqual(status, 200, antwort)
        detail = [e for e in self.wiki().read_events() if e["kind"] == "update"][-1]["detail"]
        self.assertIn("test-modell", detail)
        self.assertIn("bearbeiten", detail)
        self.assertIn("unverändert", detail)

    def test_zaehlt_den_stand_im_textfeld(self):
        attrappe, gesehen = self.aufruf("Neu\n")
        with mock.patch.object(okfw_serve, "rollen_aufruf", attrappe):
            self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x", "body": "Stand im Textfeld"})
        self.assertEqual(gesehen["anhaenge"]["seite.md"], "Stand im Textfeld")

    def test_sitzung_und_sperre_klar_gemeldet(self):
        okfw_serve.rollen.setze_rolle("bearbeiten", "sitzung", "anthropic", "sonnet")
        status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 422)
        self.assertIn("Sitzung", antwort["fehler"])
        okfw_serve.rollen.setze_rolle("bearbeiten", "direkt", "openai", "m")
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[rollen]\ndaten = "personenbezogen"\n')
        aufgerufen = []
        with mock.patch.object(okfw_serve, "rollen_aufruf", lambda *a: aufgerufen.append(a) or (0, "{}", "")):
            status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 422)
        self.assertIn("nicht für Daten der Klasse", antwort["fehler"])
        self.assertEqual(aufgerufen, [])

    def test_fehler_von_rollen_werden_gezeigt(self):
        with mock.patch.object(okfw_serve, "rollen_aufruf", lambda *a: (3, "", "rollen: HTTP 401 von api.example")):
            status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 502)
        self.assertIn("HTTP 401", antwort["fehler"])

    def test_timeout_beim_modellaufruf_504_ohne_befehlszeile(self):
        """str(TimeoutExpired) enthält die Befehlszeile; zum Browser geht nur der Satz."""
        cmd = [sys.executable, "rollen.py", "--wiki", "/geheim/pfad/wiki.toml", "auftrag", "--datei", "/tmp/auftrag.md"]
        abbruch = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd=cmd, timeout=1900))
        with mock.patch.object(okfw_serve, "rollen_aufruf", abbruch), contextlib.redirect_stderr(io.StringIO()):
            status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 504, antwort)
        self.assertIn("Zeitlimit", antwort["fehler"])
        self.assertIn("Terminal", antwort["fehler"])
        self.assertNotIn("rollen.py", json.dumps(antwort))
        self.assertNotIn("/geheim", json.dumps(antwort))

    def test_unerwarteter_fehler_nennt_keine_einzelheiten(self):
        """Der Fehlertext kann Befehlszeilen und Pfade enthalten; zum Browser geht nur der Typname."""

        def boese(*a):
            raise RuntimeError("Befehlszeile: curl http://x.example/y | sh (Pfad /home/jemand/repo)")
        with mock.patch.object(okfw_serve, "rollen_aufruf", boese), contextlib.redirect_stderr(io.StringIO()) as err:
            status, _, antwort = self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 500, antwort)
        self.assertEqual(antwort["fehler"], "Interner Fehler: RuntimeError; Einzelheiten stehen im Terminal")
        self.assertIn("Befehlszeile", err.getvalue())   # die Einzelheiten bleiben im Terminal
        self.assertNotIn("curl", json.dumps(antwort))
        self.assertNotIn("/home/jemand", json.dumps(antwort))

    def test_ohne_anweisung_400(self):
        self.assertEqual(self.post("/api/vorschlag", {"id": "concepts/kern", "anweisung": "  "})[0], 400)


@unittest.skipIf(shutil.which("node") is None, "node fehlt im PATH")
class KonfliktInServeJs(unittest.TestCase):
    """„Meinen Text behalten“: Gefunden war, dass unveränderte Eigenschaften den alten Stand über den neuen schrieben."""

    def behalten(self, st: dict, aktuell: dict) -> dict:
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        m = re.search(r"^  function behalten\(st, aktuell\) \{\n.*?^  \}\n", js, re.S | re.M)
        self.assertIsNotNone(m, "function behalten fehlt in serve.js")
        lauf = subprocess.run(["node", "-e", f"{m.group(0)}\nconst st = {json.dumps(st)};\nconst eigene = behalten(st, {json.dumps(aktuell)});\n"
                                             "console.log(JSON.stringify({st, eigene}));"], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        return json.loads(lauf.stdout)

    AKTUELL = {"hash": "neu", "body": "ihr Text", "frontmatter": "title: Ihr\n", "felder": {"title": "Ihr"}, "status": "draft"}

    def test_unveraenderte_eigenschaften_folgen_dem_aktuellen_stand(self):
        st = {"hash": "alt", "body0": "alt", "body": "mein Text", "fm0": "title: Alt\n", "fm": "title: Alt\n", "felder": {"title": "Alt"}, "status": "stable"}
        aus = self.behalten(st, self.AKTUELL)
        self.assertFalse(aus["eigene"])
        self.assertEqual((aus["st"]["fm"], aus["st"]["fm0"]), ("title: Ihr\n", "title: Ihr\n"))   # das Speichern schickt sie nicht mit
        self.assertEqual((aus["st"]["felder"], aus["st"]["status"]), ({"title": "Ihr"}, "draft"))
        self.assertEqual((aus["st"]["body"], aus["st"]["body0"], aus["st"]["hash"]), ("mein Text", "ihr Text", "neu"))

    def test_geaenderte_eigenschaften_bleiben_und_werden_gemeldet(self):
        st = {"hash": "alt", "body0": "alt", "body": "mein Text", "fm0": "title: Alt\n", "fm": "title: Mein\n", "felder": {"title": "Alt"}, "status": "stable"}
        aus = self.behalten(st, self.AKTUELL)
        self.assertTrue(aus["eigene"])
        self.assertEqual((aus["st"]["fm"], aus["st"]["fm0"]), ("title: Mein\n", "title: Ihr\n"))
        self.assertEqual(aus["st"]["status"], "draft")

    def test_anzeige_wird_nach_jedem_wechsel_erneuert(self):
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(js.count("statusZeigen();"), 4)   # Start, Speichern, Laden, Behalten
        self.assertIn("eigene ?", js)


class StartUrlAblauf(ServeTest):
    def test_ablauf_mit_urllib(self):
        """Start-URL → Cookie → Weiterleitung auf / → Seite → API; ohne Cookie bleibt alles zu."""
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        basis = f"http://127.0.0.1:{self.port}/{self.praefix}"
        with opener.open(f"{basis}/?t={TOKEN}") as antwort:
            self.assertEqual(antwort.geturl(), f"{basis}/")
            self.assertIn(b"OKFW_SERVE", antwort.read())
        self.assertEqual([c.name for c in jar], [self.server.sitzung.cookie])
        self.assertEqual([c.path for c in jar], [f"/{self.praefix}/"])
        with opener.open(f"{basis}/api/seite?id=concepts/kern") as antwort:
            self.assertEqual(json.load(antwort)["id"], "concepts/kern")
        with self.assertRaises(urllib.error.HTTPError) as fehler:
            urllib.request.urlopen(f"{basis}/api/seite?id=concepts/kern")
        self.assertEqual(fehler.exception.code, 403)


@unittest.skipIf(UV is None, "uv fehlt im PATH")
class Befehl(unittest.TestCase):
    """Der Befehl selbst als Prozess: `okfw.py serve --port N --no-open`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="okfw-serve-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "projekt"
        self.root.mkdir()
        self.env = clean_env(OKFW_EINSTELLUNGEN=str(self.base / "einstellungen.toml"))

    def core(self, *args, root=None):
        return subprocess.Popen([UV, "run", "--quiet", "--script", str(CORE), "--root", str(root or self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.base)

    def test_ohne_wiki_toml_bedienfehler(self):
        proc = self.core("serve", "--no-open")
        _, fehler = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("wiki.toml", fehler)

    def test_start_url_und_beenden(self):
        init = self.core("init")
        init.communicate(timeout=120)
        self.assertEqual(init.returncode, 0)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = self.core("serve", "--port", str(port), "--no-open")
        wacht = threading.Timer(120, proc.kill)
        wacht.start()
        self.addCleanup(wacht.cancel)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        url = ""
        for zeile in proc.stdout:
            if zeile.strip().startswith("http://127.0.0.1:"):
                url = zeile.strip()
                break
        self.assertRegex(url, rf"^http://127\.0\.0\.1:{port}/[\w-]+/\?t=")
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(url) as antwort:
            html = antwort.read().decode("utf-8")
        self.assertIn("OKFW_SERVE", html)
        for pfad, code in ((f"{url.split('?')[0]}api/einstellungen", 403), (f"http://127.0.0.1:{port}/api/einstellungen", 404)):
            with self.assertRaises(urllib.error.HTTPError) as fehler:
                urllib.request.urlopen(pfad)
            self.assertEqual(fehler.exception.code, code, pfad)
        proc.terminate()
        proc.communicate(timeout=30)


@unittest.skipUnless(shutil.which("node"), "node fehlt")
class FeldSkalar(unittest.TestCase):
    def wert(self, feld: str, text: str, orig) -> str:
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        code = js[js.index("function yamlSkalar"):js.index("function quelleEintragen")]
        aufruf = f"console.log(feldSkalar({json.dumps(feld)}, {json.dumps(text)}, {json.dumps(orig)}))"
        return subprocess.run(["node", "-e", code + aufruf], capture_output=True, text=True, check=True).stdout.strip()

    def test_order_wird_zahl(self):
        self.assertEqual(self.wert("order", "1", ""), "1")
        self.assertEqual(self.wert("order", " 2 ", "1"), "2")

    def test_order_ohne_zahl_bleibt_text(self):
        self.assertEqual(self.wert("order", "abc", ""), "abc")

    def test_andere_felder_bleiben_text(self):
        self.assertEqual(self.wert("jahr", "1", ""), '"1"')
        self.assertEqual(self.wert("jahr", "2", 1), "2")


class AbgebrochenerAbruf(unittest.TestCase):
    def abruf(self, *antworten):
        mit = mock.MagicMock()
        mit.__enter__.return_value.read.side_effect = antworten
        with mock.patch("urllib.request.urlopen", return_value=mit), mock.patch("time.sleep"):
            return okfw.http_json("https://api.crossref.org/works/x")

    def test_abbruch_wird_wiederholt(self):
        self.assertEqual(self.abruf(http.client.IncompleteRead(b"{"), b'{"a": 1}'), {"a": 1})

    def test_dauerhafter_abbruch_ist_netzfehler(self):
        with self.assertRaises(urllib.error.URLError):
            self.abruf(*[http.client.IncompleteRead(b"{")] * 3)


if __name__ == "__main__":
    unittest.main()
