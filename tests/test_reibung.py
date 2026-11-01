"""Gruppe 3: Reibung – Marker in Code, Kurz-SHA, Repo-Pfad je Datei, Stop-Hook mit Lint, Hook-Fehler als eine Zeile."""

import json

from .hilfe import HOOK, WikiTest

SHA = "37a3d5e2ad727a043034103e01b23b75052f186c"
KONZEPT_MIT_CODE = """# Konzept

Offene Frage, als Kasten:

> [!todo] Noch zu klären.

Im Code zählt das nicht: `> [!todo]` und `> ⚠️ **Widerspruch:** X` sind nur Beispiele.

```markdown
> [!todo] Beispiel im Codeblock
> ⚠️ **Widerspruch:** Beispiel im Codeblock, siehe [Quelle](/sources/q1.md)
```
"""


class MarkerInCode(WikiTest):
    def setUp(self):
        super().setUp()
        self.quelle("q1")
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test", "--sources", "q1")
        self.core("body", "concepts/k1", "-", stdin=KONZEPT_MIT_CODE)

    def test_status_zaehlt_nur_echte_todos(self):
        todo = self.py("result = wiki.status()['todo']")
        quelle = self.text("sources/q1").count("[!todo]")
        self.assertEqual(todo, quelle + 1)
        self.assertIn(f"offene [!todo]: {quelle + 1}", self.core("status").stdout)

    def test_dashboard_offene_stellen_ohne_code(self):
        spots = self.py("result = wiki.reader_html()['concepts/k1']['open']")
        self.assertEqual([s["kind"] for s in spots], ["todo"])

    def test_lint_widerspruch_im_code_ist_keiner(self):
        self.assertNotIn("contradiction_one_sided", self.lint_codes("concepts/k1"))

    def test_echter_widerspruch_zaehlt_weiter(self):
        self.core("body", "concepts/k1", "-",
                  stdin="> ⚠️ **Widerspruch:** A sagt X, [Quelle q1](/sources/q1.md) sagt Y.\n")
        self.assertIn("contradiction_one_sided", self.lint_codes("concepts/k1"))
        spots = self.py("result = wiki.reader_html()['concepts/k1']['open']")
        self.assertEqual([s["kind"] for s in spots], ["contra"])


