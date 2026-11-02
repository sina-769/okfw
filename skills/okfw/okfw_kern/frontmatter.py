"""YAML und Frontmatter (sicherheitskritisch): sicherer Loader ohne Merge-Schlüssel und Dubletten, Lesen und Setzen einzelner Schlüssel, Prüfwert des Seitenkörpers."""

import hashlib
import re

import yaml


MAX_FRONTMATTER_BYTES = 64 * 1024
FRONTMATTER_RE = re.compile(r"---[ \t]*\n(.*?)^---[ \t]*\n?", re.S | re.M)


class _Loader(yaml.SafeLoader):
    """SafeLoader ohne Anker/Aliase, ohne implizite Zeitstempel, ohne Merge- und doppelte Schlüssel.

    Zeitstempel bleiben Strings, damit OKF-Werte wie 2026-06-30T14:00:00Z unverändert
    geprüft und zurückgeschrieben werden. Doppelte Schlüssel sind abgelehnt, weil
    set_frontmatter_key genau eine Zeile ändert; kein zweiter Eintrag darf dahinter weiter gelten.
    """

    def flatten_mapping(self, node):
        gesehen = set()
        for schluessel, _ in node.value:
            if schluessel.tag == "tag:yaml.org,2002:merge":
                raise yaml.YAMLError("YAML-Merge-Schlüssel sind nicht erlaubt")
            if isinstance(schluessel, yaml.ScalarNode):
                if (schluessel.tag, schluessel.value) in gesehen:
                    raise yaml.YAMLError(f"Doppelter Schlüssel {schluessel.value!r}")
                gesehen.add((schluessel.tag, schluessel.value))
        super().flatten_mapping(node)

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML-Aliase sind nicht erlaubt")
        if getattr(self.peek_event(), "anchor", None):
            raise yaml.YAMLError("YAML-Anker sind nicht erlaubt")
        return super().compose_node(parent, index)


_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(text: str):
    try:
        return yaml.load(text, Loader=_Loader)
    except RecursionError as exc:
        raise yaml.YAMLError("YAML zu tief verschachtelt") from exc


def dump_yaml(data) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)


def dump_key(key: str, value) -> str:
    """Ein Top-Level-Schlüssel im Blockstil; Blätter wie {by, at} bleiben einzeilig.

    PyYAML setzt eine Zuordnung, die nur Skalare enthält, ganz in Flow-Stil ({status: stable});
    deshalb Skalare im Blockstil und Sammlungen mit default_flow_style=None.
    """
    style = None if isinstance(value, (dict, list)) else False
    return yaml.safe_dump({key: value}, allow_unicode=True, sort_keys=False, default_flow_style=style, width=100)


def dump_frontmatter(fm: dict) -> str:
    return "".join(dump_key(k, v) for k, v in fm.items())


# generated.modelle: Art und Kennung eines Eintrags. Ein Eintrag bleibt, auch wenn ein Mensch den Text später ändert.
MODELL_ARTEN = {
    "text": "vom Akteur geschrieben",
    "entwurf": "Entwurf einer Rolle, eingearbeitet",
    "vorschlag": "Vorschlag unverändert übernommen",
    "vorschlag-bearbeitet": "Vorschlag überarbeitet übernommen",
    "akteur": "Akteur vor einer späteren Änderung, Modellanteil möglich",
}
MODELL_KENNUNG = ("harness", "anbieter", "modell", "rolle")
MODELL_UNBEKANNT = "unknown"


def akteur_modell(akteur) -> dict | None:
    """Kennung aus einem Akteur `<producer>/<version>`; human:<id> und process:<id> sind kein Modell."""
    m = re.fullmatch(r"([\w.-]+)/([\w.:-]+)", str(akteur or ""))
    return {"harness": m.group(1), "modell": m.group(2)} if m else None


def modelle_merge(modelle: list, eintrag: dict) -> list:
    """Dasselbe Modell in derselben Rolle bleibt ein Eintrag (der neueste Stand), andere Einträge bleiben unberührt. Ein
    bekanntes Modell ersetzt `unknown` bei gleichem Harness, Anbieter und gleicher Rolle."""
    def passt(e) -> bool:
        return isinstance(e, dict) and all(e.get(k) == eintrag.get(k) for k in MODELL_KENNUNG if k != "modell") \
            and e.get("modell") in (eintrag.get("modell"), MODELL_UNBEKANNT)
    gleich = [i for i, e in enumerate(modelle) if passt(e)]
    if not gleich:
        return [*modelle, eintrag]
    return [eintrag if i == gleich[0] else e for i, e in enumerate(modelle)]


def body_sha256(body: str) -> str:
    """Prüfwert des Markdown-Körpers ohne Frontmatter: Zeilenenden als LF, Leerzeilen an Anfang und Ende entfernt."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_frontmatter(text: str) -> tuple[str | None, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _key_lines(lines: list[str], key: str) -> tuple[int, int] | None:
    """Zeilenbereich eines Top-Level-Schlüssels, auch in Anführungszeichen; None, wenn er fehlt."""
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"""(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*:""", ln)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t", "- ")) or lines[end].strip() in ("", "-")):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return start, end


def set_frontmatter_key(raw: str, key: str, value) -> str:
    """Ersetzt einen Top-Level-Schlüssel im Frontmatter-Text; alles andere bleibt byte-genau."""
    block = dump_key(key, value)
    lines = raw.splitlines(keepends=True)
    bereich = _key_lines(lines, key)
    if bereich is None:
        return (raw if not raw or raw.endswith("\n") else raw + "\n") + block
    return "".join(lines[:bereich[0]]) + block + "".join(lines[bereich[1]:])


def remove_frontmatter_key(raw: str, key: str) -> str:
    """Entfernt einen Top-Level-Schlüssel aus dem Frontmatter-Text; alles andere bleibt byte-genau."""
    lines = raw.splitlines(keepends=True)
    bereich = _key_lines(lines, key)
    return raw if bereich is None else "".join(lines[:bereich[0]]) + "".join(lines[bereich[1]:])
