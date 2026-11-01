"""Gemeinsame Hilfen für die Tests des Kerns und der Hooks (nur Standardbibliothek).

Die Tests rufen Kern und Hooks so auf, wie Claude und Claude Code es tun: über `uv run --script`.
Was ein Test im Kern selbst nachsehen muss, läuft als kurzes Skript mit demselben PEP-723-Kopf wie
okfw.py (`py`), damit keine Abhängigkeit dazukommt. Jeder Test bekommt ein frisches Wiki unter $TMPDIR.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
# OKFW_SKILL prüft einen anderen Skill-Ordner, etwa eine installierte Kopie oder einen älteren Stand
SKILL = Path(os.environ.get("OKFW_SKILL") or PLUGIN / "skills" / "okfw").resolve()
CORE = SKILL / "okfw.py"
HOOK = SKILL / "hooks" / "okf_hook.py"
UV = shutil.which("uv")
HEADER = re.search(r"^# /// script\n.*?^# ///\n", CORE.read_text(encoding="utf-8"), re.S | re.M).group(0)


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "WIKI_ACTOR", "CLAUDE_PLUGIN_ROOT")}
    env.update(extra)
    return env


@unittest.skipIf(UV is None, "uv fehlt im PATH")
class WikiTest(unittest.TestCase):
    profile = "default"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="okfw-test-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._unlock)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "projekt"
        self.root.mkdir()
        self.core("init", "--profile", self.profile)

    def _unlock(self):
        # Rohquellen sind 0444; zum Aufräumen wieder beschreibbar machen
        for path in self.base.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)

    def core(self, *args, ok=True, stdin=None, root: Path | None = None) -> subprocess.CompletedProcess:
        root = root or self.root
        done = subprocess.run([UV, "run", "--quiet", "--script", str(CORE), "--root", str(root), *args],
                              capture_output=True, text=True, input=stdin, env=clean_env(), cwd=root, timeout=120)
        if ok is True:
            self.assertEqual(done.returncode, 0, f"okfw {' '.join(args)}\n{done.stdout}\n{done.stderr}")
        elif ok is False:
            self.assertNotEqual(done.returncode, 0, f"okfw {' '.join(args)} hätte scheitern sollen\n{done.stdout}")
        return done

    def py(self, code: str):
        """Führt `code` mit importiertem okfw und `wiki` aus; was der Code in `result` ablegt, kommt als JSON zurück."""
        script = self.base / "probe.py"
        script.write_text(HEADER + "import json, sys\n"
                          f"sys.path.insert(0, {str(SKILL)!r})\nimport okfw\n"
                          f"wiki = okfw.Wiki.load(root={str(self.root)!r})\nresult = None\n"
                          + code + "\nprint(json.dumps(result, ensure_ascii=False, default=str))\n", encoding="utf-8")
        done = subprocess.run([UV, "run", "--quiet", "--script", str(script)], capture_output=True, text=True,
                              env=clean_env(), cwd=self.root, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def hook(self, name: str, event: dict, project: Path | None = None) -> subprocess.CompletedProcess:
        env = clean_env(CLAUDE_PROJECT_DIR=str(project or self.root), TMPDIR=str(self.base))   # Rundenzähler je Test
        return subprocess.run([UV, "run", "--quiet", "--script", str(HOOK), name], input=json.dumps(event),
                              capture_output=True, text=True, env=env, cwd=self.base, timeout=120)

    def fm(self, page: str) -> dict:
        return self.py(f"result = wiki.parse(wiki.page_path({page!r})).fm")

    def text(self, page: str) -> str:
        return (self.root / "wiki" / f"{page}.md").read_text(encoding="utf-8")

    def lint_codes(self, page: str | None = None) -> list[str]:
        out = json.loads(self.core("lint", "--json", ok=None).stdout)
        return [f["code"] for f in out["findings"] if page is None or f["path"] == f"wiki/{page}.md"]

    def datei(self, name: str, text: str = "Inhalt\n") -> Path:
        path = self.base / "downloads" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def quelle(self, slug: str, **opts) -> None:
        """Quellenkarte ohne Rohquelle (access citation), für Seiten, die Quellen brauchen."""
        self.core("new", "source", slug, "--title", f"Quelle {slug}", "--description", "Testquelle",
                  "--access", "citation", "--resource", f"https://example.org/{slug}", *opts.get("extra", ()))
