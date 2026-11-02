"""Lizenzklassen und Rechte-Matrix: was mit einer Quelle erlaubt ist."""

import re


# Lizenzklassen vom offensten zum strengsten. Passen mehrere Muster, gilt die strengste Klasse.
LICENSE_CLASSES = {
    "frei": "Gemeinfrei", "by": "Namensnennung", "sa": "Namensnennung, gleiche Lizenz",
    "nd": "Keine Bearbeitung", "nc": "Nicht kommerziell", "nc-nd": "Nicht kommerziell, keine Bearbeitung",
    "vorbehalten": "Alle Rechte vorbehalten", "unbekannt": "Unbekannt",
}
LICENSE_PATTERNS = [  # (Klasse, Muster auf Name und URL in Kleinbuchstaben)
    ("frei", r"cc0|publicdomain|public domain|gemeinfrei|amtliches werk|§ ?5 urhg|regierungswerk"
             r"|government work|dl-de/zero|unlicense"),
    ("by", r"\bcc[ -]?by\b|creativecommons\.org/licenses/by/|^(the )?mit( license)?$|opensource\.org/licenses/mit"
           r"|apache|\bbsd[- ]|^isc$|\bmpl\b|2011/833|dl-de/by|open government licen"),
    ("sa", r"by-sa\b|sharealike|\b[al]?gpl|copyleft|odbl"),
    ("nd", r"by-nd\b|noderiv|keine bearbeitung"),
    ("nc", r"by-nc\b|by-nc-sa|noncommercial|nicht kommerziell"),
    ("nc-nd", r"by-nc-nd|nc-nd"),
    ("vorbehalten", r"alle rechte vorbehalten|all rights reserved|nutzungsbedingungen|terms ?(of|and|&) ?(use|service|conditions)"
                    r"|termsandconditions|site-polic|/tdm\b|tdm[_-]?licen|userlicense|urheberrechtlich geschützt"
                    r"|proprietär|proprietary|abdruck|text-and-data-mining|copyright[_-]polic|10\.15223/policy-"
                    r"|arxiv\.org/licenses/nonexclusive"),
]
# Was die Person mit einer Quelle tun will. Werte: ja, bedingt, nein, fragen – jeweils mit Hinweis nach „:“.
RIGHTS_ACTIONS = {
    "zitieren": "Kurz zitieren mit Beleg",
    "volltext_ablegen": "Volltext lokal ablegen (eigene Arbeit)",
    "volltext_weitergeben": "Volltext weitergeben oder versionieren",
    "abbildung": "Abbildung oder Tabelle übernehmen",
    "bearbeiten": "Bearbeiten, übersetzen, nachzeichnen",
    "ki_auswertung": "Volltext maschinell auswerten lassen",
    "kommerziell": "Im kommerziellen Rahmen nutzen",
}
_BY = "mit Namensnennung und Lizenzangabe"
_QUOTE = "ja: im Rahmen des Zitatrechts (§ 51 UrhG)"
RIGHTS_MATRIX = {
    "frei": {a: "ja" for a in RIGHTS_ACTIONS},
    "by": {"zitieren": "ja", "volltext_ablegen": "ja", "volltext_weitergeben": f"bedingt: {_BY}",
           "abbildung": f"bedingt: {_BY}", "bearbeiten": f"bedingt: {_BY}, Änderungen kennzeichnen",
           "ki_auswertung": "ja", "kommerziell": "ja"},
    "sa": {"zitieren": "ja", "volltext_ablegen": "ja", "volltext_weitergeben": f"bedingt: {_BY}, unter gleicher Lizenz",
           "abbildung": f"bedingt: {_BY}, unter gleicher Lizenz",
           "bearbeiten": f"bedingt: {_BY}, Änderungen kennzeichnen, unter gleicher Lizenz",
           "ki_auswertung": "ja", "kommerziell": "ja"},
    "nd": {"zitieren": "ja", "volltext_ablegen": "ja", "volltext_weitergeben": f"bedingt: nur unverändert, {_BY}",
           "abbildung": f"bedingt: nur unverändert, {_BY}", "bearbeiten": "nein: Lizenz verbietet Bearbeitungen",
           "ki_auswertung": "ja", "kommerziell": "ja"},
    "nc": {"zitieren": "ja", "volltext_ablegen": "ja", "volltext_weitergeben": f"bedingt: nicht kommerziell, {_BY}",
           "abbildung": f"bedingt: nicht kommerziell, {_BY}", "bearbeiten": f"bedingt: nicht kommerziell, {_BY}",
           "ki_auswertung": "ja", "kommerziell": "nein: Lizenz erlaubt nur nicht kommerzielle Nutzung"},
    "nc-nd": {"zitieren": "ja", "volltext_ablegen": "ja",
              "volltext_weitergeben": f"bedingt: nicht kommerziell und unverändert, {_BY}",
              "abbildung": f"bedingt: nicht kommerziell und unverändert, {_BY}",
              "bearbeiten": "nein: Lizenz verbietet Bearbeitungen", "ki_auswertung": "ja",
              "kommerziell": "nein: Lizenz erlaubt nur nicht kommerzielle Nutzung"},
    "vorbehalten": {"zitieren": _QUOTE, "volltext_ablegen": "bedingt: nur für die eigene Arbeit, nicht weitergeben",
                    "volltext_weitergeben": "nein: ohne Erlaubnis des Rechteinhabers nicht",
                    "abbildung": "fragen: Genehmigung des Rechteinhabers nötig",
                    "bearbeiten": "nein: ohne Erlaubnis des Rechteinhabers nicht",
                    "ki_auswertung": "bedingt: nur eigene Auswertung; Text-und-Data-Mining-Vorbehalt des Anbieters "
                                     "beachten, nicht an Dienste geben, die speichern oder trainieren",
                    "kommerziell": "fragen: Nutzungsbedingungen prüfen"},
    "unbekannt": {**{a: "fragen: Lizenz zuerst klären" for a in RIGHTS_ACTIONS}, "zitieren": _QUOTE},
}
RIGHT_VALUES = ("ja", "bedingt", "nein", "fragen")
LICENSE_HINT = (" (unknown ist gültig; `lizenz <seite> --fetch` sucht über Crossref, DataCite, Europe PMC und "
                "OpenAlex)")
