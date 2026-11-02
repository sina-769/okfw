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
import time
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
GUTES_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'
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

    def auftrag_abwarten(self, kennung, bis=("fertig", "gescheitert", "abgebrochen")):
        frist = time.monotonic() + 30
        while time.monotonic() < frist:
            d = self.anfrage("GET", f"/api/auftrag?id={kennung}")[2]
            if d["zustand"] in bis:
                return d
            time.sleep(0.02)
        self.fail(f"Auftrag {kennung} blieb in {d['zustand']}")

    def vorschlag(self, **daten):
        """Startet „Bearbeiten per Prompt“ und wartet: (Status des Starts, Auftrag oder Fehler)."""
        status, _, antwort = self.post("/api/auftrag", {"typ": "bearbeiten", "id": "concepts/kern", **daten})
        return status, self.auftrag_abwarten(antwort["id"]) if status == 202 else antwort

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
        ohne = lambda s: "".join(z for z in s.splitlines(keepends=True) if not z.startswith(("generated:", "  by:", "  at:", "  modelle:", "  - {")))
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

    def test_freigabe_ohne_gegenpruefung_fragt_nach(self):
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"]})
        self.assertEqual(status, 409)
        self.assertEqual(antwort["ohne_gegenpruefung"], ["concepts/kern"])
        self.assertIn("Gegenprüfung", antwort["fehler"])
        for wort in ("--force", "verify", "Prozess"):
            self.assertNotIn(wort, antwort["fehler"])
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")
        self.assertNotIn("verified", self.seite("concepts/kern").fm)

    def test_trotzdem_freigeben_bleibt_als_ohne_gegenpruefung_sichtbar(self):
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"], "ohne_gegenpruefung": ["concepts/kern"]})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(antwort["ohne_gegenpruefung"], ["concepts/kern"])
        wiki = self.wiki()
        p = self.seite("concepts/kern")
        self.assertEqual(p.fm["status"], "stable")
        self.assertIsNone(wiki.release_state(p)["check"])
        self.assertNotEqual(wiki.release_state(p)["tier"], "machine-confirmed")
        ereignis = json.loads((self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertIn("freigegeben ohne Gegenprüfung", ereignis["detail"])
        self.assertIn("human_without_check", [f.code for f in wiki.lint()])
        knoten = next(n for n in wiki.dashboard_data()["nodes"] if n["id"] == "concepts/kern")
        self.assertTrue(knoten["ohne_gegenpruefung"])

    def test_bestaetigung_gilt_nur_den_genannten_seiten(self):
        self.kern("new", "concept", "zwei", "--title", "Zwei", "--description", "d")
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern", "concepts/zwei"], "ohne_gegenpruefung": ["concepts/kern"]})
        self.assertEqual(status, 409, antwort)
        self.assertEqual(antwort["ohne_gegenpruefung"], ["concepts/zwei"])
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")
        for falsch in (True, "concepts/kern", [1]):
            self.assertEqual(self.post("/api/freigabe", {"ids": ["concepts/kern"], "ohne_gegenpruefung": falsch})[0], 400, falsch)
        self.assertEqual(self.seite("concepts/kern").fm["status"], "draft")

    def test_mit_gegenpruefung_fragt_nichts_und_vermerkt_nichts(self):
        self.gegenpruefen("concepts/kern")
        status, _, antwort = self.post("/api/freigabe", {"ids": ["concepts/kern"]})
        self.assertEqual((status, antwort["ohne_gegenpruefung"]), (200, []))
        ereignis = json.loads((self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("ohne Gegenprüfung", ereignis["detail"])
        knoten = next(n for n in self.wiki().dashboard_data()["nodes"] if n["id"] == "concepts/kern")
        self.assertFalse(knoten["ohne_gegenpruefung"])

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
            status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": GUTES_SVG})
        self.assertEqual((status, antwort["ausgabe"]), (200, "abgelegt"), antwort)
        self.assertEqual(gesehen["befehl"][:6], [sys.executable, okfw.__file__, "--config", str(self.root / "wiki.toml"), "abbildung", "kasten"])
        self.assertEqual((gesehen["env"]["WIKI_ACTOR"], gesehen["datei"]), ("human:tester", GUTES_SVG))
        self.assertEqual(gesehen["env"]["OKFW_EINSTELLUNGEN"], str(self.einstellungen))
        for rc, fehler, code, text in ((2, "okfw: kaputtes SVG", 422, "kaputtes SVG"),
                                       (2, "usage: okfw.py\nokfw.py: error: invalid choice: 'abbildung'", 422, "invalid choice"),
                                       (1, "Traceback (most recent call last):\n  File x\nKeyError: 'y'", 500, "KeyError: 'y'")):
            with self.subTest(rc=rc, fehler=fehler), mock.patch.object(okfw_serve.subprocess, "run",
                                                                       lambda b, **kw: subprocess.CompletedProcess(b, rc, "", fehler)):
                status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": GUTES_SVG})
                self.assertEqual(status, code, antwort)
                self.assertIn(text, antwort["fehler"])
                self.assertNotIn("Traceback", antwort["fehler"])

    def test_abbildung_gleicher_name_fragt_und_schreibt_nicht(self):
        datei = self.root / "wiki" / "abbildungen" / "kasten.svg"
        self.assertEqual(self.post("/api/abbildung", {"name": "kasten", "svg": GUTES_SVG})[0], 200)
        alt = datei.read_text(encoding="utf-8")
        anders = GUTES_SVG.replace("<rect", '<rect id="neu"')
        status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": anders})
        self.assertEqual(status, 409, antwort)
        self.assertNotIn("--ersetzen", antwort["fehler"])
        self.assertEqual((antwort["vorhanden"], antwort["vorschlag"]), (True, "kasten-2"))
        self.assertEqual(datei.read_text(encoding="utf-8"), alt)
        # der vorgeschlagene Name ist wirklich frei, auch bei Lücken und von dort aus
        self.assertEqual(self.post("/api/abbildung", {"name": "kasten-2", "svg": GUTES_SVG})[0], 200)
        self.assertEqual(self.post("/api/abbildung", {"name": "kasten-2", "svg": anders})[2]["vorschlag"], "kasten-3")
        self.assertEqual(self.post("/api/abbildung", {"name": "kasten", "svg": anders})[2]["vorschlag"], "kasten-3")
        status, _, antwort = self.post("/api/abbildung", {"name": "kasten", "svg": anders, "ersetzen": True})
        self.assertEqual((status, antwort["ersetzt"]), (200, True), antwort)
        self.assertIn('id="neu"', datei.read_text(encoding="utf-8"))
        self.assertEqual([e["kind"] for e in self.wiki().read_events() if e["page"] == "abbildungen/kasten"], ["create", "update"])

    def test_abbildung_pruefen_schreibt_nichts_und_nennt_alles(self):
        ordner = self.root / "wiki" / "abbildungen"
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 9 9"><style>.a{fill:red}</style>'
               '<linearGradient id="g"/><rect class="a"/><image href="http://evil.example/x.png"/>'
               '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">HTML</div></foreignObject></svg>')
        status, _, d = self.post("/api/abbildung", {"name": "neu", "svg": svg, "pruefen": True})
        self.assertEqual(status, 200, d)
        self.assertFalse((ordner / "neu.svg").exists())
        self.assertFalse(d["vorhanden"])
        self.assertEqual(d["sichtbar"], ["Element foreignObject", "Element image"])
        self.assertEqual(d["ohne_wirkung"], ["Attribut version"])
        self.assertTrue(any("draw.io" in t for t in d["hinweise"]))
        self.assertIn('style="fill:red"', d["svg"])
        self.assertIn('id="vorschau-g"', d["svg"])   # stört die ids der Seite nicht
        self.assertNotIn("evil.example", d["svg"])
        # beim Ablegen dieselbe Auskunft, die der Kern sonst nur nach stderr schreibt
        status, _, d = self.post("/api/abbildung", {"name": "neu", "svg": svg})
        self.assertEqual(status, 200, d)
        self.assertEqual(d["sichtbar"], ["Element foreignObject", "Element image"])
        self.assertFalse(d["ersetzt"])
        self.seite_mit_bild()
        status, _, d = self.post("/api/abbildung", {"name": "neu", "svg": svg, "pruefen": True})
        self.assertEqual((d["vorhanden"], d["vorschlag"], d["genutzt_auf"]), (True, "neu-2", ["Kern"]))

    def seite_mit_bild(self):
        akt = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": akt["hash"], "frontmatter": akt["frontmatter"],
                                                      "body": akt["body"] + "\n\n![Bild](/abbildungen/neu.svg)\n*Abb.: Bild.*\n"})
        self.assertEqual(status, 200, antwort)

    def test_abbildung_abgelehntes_sagt_warum_ohne_pfad(self):
        for svg, text in (('<!DOCTYPE svg [<!ENTITY x "y">]><svg viewBox="0 0 1 1"/>', "Standardkopf"), ('<svg width="100%" height="9"/>', "viewBox="),
                          ("<svg><g></svg>", "kein gültiges XML"), ("\ud800", "UTF-8")):
            for pruefen in (True, False):
                with self.subTest(svg=svg, pruefen=pruefen):
                    status, _, antwort = self.post("/api/abbildung", {"name": "boese", "svg": svg, "pruefen": pruefen})
                    self.assertEqual(status, 422, antwort)
                    self.assertIn(text, antwort["fehler"])
                    self.assertNotIn("okfw-abb-", antwort["fehler"])

    def test_abbildung_dialog_kennt_keine_kommandozeile(self):
        js = (okfw_serve.Path(okfw_serve.__file__).parent / "templates" / "serve.js").read_text(encoding="utf-8")
        self.assertNotIn("--ersetzen", js)
        self.assertIn('pruefen: true', js)
        self.assertIn('fatal: true', js)   # f.text() machte aus Latin-1 stillschweigend Ersatzzeichen
        self.assertIn('vor.endsWith("\\n\\n")', js)   # das Bild muss ein eigener Absatz sein, sonst zeigt das Dashboard es nicht


class FreigabeInServeJs(unittest.TestCase):
    """Der Ablauf in freigeben(): frage() und api() sind Attrappen; api antwortet nach der Vorgabe des Tests."""

    def lauf(self, antworten, server):
        js = (SKILL / "templates" / "serve.js").read_text(encoding="utf-8")
        m = re.search(r"^  async function freigeben\(ids\) \{\n.*?^  \}\n", js, re.S | re.M)
        self.assertIsNotNone(m, "function freigeben fehlt in serve.js")
        rahmen = (f"const fragen = [], posts = [], meldungen = [], antworten = {json.dumps(antworten)}, server = {json.dumps(server)};\n"
                  "const frage = async (text, ja, nein) => { fragen.push({text, ja, nein}); return antworten.shift() ?? false; };\n"
                  "const api = async (pfad, body) => { posts.push(body); return server.shift(); };\n"
                  "const node = id => ({title: 'Titel ' + id});\n"
                  "const say = text => meldungen.push(text);\n"
                  "const fail = r => meldungen.push('Fehler: ' + r.data.fehler);\n"
                  "const reloadTo = () => {};\n"
                  "globalThis.setTimeout = () => {};\n"
                  f"{m.group(0)}\n"
                  "freigeben(['a']).then(() => console.log(JSON.stringify({fragen, posts, meldungen})));")
        lauf = subprocess.run(["node", "-e", rahmen], capture_output=True, text=True, timeout=60)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        return json.loads(lauf.stdout)

    FEHLT = {"ok": False, "status": 409, "data": {"fehler": "x", "ohne_gegenpruefung": ["a"]}}
    FREI = {"ok": True, "status": 200, "data": {"freigegeben": ["a"], "ohne_gegenpruefung": ["a"]}}

    def test_trotzdem_freigeben_schickt_die_bestaetigung(self):
        aus = self.lauf([True, True], [self.FEHLT, self.FREI])
        self.assertEqual(aus["posts"], [{"ids": ["a"]}, {"ids": ["a"], "ohne_gegenpruefung": ["a"]}])
        frage = aus["fragen"][1]
        self.assertEqual(frage["ja"], "Trotzdem freigeben")
        self.assertIn("Titel a", frage["text"])
        self.assertIn("keine Gegenprüfung", frage["text"])
        self.assertNotIn("Prozess", frage["text"])
        self.assertIn("ohne Gegenprüfung", aus["meldungen"][0])

    def test_abbrechen_schickt_keine_bestaetigung(self):
        aus = self.lauf([True, False], [self.FEHLT])
        self.assertEqual(aus["posts"], [{"ids": ["a"]}])
        self.assertEqual(aus["meldungen"], [])

    def test_mit_gegenpruefung_nur_eine_frage(self):
        aus = self.lauf([True], [{"ok": True, "status": 200, "data": {"freigegeben": ["a"], "ohne_gegenpruefung": []}}])
        self.assertEqual(len(aus["fragen"]), 1)
        self.assertNotIn("ohne Gegenprüfung", aus["meldungen"][0])


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
                  "/api/abbildung": ({"name": "n", "svg": "<svg/>"}, ("name", "svg", "ersetzen", "pruefen")),
                  "/api/einstellungen/rolle": ({"rolle": "lesen", "harness": "direkt", "anbieter": "openai", "modell": "m"}, ("rolle", "harness", "anbieter", "modell")),
                  "/api/einstellungen/anbieter": ({"id": "typ", "openai": "https://x.example/v1", "daten": ["intern"]}, ("id", "name", "openai", "anthropic", "schluessel", "schluessel_env", "hinweis", "daten")),
                  "/api/einstellungen/person": ({"kennung": "tester"}, ("kennung",)),
                  "/api/design": ({"css": "a{}"}, ("css",)),
                  "/api/auftrag": ({"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "x"}, ("typ", "id", "anweisung", "body")),
                  "/api/auftrag/abbrechen": ({"auftrag": "x"}, ("auftrag",)),
                  "/api/vorschlag/annehmen": ({"auftrag": "x", "aenderung": "a1"}, ("auftrag", "aenderung", "ziel")),
                  "/api/vorschlag/verwerfen": ({"auftrag": "x"}, ("auftrag", "aenderung"))}
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: (0, json.dumps({"text": "t"}), "")):
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

        def attrappe(argv, ordner, abbruch=None):
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
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            status, antwort = self.vorschlag(anweisung="Kürze die Seite.")
        self.assertEqual(status, 202, antwort)
        self.assertEqual(antwort["zustand"], "fertig", antwort)
        self.assertEqual(antwort["aenderungen"][0]["daten"]["neu"], neu.strip())
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
        self.assertEqual(antwort["gesendet"], {"seite": "concepts/kern", "zeichen": len(self.seite("concepts/kern").body.strip("\n")), "anhaenge": 1, "hinweis": "Gesendet wurde nur diese Seite"})

    def test_speichern_nennt_rolle_und_modell(self):
        text = "# Kern\n\nVorgeschlagener Text.\n"
        attrappe, _ = self.aufruf(text)
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            self.vorschlag(anweisung="Mach neu.")
        d = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text, "vorschlag": True})
        self.assertEqual(status, 200, antwort)
        detail = [e for e in self.wiki().read_events() if e["kind"] == "update"][-1]["detail"]
        self.assertIn("test-modell", detail)
        self.assertIn("bearbeiten", detail)
        self.assertIn("unverändert", detail)

    def vorschlag_holen(self, text):
        attrappe, _ = self.aufruf(text)
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            self.vorschlag(anweisung="Mach neu.")
        return self.anfrage("GET", "/api/seite?id=concepts/kern")[2]

    def herkunft(self):
        return self.seite("concepts/kern").fm["generated"]

    def test_uebernommener_vorschlag_bleibt_im_frontmatter(self):
        text = "# Kern\n\nVorgeschlagener Text.\n"
        d = self.vorschlag_holen(text)
        self.assertEqual(self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text, "vorschlag": True})[0], 200)
        gen = self.herkunft()
        self.assertEqual(gen["by"], "human:tester")   # die menschliche Übernahme bleibt erkennbar
        e = gen["modelle"][-1]
        self.assertEqual({k: e[k] for k in ("harness", "anbieter", "modell", "rolle", "art", "by")},
                         {"harness": "direkt", "anbieter": "openai", "modell": "test-modell", "rolle": "bearbeiten",
                          "art": "vorschlag", "by": "human:tester"})
        self.assertEqual(e["body_sha256"], okfw.body_sha256(text))

    def test_ueberarbeiteter_vorschlag_und_spaeteres_bearbeiten(self):
        d = self.vorschlag_holen("# Kern\n\nVorschlag.\n")
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "# Kern\n\nÜberarbeitet.\n", "vorschlag": True})
        self.assertEqual(status, 200, antwort)
        self.assertEqual(self.herkunft()["modelle"][-1]["art"], "vorschlag-bearbeitet")
        status, _, antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": antwort["stand"]["hash"], "body": "# Kern\n\nVon Hand.\n"})
        self.assertEqual(status, 200, antwort)
        self.assertIn("test-modell", [e["modell"] for e in self.herkunft()["modelle"]])

    def test_von_hand_kopierter_vorschlag_zaehlt(self):
        text = "# Kern\n\nKopiert.\n"
        d = self.vorschlag_holen(text)
        self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text})
        self.assertEqual(self.herkunft()["modelle"][-1]["art"], "vorschlag")

    def test_verworfener_vorschlag_hinterlaesst_nichts(self):
        d = self.vorschlag_holen("# Kern\n\nVerworfen.\n")
        self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "# Kern\n\nEigener Text.\n"})
        self.assertNotIn("test-modell", [e.get("modell") for e in self.herkunft().get("modelle", [])])

    def test_ohne_vorschlagsdatei_bleibt_die_kennzeichnung(self):
        d = self.vorschlag_holen("# Kern\n\nVor dem Neustart.\n")
        self.server.sitzung.auftraege.auftraege.clear()
        self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": "# Kern\n\nVor dem Neustart.\n", "vorschlag": True})
        self.assertEqual(self.herkunft()["modelle"][-1]["modell"], "unknown")
        self.assertIn("modell_unbekannt", [f.code for f in self.wiki().lint() if f.path.endswith("concepts/kern.md")])

    def test_eigenschaften_lassen_den_vermerk_stehen(self):
        text = "# Kern\n\nVorschlag.\n"
        d = self.vorschlag_holen(text)
        antwort = self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text, "vorschlag": True})[2]
        vorher = self.herkunft()["modelle"]
        fm = antwort["stand"]["frontmatter"]
        status, _, a2 = self.post("/api/seite", {"id": "concepts/kern", "hash": antwort["stand"]["hash"], "frontmatter": fm.replace("description: Der Kern.", "description: Neu.")})
        self.assertEqual(status, 200, a2)
        self.assertEqual(self.herkunft()["modelle"], vorher)
        status, _, a3 = self.post("/api/seite", {"id": "concepts/kern", "hash": a2["stand"]["hash"], "frontmatter": re.sub(r"(?m)^  modelle:\n(?:  .*\n)*", "", a2["stand"]["frontmatter"])})
        self.assertEqual(status, 422, a3)
        self.assertIn("generated", a3["fehler"])
        self.assertEqual(self.herkunft()["modelle"], vorher)

    def test_freigabe_im_dashboard_laesst_den_vermerk_stehen(self):
        text = "# Kern\n\nVorschlag.\n"
        d = self.vorschlag_holen(text)
        self.post("/api/seite", {"id": "concepts/kern", "hash": d["hash"], "body": text, "vorschlag": True})
        vorher = self.herkunft()
        self.post("/api/freigabe", {"ids": ["concepts/kern"], "force": True})
        self.assertEqual(self.herkunft(), vorher)

    def test_zaehlt_den_stand_im_textfeld(self):
        attrappe, gesehen = self.aufruf("Neu\n")
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            self.vorschlag(anweisung="x", body="Stand im Textfeld")
        self.assertEqual(gesehen["anhaenge"]["seite.md"], "Stand im Textfeld")

    def test_sitzung_und_sperre_klar_gemeldet(self):
        okfw_serve.rollen.setze_rolle("bearbeiten", "sitzung", "anthropic", "sonnet")
        status, antwort = self.vorschlag(anweisung="x")
        self.assertEqual(status, 422)
        self.assertIn("Sitzung", antwort["fehler"])
        okfw_serve.rollen.setze_rolle("bearbeiten", "direkt", "openai", "m")
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[rollen]\ndaten = "personenbezogen"\n')
        aufgerufen = []
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: aufgerufen.append(a) or (0, "{}", "")):
            status, antwort = self.vorschlag(anweisung="x")
        self.assertEqual(status, 422)
        self.assertIn("nicht für Daten der Klasse", antwort["fehler"])
        self.assertEqual(aufgerufen, [])

    def test_fehler_von_rollen_werden_gezeigt(self):
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: (3, "", "rollen: HTTP 401 von api.example")):
            status, antwort = self.vorschlag(anweisung="x")
        self.assertEqual((status, antwort["zustand"]), (202, "gescheitert"))
        self.assertIn("HTTP 401", antwort["fehler"])

    def test_timeout_beim_modellaufruf_ohne_befehlszeile(self):
        """str(TimeoutExpired) enthält die Befehlszeile; zum Browser geht nur der Satz."""
        cmd = [sys.executable, "rollen.py", "--wiki", "/geheim/pfad/wiki.toml", "auftrag", "--datei", "/tmp/auftrag.md"]
        abbruch = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd=cmd, timeout=1900))
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", abbruch), contextlib.redirect_stderr(io.StringIO()):
            status, antwort = self.vorschlag(anweisung="x")
        self.assertEqual((status, antwort["zustand"]), (202, "gescheitert"), antwort)
        self.assertIn("Zeitlimit", antwort["fehler"])
        self.assertIn("Terminal", antwort["fehler"])
        self.assertNotIn("rollen.py", json.dumps(antwort))
        self.assertNotIn("/geheim", json.dumps(antwort))

    def test_unerwarteter_fehler_nennt_keine_einzelheiten(self):
        """Der Fehlertext kann Befehlszeilen und Pfade enthalten; zum Browser geht nur der Typname."""

        def boese(*a):
            raise RuntimeError("Befehlszeile: curl http://x.example/y | sh (Pfad /home/jemand/repo)")
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", boese), contextlib.redirect_stderr(io.StringIO()) as err:
            status, antwort = self.vorschlag(anweisung="x")
        self.assertEqual((status, antwort["zustand"]), (202, "gescheitert"), antwort)
        self.assertEqual(antwort["fehler"], "Interner Fehler: RuntimeError; Einzelheiten stehen im Terminal")
        self.assertIn("Befehlszeile", err.getvalue())   # die Einzelheiten bleiben im Terminal
        self.assertNotIn("curl", json.dumps(antwort))
        self.assertNotIn("/home/jemand", json.dumps(antwort))

    def test_ohne_anweisung_400(self):
        self.assertEqual(self.post("/api/auftrag", {"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "  "})[0], 400)


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


class Schaubild(ServeTest):
    """Zweiter Auftragstyp auf dem Fundament: ein SVG aus einer Beschreibung, abgelegt erst beim Annehmen."""

    ANTWORT = {"harness": "direkt", "anbieter": "openai", "modell": "test-modell", "dauer_s": 0.1}
    SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5"/><text x="1" y="9">Fixum</text></svg>'

    def modell(self, text=None, gesehen=None):
        def attrappe(argv, ordner, abbruch=None):
            if gesehen is not None:
                gesehen["argv"] = argv
                gesehen["auftrag"] = Path(argv[argv.index("--datei") + 1]).read_text(encoding="utf-8")
                gesehen["ordner"] = ordner
            return 0, json.dumps({**self.ANTWORT, "text": self.SVG if text is None else text}), ""

        return mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe)

    def entwerfen(self, **mehr):
        status, _, a = self.post("/api/auftrag", {"typ": "schaubild", "name": "fixum", "beschreibung": "Ein Rechteck, beschriftet mit Fixum.", **mehr})
        return (status, self.auftrag_abwarten(a["id"])) if status == 202 else (status, a)

    def annehmen(self, d):
        return self.post("/api/vorschlag/annehmen", {"auftrag": d["id"], "aenderung": "a1"})

    def lint_codes(self, seite="concepts/kern"):
        return [f.code for f in self.wiki().lint() if f.path.endswith(f"{seite}.md")]

    def test_sendet_nur_die_beschreibung(self):
        gesehen = {}
        with self.modell(gesehen=gesehen):
            status, d = self.entwerfen()
        self.assertEqual((status, d["zustand"]), (202, "fertig"), d)
        self.assertNotIn("--anhang", gesehen["argv"])
        self.assertEqual(gesehen["argv"][gesehen["argv"].index("--rolle") + 1], "bearbeiten")
        self.assertIn("Ein Rechteck, beschriftet mit Fixum.", gesehen["auftrag"])
        self.assertIn("Erfinde keine Zahlen", gesehen["auftrag"])
        self.assertNotIn("Der Kern", gesehen["auftrag"])
        self.assertFalse(Path(gesehen["ordner"]).exists())
        self.assertEqual(d["gesendet"]["hinweis"], "Gesendet wurde nur deine Beschreibung")

    def test_nichts_liegt_ab_bis_zum_annehmen(self):
        with self.modell(f"Hier ist das Schaubild:\n\n```svg\n{self.SVG}\n```\nGern geschehen."):
            _, d = self.entwerfen(titel="Das Fixum", unterschrift="Das Fixum je Packung.")
        figur = self.root / "wiki" / "abbildungen" / "fixum.svg"
        self.assertFalse(figur.exists())
        a = d["aenderungen"][0]
        self.assertIn("<rect", a["daten"]["vorschau"])
        self.assertNotIn("svg", a["daten"])                 # roh geht nicht zum Browser
        self.assertIn("Illustration, kein Beleg", " ".join(d["warnungen"]))
        vorher = len(self.wiki().read_events())
        status, _, antwort = self.annehmen(d)
        self.assertEqual(status, 200, antwort)
        self.assertTrue(figur.is_file())
        ereignis = self.wiki().read_events()
        self.assertEqual(len(ereignis), vorher + 1)
        self.assertEqual((ereignis[-1]["page"], ereignis[-1]["by"]), ("abbildungen/fixum", "human:tester"))
        self.assertEqual(ereignis[-1]["modelle"][0]["modell"], "test-modell")
        self.assertEqual(antwort["einbindung"], "![Das Fixum](/abbildungen/fixum.svg)\n*Abb.: Das Fixum je Packung. Mit KI erzeugt (test-modell).*")

    def test_was_das_modell_einschleust_wird_bereinigt(self):
        boese = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9" onload="alert(1)"><script>alert(2)</script>'
                 '<a href="https://boese.example/"><rect width="5" height="5"/></a></svg>')
        with self.modell(boese):
            _, d = self.entwerfen()
        self.assertEqual(d["zustand"], "fertig", d)
        vorschau = d["aenderungen"][0]["daten"]["vorschau"]
        for marke in ("<script", "onload", "boese.example"):
            self.assertNotIn(marke, vorschau)
        self.assertEqual(self.annehmen(d)[0], 200)
        abgelegt = (self.root / "wiki" / "abbildungen" / "fixum.svg").read_text(encoding="utf-8")
        for marke in ("<script", "onload", "boese.example"):
            self.assertNotIn(marke, abgelegt)

    def test_ohne_oder_mit_abgelehntem_svg_kein_vorschlag(self):
        for text, grund in (("Ich kann das leider nicht zeichnen.", "kein SVG"),
                            ("<svg " * 200_000, "kein SVG"),        # lang und ohne Abschluss: darf unter der Sperre nicht hängen
                            ("</svg> davor <svg>", "kein SVG"),
                            ('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', "abgelehnt"),
                            (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><!-- {"x" * 210_000} --></svg>', "größer")):
            with self.subTest(grund=grund), self.modell(text):
                _, d = self.entwerfen()
                self.assertEqual((d["zustand"], d["aenderungen"]), ("gescheitert", []), d)
                self.assertIn(grund, d["fehler"])

    def test_vorhandenes_bild_wird_nie_ersetzt(self):
        aufgerufen = []
        self.post("/api/abbildung", {"name": "alt", "svg": GUTES_SVG})
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: aufgerufen.append(a) or (0, "{}", "")):
            status, antwort = self.entwerfen(name="alt")
        self.assertEqual(status, 409, antwort)
        self.assertEqual(aufgerufen, [])
        with self.modell():                                 # entsteht erst während des Laufs
            _, d = self.entwerfen(name="spaeter")
        self.post("/api/abbildung", {"name": "spaeter", "svg": GUTES_SVG})
        figur = self.root / "wiki" / "abbildungen" / "spaeter.svg"
        vorher = figur.read_bytes()
        self.assertEqual(self.annehmen(d)[0], 409)
        self.assertEqual(figur.read_bytes(), vorher)

    def test_falsche_eingaben_sind_nie_500(self):
        gut = {"typ": "schaubild", "name": "n", "beschreibung": "b"}
        with self.modell():
            for feld in ("name", "beschreibung", "titel", "unterschrift"):
                for wert in (1, True, [], {}, None, "\ud800"):
                    with self.subTest(feld=feld, wert=wert):
                        status, _, antwort = self.post("/api/auftrag", None, roh=json.dumps({**gut, "name": "x" + str(abs(hash((feld, str(wert))))), feld: wert}))
                        self.assertLess(status, 500, antwort)
                        if wert == "\ud800":
                            self.assertEqual(status, 422, antwort)
                        if status == 202:           # solange die Attrappe gilt: nichts verlässt den Rechner
                            self.auftrag_abwarten(antwort["id"])

    def test_fremde_datei_bekommt_eine_bereinigte_vorschau(self):
        with self.modell():
            _, d = self.entwerfen()
        ordner = self.root / "wiki" / okfw.AUFTRAEGE
        gut = json.loads((ordner / f"{d['id']}.json").read_text(encoding="utf-8"))
        a = gut["aenderungen"][0]
        a["daten"]["svg"] = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script><rect width="1" height="1"/></svg>'
        a["daten"]["vorschau"] = "<img src=x onerror=alert(1)>"
        (ordner / f"{d['id']}.json").write_text(json.dumps(gut), encoding="utf-8")
        self.server.sitzung.auftraege = okfw_serve.okfw_auftraege.Auftraege(self.server.sitzung)
        vorschau = self.anfrage("GET", f"/api/auftrag?id={d['id']}")[2]["aenderungen"][0]["daten"]["vorschau"]
        self.assertIn("<rect", vorschau)
        self.assertNotIn("<script", vorschau)
        self.assertNotIn("onerror", vorschau)

    def test_lint_verlangt_die_kennzeichnung_und_die_datei_zeigt_sie(self):
        with self.modell():
            _, d = self.entwerfen()
        einbindung = self.annehmen(d)[2]["einbindung"]
        self.datei("concepts/kern").write_text(self.datei("concepts/kern").read_text(encoding="utf-8").rstrip() + f"\n\n{einbindung}\n", encoding="utf-8")
        self.assertNotIn("figure_generated", self.lint_codes())
        self.assertIn("Mit KI erzeugt (test-modell)", self.wiki().render_dashboard())
        pfad = self.datei("concepts/kern")
        pfad.write_text(pfad.read_text(encoding="utf-8").replace("Mit KI erzeugt (test-modell)", "Von Hand gezeichnet"), encoding="utf-8")
        self.assertIn("figure_generated", self.lint_codes())
        status, _, antwort = self.post("/api/abbildung", {"name": "fixum", "svg": GUTES_SVG, "ersetzen": True})
        self.assertEqual(status, 200, antwort)              # von Hand ersetzt: stammt nicht mehr vom Modell
        self.assertNotIn("figure_generated", self.lint_codes())

    def test_anfrage_kann_keine_herkunft_setzen(self):
        self.post("/api/abbildung", {"name": "frei", "svg": GUTES_SVG, "rolle": "bearbeiten", "modell": "erfunden"})
        self.assertNotIn("modelle", self.wiki().read_events()[-1])

    def test_befehl_modell_nur_mit_rolle(self):
        svg = self.base / "a.svg"
        svg.write_text(GUTES_SVG, encoding="utf-8")
        code = still(okfw.main, ["--root", str(self.root), "abbildung", "ohne-rolle", "--file", str(svg), "--modell", "m"])
        self.assertNotEqual(code, 0)
        self.assertFalse((self.root / "wiki" / "abbildungen" / "ohne-rolle.svg").exists())
        self.kern("abbildung", "mit-rolle", "--file", str(svg), "--rolle", "bearbeiten", "--modell", "m")
        self.assertEqual(self.wiki().read_events()[-1]["modelle"][0]["modell"], "m")


class Auftraege(ServeTest):
    """Fundament: Auftrag im Hintergrund, Vorschlag als Datei, angenommen wird nur über den Kern."""

    ANTWORT = {"harness": "direkt", "anbieter": "openai", "modell": "test-modell", "dauer_s": 0.1}

    def modell(self, text="# Kern\n\nNeu.\n"):
        return mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: (0, json.dumps({**self.ANTWORT, "text": text}), ""))

    def ordner(self):
        return self.root / "wiki" / okfw.AUFTRAEGE

    def fertiger_auftrag(self, text="# Kern\n\nNeu.\n"):
        with self.modell(text):
            status, d = self.vorschlag(anweisung="x")
        self.assertEqual((status, d["zustand"]), (202, "fertig"), d)
        return d

    def annehmen(self, d, **mehr):
        return self.post("/api/vorschlag/annehmen", {"auftrag": d["id"], "aenderung": d["aenderungen"][0]["id"], **mehr})

    def neuer_server(self):
        """Wie ein Neustart: dieselbe Ablage, frischer Zustand."""
        self.server.sitzung.auftraege = okfw_serve.okfw_auftraege.Auftraege(self.server.sitzung)

    def test_laeuft_im_hintergrund_und_die_seite_bleibt_bedienbar(self):
        los, gesehen = threading.Event(), []

        def attrappe(argv, ordner, abbruch=None):
            gesehen.append(1)
            self.assertTrue(los.wait(20))
            return 0, json.dumps({**self.ANTWORT, "text": "Neu"}), ""

        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            status, _, a = self.post("/api/auftrag", {"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "x"})
            self.assertEqual(status, 202, a)
            self.assertEqual(self.auftrag_abwarten(a["id"], ("laeuft",))["aenderungen"], [])
            self.assertEqual(self.anfrage("GET", "/api/seite?id=concepts/kern")[0], 200)   # keine Sperre während des Modells
            liste = self.anfrage("GET", "/api/auftraege")[2]["auftraege"]
            self.assertEqual([x["id"] for x in liste], [a["id"]])
            self.assertNotIn("aenderungen", liste[0])
            los.set()
            self.assertEqual(self.auftrag_abwarten(a["id"])["zustand"], "fertig")
        self.assertEqual(gesehen, [1])

    def test_ablage_privat_und_nicht_im_commit(self):
        d = self.fertiger_auftrag()
        self.assertEqual(oct(self.ordner().stat().st_mode & 0o777), "0o700")
        self.assertEqual((self.ordner() / ".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertEqual(oct((self.ordner() / f"{d['id']}.json").stat().st_mode & 0o777), "0o600")
        self.assertNotIn(GEHEIM, (self.ordner() / f"{d['id']}.json").read_text(encoding="utf-8"))

    def test_kein_export_und_kein_lint_sieht_den_vorschlag(self):
        self.fertiger_auftrag("# Kern\n\nMARKE-IM-VORSCHLAG\n")
        wiki = self.wiki()
        self.assertFalse([p.id for p in wiki.pages() if "auftraege" in p.id])
        self.assertFalse([f for f in wiki.lint() if okfw.AUFTRAEGE in f.path])
        self.assertNotIn("MARKE-IM-VORSCHLAG", wiki.render_dashboard())
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "access"\nhidden = ["intern"]\n')
        self.assertNotIn("MARKE-IM-VORSCHLAG", self.wiki().public_view().render_dashboard())

    def test_annehmen_geht_ueber_den_kern(self):
        text = "# Kern\n\nAngenommen.\n"
        d = self.fertiger_auftrag(text)
        vorher = len(self.wiki().read_events())
        status, _, antwort = self.annehmen(d)
        self.assertEqual(status, 200, antwort)
        self.assertEqual(self.seite("concepts/kern").body.strip(), text.strip())
        self.assertEqual(len(self.wiki().read_events()), vorher + 1)
        eintrag = self.seite("concepts/kern").fm["generated"]["modelle"][-1]
        self.assertEqual((eintrag["modell"], eintrag["art"]), ("test-modell", "vorschlag"))
        self.assertEqual(antwort["stand"]["body"], text.strip())
        self.assertEqual(self.annehmen(d)[0], 409)   # entschieden ist entschieden
        self.assertEqual(self.anfrage("GET", "/api/auftraege")[2]["auftraege"], [])

    def test_annehmen_auf_veraendertem_stand_ueberschreibt_nichts(self):
        d = self.fertiger_auftrag()
        stand = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        self.assertEqual(self.post("/api/seite", {"id": "concepts/kern", "hash": stand["hash"], "body": "# Kern\n\nDazwischen.\n"})[0], 200)
        status, _, antwort = self.annehmen(d)
        self.assertEqual(status, 409, antwort)
        self.assertIn("Dazwischen", self.seite("concepts/kern").body)
        self.assertEqual(self.anfrage("GET", f"/api/auftrag?id={d['id']}")[2]["aenderungen"][0]["status"], "offen")

    def test_ins_textfeld_schreibt_nichts_und_speichern_nennt_das_modell(self):
        text = "# Kern\n\nFürs Textfeld.\n"
        d = self.fertiger_auftrag(text)
        vorher = self.datei("concepts/kern").read_bytes()
        status, _, antwort = self.annehmen(d, ziel="textfeld")
        self.assertEqual((status, antwort["neu"]), (200, text.strip()))
        self.assertEqual(self.datei("concepts/kern").read_bytes(), vorher)
        stand = self.anfrage("GET", "/api/seite?id=concepts/kern")[2]
        self.post("/api/seite", {"id": "concepts/kern", "hash": stand["hash"], "body": "# Kern\n\nÜberarbeitet.\n", "vorschlag": True})
        eintrag = self.seite("concepts/kern").fm["generated"]["modelle"][-1]
        self.assertEqual((eintrag["modell"], eintrag["art"]), ("test-modell", "vorschlag-bearbeitet"))

    def test_verwerfen_laesst_die_seite_und_leert_die_liste(self):
        d = self.fertiger_auftrag()
        vorher = self.datei("concepts/kern").read_bytes()
        self.assertEqual(self.post("/api/vorschlag/verwerfen", {"auftrag": d["id"], "aenderung": "a1"})[0], 200)
        self.assertEqual(self.datei("concepts/kern").read_bytes(), vorher)
        self.assertEqual(self.anfrage("GET", "/api/auftraege")[2]["auftraege"], [])
        self.assertEqual(self.annehmen(d)[0], 409)

    def test_gescheiterter_auftrag_laesst_sich_ausblenden(self):
        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", lambda *a: (3, "", "rollen: HTTP 500")):
            _, d = self.vorschlag(anweisung="x")
        self.assertEqual(d["zustand"], "gescheitert")
        self.assertEqual(len(self.anfrage("GET", "/api/auftraege")[2]["auftraege"]), 1)
        self.assertEqual(self.post("/api/vorschlag/verwerfen", {"auftrag": d["id"]})[0], 200)
        self.assertEqual(self.anfrage("GET", "/api/auftraege")[2]["auftraege"], [])

    def test_abbrechen_auch_wartend(self):
        def attrappe(argv, ordner, abbruch=None):
            self.assertTrue(abbruch.wait(20))
            raise okfw_serve.okfw_auftraege.Abgebrochen()

        with mock.patch.object(okfw_serve.okfw_auftraege, "rollen_aufruf", attrappe):
            kopf = {"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "x"}
            erster = self.post("/api/auftrag", kopf)[2]["id"]
            self.auftrag_abwarten(erster, ("laeuft",))
            zweiter = self.post("/api/auftrag", kopf)[2]["id"]
            time.sleep(0.3)
            self.assertEqual(self.anfrage("GET", f"/api/auftrag?id={zweiter}")[2]["zustand"], "wartet")   # je Rolle einer zugleich
            self.assertEqual(self.post("/api/auftrag/abbrechen", {"auftrag": zweiter})[0], 200)
            self.assertEqual(self.auftrag_abwarten(zweiter)["zustand"], "abgebrochen")
            self.assertEqual(self.anfrage("GET", f"/api/auftrag?id={erster}")[2]["zustand"], "laeuft")
            self.assertEqual(self.post("/api/auftrag/abbrechen", {"auftrag": erster})[0], 200)
            d = self.auftrag_abwarten(erster)
        self.assertEqual((d["zustand"], d["aenderungen"]), ("abgebrochen", []))
        self.assertEqual(self.post("/api/auftrag/abbrechen", {"auftrag": erster})[0], 409)

    def test_abbruch_beendet_den_prozess_samt_kind(self):
        ordner = self.base / "lauf"
        ordner.mkdir()
        kind = self.base / "kind.pid"
        code = ("import os, subprocess, sys, time\n"
                f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\nopen({str(kind)!r}, 'w').write(str(p.pid))\ntime.sleep(120)\n")
        abbruch = threading.Event()
        threading.Thread(target=lambda: (self.warte_auf(kind), abbruch.set()), daemon=True).start()
        start = time.monotonic()
        with self.assertRaises(okfw_serve.okfw_auftraege.Abgebrochen):
            okfw_serve.okfw_auftraege.rollen_aufruf([sys.executable, "-c", code], str(ordner), abbruch)
        self.assertLess(time.monotonic() - start, 30)
        pid = int(kind.read_text(encoding="utf-8"))
        frist = time.monotonic() + 5
        while time.monotonic() < frist and self.lebt(pid):
            time.sleep(0.05)
        self.assertFalse(self.lebt(pid))

    @staticmethod
    def warte_auf(datei):
        while not (datei.exists() and datei.read_text(encoding="utf-8")):
            time.sleep(0.02)

    @staticmethod
    def lebt(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        lauf = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        return bool(lauf.stdout.strip()) and not lauf.stdout.strip().startswith("Z")

    def test_neustart_bricht_laufendes_ab_und_behaelt_vorschlaege(self):
        fertig = self.fertiger_auftrag("# Kern\n\nVor dem Neustart.\n")
        datei = self.ordner() / f"{fertig['id']}.json"
        lief = {**json.loads(datei.read_text(encoding="utf-8")), "id": "20260101T000000-00000000", "zustand": "laeuft", "aenderungen": []}
        (self.ordner() / f"{lief['id']}.json").write_text(json.dumps(lief), encoding="utf-8")
        self.neuer_server()
        d = self.anfrage("GET", f"/api/auftrag?id={lief['id']}")[2]
        self.assertEqual(d["zustand"], "abgebrochen")
        self.assertIn("neu gestartet", d["fehler"])
        self.assertEqual(json.loads((self.ordner() / f"{lief['id']}.json").read_text(encoding="utf-8"))["zustand"], "abgebrochen")
        self.assertEqual(self.annehmen(fertig)[0], 200)
        self.assertEqual(self.seite("concepts/kern").fm["generated"]["modelle"][-1]["modell"], "test-modell")   # die Herkunft überlebt den Neustart

    def test_erledigtes_faellt_nach_der_frist_weg(self):
        d = self.fertiger_auftrag()
        self.annehmen(d)
        datei = self.ordner() / f"{d['id']}.json"
        alt = json.loads(datei.read_text(encoding="utf-8"))
        datei.write_text(json.dumps({**alt, "zeit": time.time() - okfw_serve.okfw_auftraege.AUFHEBEN_S - 1}), encoding="utf-8")
        self.neuer_server()
        self.assertEqual(self.anfrage("GET", "/api/auftraege")[0], 200)
        self.assertFalse(datei.exists())

    def test_fremde_dateien_im_ordner_sind_nur_daten(self):
        d = self.fertiger_auftrag()
        gut = json.loads((self.ordner() / f"{d['id']}.json").read_text(encoding="utf-8"))
        name = "20260101T000000-0000000"
        schlecht = {"a": "kein json", "b": "[]", "c": json.dumps({"id": "x"})}
        for i, inhalt in enumerate(schlecht.values()):
            (self.ordner() / f"{name}{i}.json").write_text(inhalt, encoding="utf-8")
        for i, aenderung in enumerate(({"art": "shell"}, {"status": "angenommen", "daten": []}, {"daten": {"alt": 1, "neu": 2}})):
            kennung = f"{name}{i + 3}"
            a = {**gut["aenderungen"][0], **aenderung}
            (self.ordner() / f"{kennung}.json").write_text(json.dumps({**gut, "id": kennung, "aenderungen": [a]}), encoding="utf-8")
        (self.ordner() / f"{name}6.json").write_text(json.dumps({**gut, "id": "20260101T000000-ffffffff"}), encoding="utf-8")   # Name passt nicht
        (self.ordner() / f"{name}7.json").symlink_to(self.ordner() / f"{d['id']}.json")
        self.neuer_server()
        with contextlib.redirect_stderr(io.StringIO()):
            status, _, antwort = self.anfrage("GET", "/api/auftraege")
        self.assertEqual(status, 200)
        self.assertEqual([x["id"] for x in antwort["auftraege"]], [d["id"]])
        self.assertEqual(self.anfrage("GET", "/api/auftrag?id=../../wiki")[0], 404)

    def test_ordner_als_symlink_wird_abgelehnt(self):
        ziel = self.base / "draussen"
        ziel.mkdir()
        self.ordner().symlink_to(ziel)
        self.neuer_server()
        with self.modell():
            status, _, antwort = self.post("/api/auftrag", {"typ": "bearbeiten", "id": "concepts/kern", "anweisung": "x"})
        self.assertEqual(status, 422, antwort)
        self.assertEqual(list(ziel.iterdir()), [])

    def test_unbekannte_art_faellt_weg_und_wird_gemeldet(self):
        typ = okfw_serve.okfw_auftraege.TYPEN["bearbeiten"]
        gut = {"art": "koerper", "ziel": "concepts/kern", "basis": "x", "daten": {"alt": "a", "neu": "b"}}
        with self.modell(), mock.patch.object(typ, "auswerten", lambda *a: {"warnungen": [], "aenderungen": [{"art": "shell", "ziel": "x"}, gut]}):
            _, d = self.vorschlag(anweisung="x")
        self.assertEqual([a["art"] for a in d["aenderungen"]], ["koerper"])
        self.assertIn("shell", " ".join(d["warnungen"]))

    def test_unbekannter_typ(self):
        status, _, antwort = self.post("/api/auftrag", {"typ": "shell", "id": "concepts/kern"})
        self.assertEqual(status, 422, antwort)

    def test_was_gebraucht_wird_kommt_mit(self):
        d = self.fertiger_auftrag("# Kern\n\nErster.\n")
        s = self.server.sitzung.auftraege
        with self.server.sitzung.lock:
            intern = s.auftraege[d["id"]]
            intern["aenderungen"][0]["braucht"] = ["a2"]
            intern["aenderungen"].append({**intern["aenderungen"][0], "id": "a2", "braucht": [], "ziel": "sources/q1", "status": "verworfen"})
        status, _, antwort = self.annehmen(d)
        self.assertEqual(status, 409, antwort)   # was gebraucht wird, ist verworfen
        self.assertEqual(intern["aenderungen"][0]["status"], "offen")
        intern["aenderungen"][1]["braucht"] = ["a1"]
        intern["aenderungen"][1]["status"] = "offen"
        self.assertEqual(self.annehmen(d)[0], 422)   # im Kreis

    def test_schreibschicht_nur_im_server(self):
        served = self.anfrage("GET", "/", kopf={"Accept": "text/html"})
        html = served[2] if isinstance(served[2], str) else ""
        for marke in ("window.okfwEdit =", "window.okfwServe =", "window.okfwAuftraege =", 'art("koerper"', 'art("abbildung"', 'art("quelle"'):
            self.assertIn(marke, html)
            self.assertNotIn(marke, self.wiki().render_dashboard())
        for name in okfw_serve.SERVE_JS:
            self.assertNotIn("</script", (SKILL / "templates" / name).read_text(encoding="utf-8").lower())

    def test_auftraege_css_hat_keine_eigenen_farben(self):
        css = (SKILL / "templates" / "auftraege.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch)\(", css))

    def test_abgewiesene_anfragen_erben_die_schranken(self):
        for pfad in ("/api/auftrag", "/api/auftrag/abbrechen", "/api/vorschlag/annehmen", "/api/vorschlag/verwerfen"):
            self.assertEqual(self.anfrage("POST", pfad, {}, origin="http://boese.example")[0], 403, pfad)
            self.assertEqual(self.anfrage("POST", pfad, {}, cookie=False)[0], 403, pfad)


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


class Pfad(ServeTest):
    """Stationen und Lage über /api/pfad, immer mit Hash und Ereignis."""

    def setUp(self):
        super().setUp()
        for slug in "ab":
            self.kern("new", "concept", slug, "--title", f"Karte {slug}", "--description", "Testkarte")
        self.kern("pfad", "neu", "Mein Vortrag")
        self.pid = "pfade/mein-vortrag"
        self.datei(self.pid).write_text(self.datei(self.pid).read_text(encoding="utf-8").split("---\n\n", 1)[0]
                                        + "---\n\n# Eins\n\nText.\n\n- [Karte a](/concepts/a.md)\n\n> [!notiz]\n> Merken\n",
                                        encoding="utf-8")

    def stand(self):
        status, _, d = self.anfrage("GET", f"/api/pfad?id={self.pid}")
        self.assertEqual(status, 200, d)
        return d

    def speichern(self, stand, **mehr):
        return self.post("/api/pfad", {"id": stand["id"], "hash": stand["hash"], "stationen": stand["stationen"], **mehr})

    def test_lesen(self):
        d = self.stand()
        self.assertEqual(d["stationen"][0]["titel"], "Eins")
        self.assertEqual(d["stationen"][0]["notizen"], ["Merken"])
        karte = [e for e in d["stationen"][0]["elemente"] if e["art"] == "karte"][0]
        self.assertEqual((karte["id"], karte["titel"]), ("concepts/a", "Karte a"))
        ids = [k["id"] for k in d["karten"]]
        self.assertIn("concepts/b", ids)
        self.assertNotIn(self.pid, ids, "der Pfad selbst ist keine Karte")
        for pfad, code in (("gibtsnicht", 404), ("zuerst-lesen", 404)):
            self.assertEqual(self.anfrage("GET", f"/api/pfad?id={pfad}")[0], code)

    def test_verschieben_aendert_nur_die_lage(self):
        d = self.stand()
        roh = self.datei(self.pid).read_text(encoding="utf-8").split("---\n", 2)[2]
        for e in d["stationen"][0]["elemente"]:
            e["auto"] = False
        d["stationen"][0]["elemente"][1]["pos"] = [3, 1, 4, 3]
        status, _, antwort = self.speichern(d)
        self.assertEqual(status, 200, antwort)
        self.assertTrue(antwort["geaendert"])
        text = self.datei(self.pid).read_text(encoding="utf-8")
        self.assertEqual(text.split("---\n", 2)[2], roh, "Körper unverändert")
        self.assertIn("lage:", text)
        self.assertEqual(self.wiki().read_events()[-1]["detail"], "Pfad: Anordnung")
        self.assertEqual(self.wiki().read_events()[-1]["by"], f"human:{self.kennung}")
        self.assertEqual(self.speichern(antwort)[2]["geaendert"], False)

    def test_konflikt_schreibt_nichts_und_nennt_den_stand(self):
        d = self.stand()
        pfad = self.datei(self.pid)
        pfad.write_text(pfad.read_text(encoding="utf-8") + "\nVon nebenan.\n", encoding="utf-8")
        vorher = pfad.read_text(encoding="utf-8")
        d["stationen"].append({"titel": "Zwei", "elemente": [], "notizen": []})
        status, _, antwort = self.speichern(d)
        self.assertEqual(status, 409)
        self.assertIn("Von nebenan", json.dumps(antwort["aktuell"], ensure_ascii=False))
        self.assertEqual(pfad.read_text(encoding="utf-8"), vorher)

    def test_schlechte_eingaben(self):
        d = self.stand()
        for daten in ({}, {"id": self.pid, "hash": d["hash"]}, {"id": 1, "hash": "x", "stationen": []}):
            self.assertEqual(self.post("/api/pfad", daten)[0], 400, daten)
        boese = [{"titel": "x", "elemente": [{"art": "karte", "id": "concepts/gibtsnicht"}]}]
        status, _, antwort = self.speichern({**d, "stationen": boese})
        self.assertEqual(status, 422)
        self.assertIn("nicht gefunden", antwort["fehler"])
        self.assertEqual(self.post("/api/pfad/neu", {"titel": "  "})[0], 422)

    def test_neu_und_uebernehmen(self):
        status, _, d = self.post("/api/pfad/neu", {"titel": "Zweiter Pfad"})
        self.assertEqual((status, d["id"]), (200, "pfade/zweiter-pfad"))
        self.assertTrue(self.datei(d["id"]).exists())
        self.assertEqual(self.post("/api/pfad/uebernehmen", {})[0], 422, "ohne Schlagwort nichts zu übernehmen")
        self.kern("new", "concept", "c", "--title", "Karte c", "--description", "Testkarte", "--tags", "einstieg", "--set", "order=1")
        status, _, d = self.post("/api/pfad/uebernehmen", {})
        self.assertEqual(status, 200, d)
        self.assertEqual(d["pfad"], "pfade/zuerst-lesen", "die Oberfläche öffnet danach diesen Pfad")
        self.assertTrue(self.datei("pfade/zuerst-lesen").exists())

    def test_routen_verlangen_die_kennung(self):
        for pfad in ("/api/pfad", "/api/pfad/neu", "/api/pfad/uebernehmen"):
            self.assertEqual(self.anfrage("POST", pfad, {}, cookie=False)[0], 403, pfad)
            self.assertEqual(self.anfrage("POST", pfad, {}, origin="http://example.org")[0], 403, pfad)
        self.assertEqual(self.anfrage("GET", f"/api/pfad?id={self.pid}", cookie=False)[0], 403)

    def test_seite_ohne_notiz_und_ohne_einstieg(self):
        _, _, html = self.anfrage("GET", "/")
        self.assertNotIn("Merken", html, "Notizen holt erst der Vortrag")
        for wort in ("Lernpfad", "einstiegPfad", "Im Einstieg", "Als Einstieg", "api/einstieg", "api/pfad/aufnehmen"):
            self.assertNotIn(wort, html)


@unittest.skipUnless(shutil.which("node"), "node fehlt")
class Syntax(unittest.TestCase):
    def test_skripte_lassen_sich_parsen(self):
        for name in ("serve.js", "pfad.js"):
            lauf = subprocess.run(["node", "--check", str(SKILL / "templates" / name)], capture_output=True, text=True, timeout=60)
            self.assertEqual(lauf.returncode, 0, lauf.stderr)


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


class Darstellung(unittest.TestCase):
    """CSS-Regeln, die Darstellungsfehler verhindern (das Layout selbst prüft nur der Browser)."""

    def test_design_spalte_hell_faerbt_nur_das_farbfeld(self):
        css = (SKILL / "templates" / "serve.css").read_text(encoding="utf-8")
        self.assertIn(".okfw-vars .sw span:not(.okfw-note) {", css)   # der Textspan der Schriftnamen bleibt Text
        self.assertRegex(css, r"\.okfw-vars \.sw \.okfw-note \{[^}]*overflow-wrap: anywhere")

    def test_punkt_in_listen_schrumpft_nicht_und_steht_oben(self):
        html = (SKILL / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertRegex(html, r"\.list button svg \{[^}]*flex: none;[^}]*align-self: flex-start")


if __name__ == "__main__":
    unittest.main()
