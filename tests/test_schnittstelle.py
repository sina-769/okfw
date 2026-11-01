"""Feste Schnittstelle: Was docs/konfiguration.md Anwendungen zusagt, gibt es im Kern, und Ergänzungen brechen es nicht."""

import re
import unittest

from .hilfe import SKILL, WikiTest


def zusagen() -> dict[str, list[str] | None]:
    """Name → zugesagte Parameter, None für ein Attribut. Quelle ist allein die Tabelle in der Doku."""
    text = (SKILL / "docs" / "konfiguration.md").read_text(encoding="utf-8")
    abschnitt = text.split("### Feste Schnittstelle", 1)[1].split("\n#", 1)[0]
    ergebnis = {}
    for name, rest in re.findall(r"^\| `([\w.]+)` \|(.*)\|$", abschnitt, re.M):
        ergebnis[name] = None if rest.strip() == "Attribut" else re.findall(r"`(\w+)`", rest)
    return ergebnis


def bruch(zugesagt: list[str], ist: list[list]) -> str | None:
    """Die Zusage muss vorne stehen; was dahinter dazukommt, braucht einen Standardwert."""
    namen = [p[0] for p in ist]
    if namen[:len(zugesagt)] != zugesagt:
        return f"zugesagt {zugesagt}, im Code {namen}"
    pflicht = [p[0] for p in ist[len(zugesagt):] if p[1]]
    return f"neue Pflichtparameter: {pflicht}" if pflicht else None


PROBE = """
import inspect
def parameter(o):
    return [[p.name, p.default is inspect.Parameter.empty] for p in inspect.signature(o).parameters.values()
            if p.name not in ("self", "cls")]
def attribut(klasse, name):
    return hasattr(klasse, name) or name in getattr(klasse, "__dataclass_fields__", {})
result = {}
for name in NAMEN:
    wo, kurz = name.split(".")
    halter = {"okfw": okfw, "Wiki": okfw.Wiki, "Page": okfw.Page, "Finding": okfw.Finding}[wo]
    da = attribut(wiki if wo == "Wiki" else halter, kurz)
    wert = getattr(halter, kurz, None)
    result[name] = {"da": da, "parameter": parameter(wert) if da and callable(wert) and not inspect.isclass(wert) else None}
"""


class FesteSchnittstelle(WikiTest):
    def test_tabelle_ist_gefuellt(self):
        self.assertIn("Wiki.capture", zusagen())
        self.assertIsNone(zusagen()["Finding.code"])

    def test_jede_zusage_gilt_im_code(self):
        namen = zusagen()
        ist = self.py(f"NAMEN = {sorted(namen)!r}\n{PROBE}")
        for name, zugesagt in namen.items():
            with self.subTest(name=name):
                self.assertTrue(ist[name]["da"], f"{name} fehlt im Kern")
                if zugesagt is not None:
                    self.assertIsNotNone(ist[name]["parameter"], f"{name} ist nicht aufrufbar")
                    self.assertIsNone(bruch(zugesagt, ist[name]["parameter"]))


class BruchErkennen(unittest.TestCase):
    def test_ergaenzung_ist_kein_bruch(self):
        self.assertIsNone(bruch(["a"], [["a", True], ["neu", False]]))

    def test_umbenannt_oder_umgestellt(self):
        self.assertIn("zugesagt", bruch(["a", "b"], [["a", True], ["c", True]]))
        self.assertIn("zugesagt", bruch(["a", "b"], [["b", True], ["a", True]]))

    def test_neuer_pflichtparameter(self):
        self.assertIn("Pflicht", bruch(["a"], [["a", True], ["neu", True]]))


if __name__ == "__main__":
    unittest.main()
