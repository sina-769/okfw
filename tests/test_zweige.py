"""Parallele Zweige: Ereignisstrom und Projektionen vereinigen sich ohne Konflikt, rebuild stellt die Ordnung her."""

import json
import subprocess
import time

from .hilfe import WikiTest, clean_env


class Zweige(WikiTest):
    def git(self, *args, ok=True) -> subprocess.CompletedProcess:
        done = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                               "-c", "commit.gpgsign=false", *args],
                              capture_output=True, text=True, env=clean_env(), cwd=self.root, timeout=60)
        if ok:
            self.assertEqual(done.returncode, 0, f"git {' '.join(args)}\n{done.stdout}\n{done.stderr}")
        return done

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def karte(self, slug: str) -> None:
        self.core("new", "concept", slug, "--title", slug, "--description", "Test", "--sources", "q1")
        self.core("rebuild")
        self.commit(slug)

    def parallel(self) -> None:
        """Basis mit Quelle, dann zwei Zweige: alt schreibt zuerst, neu mindestens eine Sekunde später."""
        self.quelle("q1")
        self.core("rebuild")
        self.git("init", "-q", "-b", "basis")
        self.commit("basis")
        self.git("switch", "-q", "-c", "alt")
        self.karte("k-alt")
        time.sleep(1.1)   # Zeitstempel haben Sekunden
        self.git("switch", "-q", "-c", "neu", "basis")
        self.karte("k-neu")

    def events(self) -> list[dict]:
        lines = (self.root / "wiki" / ".events.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_init_traegt_die_regeln_ein(self):
        self.git("init", "-q")
        for path in ("wiki/.events.jsonl", "wiki/log.md", "wiki/index.md", "wiki/concepts/index.md"):
            out = self.git("check-attr", "merge", "--", path).stdout
            self.assertIn("merge: union", out, path)
        self.assertNotIn("merge: union", self.git("check-attr", "merge", "--", "wiki/concepts/k.md").stdout)

    def test_befehl_ergaenzt_und_ist_wiederholbar(self):
        attrs = self.root / ".gitattributes"
        attrs.write_text("*.png binary", encoding="utf-8")
        toml = self.root / "wiki.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + '\n[export.csl]\npath = "aus gabe/refs.json"\n', encoding="utf-8")
        self.assertIn("ergänzt", self.core("gitattributes").stdout)
        text = attrs.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("*.png binary\n"), text)
        self.assertIn('"aus gabe/refs.json" merge=union\n', text)
        self.assertIn("schon vollständig", self.core("gitattributes").stdout)
        self.assertEqual(attrs.read_text(encoding="utf-8"), text)

    def test_merge_ohne_konflikt_und_rebuild_ordnet_nach_zeit(self):
        self.parallel()
        self.git("merge", "--no-ff", "--no-commit", "alt")   # neu ist „ours“: das spätere Ereignis steht vorn
        self.assertEqual(self.git("diff", "--name-only", "--diff-filter=U").stdout, "")
        ts = [e["ts"] for e in self.events()]
        self.assertNotEqual(ts, sorted(ts), "der Test braucht eine Datei, die nicht nach Zeit steht")
        self.core("rebuild")
        self.git("add", "-u")
        self.git("commit", "-q", "--no-edit")
        self.assertNotIn("projection_stale", self.lint_codes())
        log = self.text("log")
        self.assertNotIn("<<<<", log)
        self.assertLess(log.index("k-neu"), log.index("k-alt"), "neueste zuerst")
        self.assertIn("k-alt", self.text("concepts/index"))
        self.assertIn("k-neu", self.text("concepts/index"))
        self.assertEqual(self.py("result = [e['ts'] for e in wiki.read_events()]"), sorted(ts))

    def test_beide_merge_richtungen_ergeben_dasselbe_log(self):
        self.parallel()
        logs = []
        for ours, theirs in (("neu", "alt"), ("alt", "neu")):
            self.git("switch", "-q", ours)
            self.git("merge", "--no-ff", "--no-commit", theirs)
            self.core("rebuild")
            logs.append((self.text("log"), self.text("concepts/index")))
            self.git("reset", "-q", "--hard")
        self.assertEqual(logs[0], logs[1])

    def test_wiki_ohne_regeln_scheitert_bis_zum_befehl(self):
        self.parallel()
        (self.root / ".gitattributes").unlink()
        self.commit("Regeln weg")
        self.assertNotEqual(self.git("merge", "--no-ff", "--no-commit", "alt", ok=False).returncode, 0)
        konflikte = self.git("diff", "--name-only", "--diff-filter=U").stdout.split()
        self.assertIn("wiki/.events.jsonl", konflikte)
        self.assertIn("wiki/log.md", konflikte)
        self.git("merge", "--abort")
        self.core("gitattributes")
        self.commit("Regeln")
        self.git("merge", "--no-ff", "--no-commit", "alt")
        self.assertEqual(self.git("diff", "--name-only", "--diff-filter=U").stdout, "")

    def test_doppelte_zeile_zaehlt_einmal(self):
        path = self.root / "wiki" / ".events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        vorher = self.py("result = len(wiki.read_events())")
        path.write_text("\n".join(lines + [lines[-1]]) + "\n", encoding="utf-8")
        self.assertEqual(self.py("result = len(wiki.read_events())"), vorher)