class CaptureKomfort(WikiTest):
    def test_kurz_sha_warnt(self):
        datei = self.datei("a.md")
        done = self.core("capture", "q1", "--url", "https://example.org", "--file", str(datei), "--license", "MIT",
                         "--commit", SHA[:7])
        self.assertIn("kein voller Commit-SHA", done.stderr)
        manifest = json.loads((self.root / "raw" / "q1" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["commit"], SHA[:7])

    def test_voller_sha_ohne_warnung(self):
        datei = self.datei("a.md")
        done = self.core("capture", "q1", "--url", "https://example.org", "--file", str(datei), "--license", "MIT",
                         "--commit", SHA)
        self.assertNotIn("Achtung", done.stderr)

    def test_repo_pfad_je_datei(self):
        a, b, c = self.datei("hooks.json"), self.datei("okf_hook.py"), self.datei("README.md")
        self.core("capture", "q1", "--url", "https://example.org", "--license", "MIT", "--source-path", "README.md",
                  "--file", f"{a}=hooks/hooks.json", "--file", f"{b}=skills/okfw/hooks/okf_hook.py", "--file", str(c))
        manifest = json.loads((self.root / "raw" / "q1" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([(f["path"], f["source_path"]) for f in manifest["files"]],
                         [("original/hooks.json", "hooks/hooks.json"),
                          ("original/okf_hook.py", "skills/okfw/hooks/okf_hook.py"),
                          ("original/README.md", "README.md")])

    def test_datei_mit_gleichheitszeichen_im_namen(self):
        datei = self.datei("a=b.md")
        self.core("capture", "q1", "--url", "https://example.org", "--license", "MIT", "--file", str(datei))
        self.assertTrue((self.root / "raw" / "q1" / "original" / "a=b.md").exists())


class StopHook(WikiTest):
    def test_lint_fehler_als_eine_zeile(self):
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test")
        self.core("body", "concepts/k1", "-", stdin="Aussage ohne Quelle.[^fehlt]\n\n[^fehlt]: irgendwo\n")
        done = self.hook("stop", {"hook_event_name": "Stop"})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertNotIn("decision", out)
        self.assertIn("Fehler", out["systemMessage"])
        self.assertIn("footnote_unsourced", out["systemMessage"])
        self.assertEqual(len(done.stdout.strip().splitlines()), 1)
        self.core("rebuild", "--check")   # Rebuild ist trotzdem gelaufen

    def test_sauberes_wiki_still(self):
        done = self.hook("stop", {"hook_event_name": "Stop"})
        self.assertEqual((done.returncode, done.stdout), (0, ""))


class StopHookRunden(WikiTest):
    def setUp(self):
        super().setUp()
        self.kaputt()

    def kaputt(self):
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test")
        self.core("body", "concepts/k1", "-", stdin="Aussage ohne Quelle.[^fehlt]\n\n[^fehlt]: irgendwo\n")

    def stop(self, **event) -> dict:
        done = self.hook("stop", {"hook_event_name": "Stop", "session_id": "s1", "stop_hook_active": False, **event})
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_block_mit_befund_je_seite(self):
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertNotIn("systemMessage", out)
        zeilen = out["reason"].splitlines()
        self.assertIn("Runde 1 von 2", zeilen[0])
        seite = [z for z in zeilen if z.startswith("wiki/concepts/k1.md: ")]
        self.assertEqual(len(seite), 1)
        self.assertIn("footnote_unsourced", seite[0])
        self.assertIn("nur die Person", zeilen[-1])
        self.core("rebuild", "--check")   # Rebuild ist trotzdem gelaufen

    def test_dritter_aufruf_nur_noch_meldung(self):
        self.assertEqual(self.stop()["decision"], "block")
        self.assertIn("Runde 2 von 2", self.stop()["reason"])
        for _ in range(2):
            out = self.stop()
            self.assertNotIn("decision", out)
            self.assertIn("footnote_unsourced", out["systemMessage"])
        self.assertEqual(self.stop(session_id="s2")["decision"], "block")   # eigener Zähler je Sitzung

    def test_stop_hook_active_blockiert_nie(self):
        out = self.stop(stop_hook_active=True)
        self.assertNotIn("decision", out)
        self.assertIn("Fehler", out["systemMessage"])
        self.assertIn("Runde 1 von 2", self.stop()["reason"])               # verbraucht keine Runde

    def test_endet_wie_claude_p(self):
        """Claude Code setzt nach einem Block mit stop_hook_active fort: die Kette endet nach einer Rückgabe."""
        self.assertEqual(self.stop()["decision"], "block")
        self.assertNotIn("decision", self.stop(stop_hook_active=True))

    def test_fehlerfreier_lint_setzt_zurueck(self):
        self.stop()
        self.stop()
        (self.root / "wiki" / "concepts" / "k1.md").unlink()
        self.assertEqual(self.hook("stop", {"hook_event_name": "Stop", "session_id": "s1"}).stdout, "")
        self.kaputt()
        self.assertIn("Runde 1 von 2", self.stop()["reason"])

    def test_zaehler_liegt_nicht_im_wiki(self):
        self.stop(session_id="../../s/1")
        self.assertEqual(list(self.root.rglob("okfw-stop-*")), [])
        self.assertEqual([p.name for p in self.base.glob("okfw-stop-*")], ["okfw-stop-______s_1"])

    def test_ohne_zaehler_keine_blockade(self):
        (self.base / "okfw-stop-s1").mkdir()                                # weder lesbar noch schreibbar
        self.assertNotIn("decision", self.stop())
        self.assertNotIn("decision", self.stop(session_id=""))
        self.assertNotIn("decision", self.stop(session_id=7))

    def test_kaputter_zaehler_keine_blockade(self):
        (self.base / "okfw-stop-s1").write_text("kaputt", encoding="utf-8")
        self.assertNotIn("decision", self.stop())

    def test_bedienfehler_blockiert_nie(self):
        toml = self.root / "wiki.toml"
        gut = toml.read_text(encoding="utf-8")
        toml.write_text("[wiki\n", encoding="utf-8")
        out = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("nicht lesbar", out["systemMessage"])
        toml.write_text(gut.replace('requires_core = "0.1"', 'requires_core = "9.0"'), encoding="utf-8")
        self.assertNotIn("decision", self.stop())
        toml.write_text(gut, encoding="utf-8")
        self.assertIn("Runde 1 von 2", self.stop()["reason"])               # Bedienfehler verbrauchen keine Runde

    def mit_kern(self, run_core: str) -> dict:
        """okf_hook.main mit ersetztem run_core; Ergebnis ist die Ausgabe als JSON."""
        return self.py("import contextlib, io, subprocess\n"
                       f"sys.path.insert(0, {str(HOOK.parent)!r})\nimport okf_hook\n"
                       f"def kern(*args, **opts):\n    {run_core}\n"
                       "okf_hook.run_core = kern\n"
                       "sys.argv = ['okf_hook.py', 'stop']\n"
                       f"sys.stdin = io.StringIO(json.dumps({{'session_id': 's1', 'cwd': {str(self.root)!r}}}))\n"
                       "out = io.StringIO()\n"
                       "with contextlib.redirect_stdout(out):\n    okf_hook.main()\n"
                       "result = json.loads(out.getvalue())")

    def test_uv_zeitueberschreitung_und_absturz_blockieren_nie(self):
        fall = {"return None": "uv fehlt",
                "raise subprocess.TimeoutExpired('uv', 55)": "nach 55 s abgebrochen",
                "return subprocess.CompletedProcess([], 1, '', 'Absturz')": "Exit 1",
                "return subprocess.CompletedProcess([], 0, 'kein JSON', '')": "JSONDecodeError"}
        for run_core, text in fall.items():
            out = self.mit_kern(run_core)
            self.assertNotIn("decision", out)
            self.assertIn(text, out["systemMessage"])

    def test_rueckgabe_bleibt_kurz(self):
        reason = self.py(f"sys.path.insert(0, {str(HOOK.parent)!r})\nimport okf_hook\n"
                         "result = okf_hook.block_reason([f'wiki/{i}.md: x: y' for i in range(25)], 1)")
        self.assertIn("wiki/19.md", reason)
        self.assertNotIn("wiki/20.md", reason)
        self.assertIn("und 5 weitere Seiten", reason)

    def test_kern_allein_blockiert_nie(self):
        out = json.loads(self.core("rebuild", "--hook").stdout)
        self.assertEqual(list(out), ["systemMessage"])
        out = json.loads(self.core("rebuild", "--hook", "--befunde").stdout)
        self.assertNotIn("decision", out)
        self.assertIn("footnote_unsourced", out["befunde"][0])


class HookFehlerSichtbar(WikiTest):
    def test_requires_core_beim_sitzungsstart(self):
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('requires_core = "0.1"', 'requires_core = "9.0"'),
                        encoding="utf-8")
        done = self.hook("session-start", {"hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(done.returncode, 0)
        out = json.loads(done.stdout)
        self.assertIn("verlangt okfw 9.0", out["systemMessage"])
        self.assertIn("verlangt okfw 9.0", out["hookSpecificOutput"]["additionalContext"])

    def test_requires_core_beim_stop(self):
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('requires_core = "0.1"', 'requires_core = "9.0"'),
                        encoding="utf-8")
        out = json.loads(self.hook("stop", {"hook_event_name": "Stop"}).stdout)
        self.assertIn("verlangt okfw 9.0", out["systemMessage"])

    def guard_auf(self, target) -> dict | None:
        done = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(self.root),
                                   "tool_input": {"file_path": str(target), "content": "x"}})
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout.strip() else None

    def test_ungueltige_wiki_toml_sperrt_schreiben(self):
        ausserhalb = self.base / "fremd" / "log"
        for toml in ("[wiki\n", "[paths]\nraw = 5\n", "export = 3\n", "wiki = 1\n", "[wiki]\nbundle = 1\n",
                     "[paths]\nprotected = [1]\n", '[paths]\nevents = "../fremd/log"\n', '[paths]\nraw = "../fremd"\n',
                     '[wiki]\nbundle = "../fremd"\n', '[paths]\nevents = ".git/config"\n',
                     '[paths]\nevents = ".claude/settings.local.json"\n', "x = " + "[" * 100000 + "\n"):
            with self.subTest(toml=toml):
                (self.root / "wiki.toml").write_text(toml, encoding="utf-8")
                for target in (self.root / "raw" / "x" / "original" / "a.md", self.root / "wiki" / "concepts" / "k.md"):
                    aus = self.guard_auf(target)
                    self.assertEqual(aus and aus["permissionDecision"], "deny", target)
                    self.assertIn("wiki.toml", aus["permissionDecisionReason"])
                self.assertIsNone(self.guard_auf(self.root / "wiki.toml"), "wiki.toml muss reparierbar bleiben")

    def test_anhang_als_symlink_wird_nicht_gesendet(self):
        geheim = self.datei("schluessel.txt", "GEHEIM\n")
        (self.root / "anhang.txt").symlink_to(geheim)
        meldung = self.py("import rollen\ntry:\n    result = rollen.anhaenge_lesen(['anhang.txt'])\n"
                          "except rollen.Fehler as exc:\n    result = str(exc)\n")
        self.assertIn("keine normale Datei", meldung)

    def test_post_edit_schreibt_nicht_ausserhalb(self):
        (self.root / "wiki.toml").write_text('[paths]\nevents = "../fremd/log"\n', encoding="utf-8")
        karte = self.root / "wiki" / "concepts" / "k.md"
        karte.parent.mkdir(parents=True, exist_ok=True)
        karte.write_text("---\ntitle: x\n---\n\nText\n", encoding="utf-8")
        done = self.hook("post-edit", {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.root),
                                       "tool_input": {"file_path": str(karte), "content": "x"}})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("außerhalb des Projekts", json.loads(done.stdout)["systemMessage"])
        self.assertFalse((self.base / "fremd").exists())

    def test_schreibweise_umgeht_die_sperren_nicht(self):
        for target in ("wiki/Index.md", "wiki/LOG.md", "wiki/.events.JSONL", "RAW/x/a.md", "WIKI/index.md"):
            with self.subTest(target=target):
                aus = self.guard_auf(self.root / target)
                self.assertEqual(aus and aus["permissionDecision"], "deny")

    def test_kaputte_wiki_toml_beim_sitzungsstart(self):
        (self.root / "wiki.toml").write_text("[wiki\n", encoding="utf-8")
        out = json.loads(self.hook("session-start", {"hook_event_name": "SessionStart"}).stdout)
        self.assertIn("nicht lesbar", out["systemMessage"])

    def test_ohne_wiki_toml_still(self):
        leer = self.base / "ohne-wiki"
        leer.mkdir()
        for name in ("session-start", "stop"):
            done = self.hook(name, {"hook_event_name": "Stop"}, project=leer)
            self.assertEqual((done.returncode, done.stdout), (0, ""), name)

    def test_kern_direkt_ohne_wiki_toml_still(self):
        # so riefen Vorlagen vor 0.5.0 den Kern auf; eigene settings.json können das weiter tun
        leer = self.base / "ohne-wiki"
        leer.mkdir()
        for args in (("status", "--hook"), ("rebuild", "--hook"), ("rebuild", "--quiet")):
            done = self.core(*args, root=leer)
            self.assertEqual(done.stdout, "", args)