UNKNOWN_LICENSE_RE = re.compile(r"^(unknown|unbekannt|unklar|n/?a|none|keine angabe|-+)?$", re.I)


def license_parts(lic) -> tuple[str, str]:
    """(Name, URL) aus license: {name, url, …} oder einem bloßen String."""
    if isinstance(lic, dict):
        return str(lic.get("name") or "").strip(), str(lic.get("url") or "").strip()
    return (str(lic).strip() if lic else ""), ""


def classify_license(lic, patterns: dict | None = None) -> tuple[str, bool]:
    """(Lizenzklasse, eingeordnet). license.class im Frontmatter geht vor, sonst die strengste passende Klasse.

    eingeordnet ist False, wenn ein Name dasteht, aber kein Muster passt.
    """
    if isinstance(lic, dict) and lic.get("class") in LICENSE_CLASSES:
        return lic["class"], True
    name, url = license_parts(lic)
    rules = LICENSE_PATTERNS + [(cls, pat) for pat, cls in (patterns or {}).items() if cls in LICENSE_CLASSES]
    found = {cls for text in (name.lower(), url.lower()) if text for cls, pat in rules if re.search(pat, text)}
    if not found:
        return "unbekannt", bool(UNKNOWN_LICENSE_RE.match(name))
    order = list(LICENSE_CLASSES)
    return max(found, key=order.index), True


def parse_right(value) -> tuple[str, str]:
    """„bedingt: mit Namensnennung“ → ("bedingt", "mit Namensnennung"); Unlesbares gilt als „fragen“."""
    text = str(value or "").strip()
    head, _, note = text.partition(":")
    if head.strip().lower() in RIGHT_VALUES:
        return head.strip().lower(), note.strip()
    return "fragen", text


def license_name_from(text: str) -> str:
    """Lesbarer Lizenzname aus einer Lizenz-URL oder Kurzangabe (Crossref, DataCite, Europe PMC, OpenAlex); leer, wenn unklar."""
    t = str(text or "").strip()
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/(\d\.\d)(/igo)?", t, re.I)
    if m:
        if m.group(1).lower() == "publicdomain":
            return f"CC0 {m.group(3)}" if m.group(2).lower() == "zero" else "Public Domain Mark"
        return f"CC {m.group(2).upper()} {m.group(3)}" + (" IGO" if m.group(4) else "")
    m = re.match(r"^cc[ -]?(by(?:-(?:nc|nd|sa))*|0)\b\s*([\d.]+)?", t, re.I)
    if m:
        name = "CC0" if m.group(1) == "0" else f"CC {m.group(1).upper()}"
        return name + (f" {m.group(2)}" if m.group(2) else "")
    if classify_license({"name": "", "url": t})[0] == "vorbehalten":
        return "Nutzungsbedingungen des Verlags"
    return ""


RIGHT_WORDS = {"ja": "erlaubt", "bedingt": "mit Auflage", "nein": "nicht erlaubt", "fragen": "erst fragen"}
