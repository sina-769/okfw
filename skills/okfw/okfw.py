#!/usr/bin/env -S uv run --no-config --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "markdown>=3.5"]
# ///
"""okfw – gemeinsamer Kern für Wissens-Wikis im Open Knowledge Format v0.2.

Eingang für Aufruf und Bibliothek; der Code liegt nach Aufgaben getrennt in okfw_kern/, die Befehle und ihre
Hilfe in okfw_kern/cli.py. Hier bleibt der gemeinsame Namensraum: Alles aus den Modulen ist auch okfw.<name>.

  sys.path.insert(0, str(Path.home() / ".claude/skills/okfw")); import okfw
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # auch beim Laden über den Dateipfad; die eigene Kopie gewinnt

from okfw_kern.basis import *  # noqa: E402,F403
from okfw_kern.basis import _Druckbar, _manifest_lesen  # noqa: E402,F401
from okfw_kern.frontmatter import *  # noqa: E402,F403
from okfw_kern.frontmatter import _Loader  # noqa: E402,F401
from okfw_kern.bereinigung import *  # noqa: E402,F403
from okfw_kern.bereinigung import _EntryCleaner  # noqa: E402,F401
from okfw_kern.rechte import *  # noqa: E402,F403
from okfw_kern.rechte import _BY, _QUOTE  # noqa: E402,F401
from okfw_kern.konfig import *  # noqa: E402,F403
from okfw_kern.quellen import *  # noqa: E402,F403
from okfw_kern.wiki_quellen import *  # noqa: E402,F403
from okfw_kern.wiki_sicht import *  # noqa: E402,F403
from okfw_kern.wiki_indexe import *  # noqa: E402,F403
from okfw_kern.wiki_lint import *  # noqa: E402,F403
from okfw_kern.wiki_dashboard import *  # noqa: E402,F403
from okfw_kern.wiki_ablage import *  # noqa: E402,F403
from okfw_kern.wiki import *  # noqa: E402,F403
from okfw_kern.cli import *  # noqa: E402,F403


class _Kern(type(sys)):
    """`okfw.name = wert` gilt wie in der früheren einen Datei überall, etwa für den Netzzugriff (http_json)."""

    def __setattr__(self, name, wert):
        for modul in list(sys.modules.values()):
            if getattr(modul, "__name__", "").startswith("okfw_kern.") and name in vars(modul):
                setattr(modul, name, wert)
        super().__setattr__(name, wert)


if __name__ in sys.modules:
    sys.modules[__name__].__class__ = _Kern


if __name__ == "__main__":
    sys.stdout, sys.stderr = _Druckbar(sys.stdout), _Druckbar(sys.stderr)
    sys.exit(main())