class HookProjektordner(WikiTest):
    def test_wiki_aus_einem_unterordner(self):
        unter = self.root / "kapitel" / "teil"
        unter.mkdir(parents=True)
        start = json.loads(self.hook("session-start", {"hook_event_name": "SessionStart"}, project=unter).stdout)
        self.assertIn(f"Wiki unter {self.root / 'wiki'}", start["hookSpecificOutput"]["additionalContext"])
        self.core("new", "concept", "k1", "--title", "Konzept", "--description", "Test")
        self.core("body", "concepts/k1", "-", stdin="Aussage ohne Quelle.[^fehlt]\n\n[^fehlt]: irgendwo\n")
        out = json.loads(self.hook("stop", {"hook_event_name": "Stop"}, project=unter).stdout)
        self.assertIn("footnote_unsourced", out["systemMessage"])

    def test_vorlage_ruft_den_kern_nicht_direkt_auf(self):
        vorlage = json.loads((HOOK.parent / "settings.example.json").read_text(encoding="utf-8"))
        befehle = {event: [h["command"] for g in gruppen for h in g["hooks"]] for event, gruppen in vorlage["hooks"].items()}
        for event, name in (("SessionStart", "session-start"), ("Stop", "stop")):
            self.assertEqual(len(befehle[event]), 1)
            self.assertIn(f'okf_hook.py\" {name}', befehle[event][0])
        self.assertFalse([c for cs in befehle.values() for c in cs if "okfw.py" in c])
