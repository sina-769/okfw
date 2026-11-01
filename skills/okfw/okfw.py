#!/usr/bin/env -S uv run --no-config --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0", "markdown>=3.5"]
# ///
"""okfw – gemeinsamer Kern für Wissens-Wikis im Open Knowledge Format v0.2.

Muster: pi-llm-wiki (https://github.com/zosmaai/pi-llm-wiki), übertragen auf Claude Code.
Jedes Projekt beschreibt sein Wiki in einer wiki.toml (Bundle-Ordner, Seitentypen,
Pflichtfelder, Ereignisort, Exporte). Ohne wiki.toml schreibt der Kern nichts.

  okfw.py status [--hook]
  okfw.py lint [--strict] [--json] [--summary]
  okfw.py rebuild [--check] [--quiet | --hook [--befunde]]
  okfw.py new <typ> <slug> --title T [--description D] [--sources a,b] [--tags x,y]
                 [--access A] [--resource URI] [--csl DATEI|-] [--set feld=wert ...]
                 [--doi D | --pmid P | --arxiv A | --manuell --titel T --url U [--autor --jahr --csl-typ]]
                 [--volltext PFAD [--von URL]] [--evidenz E] [--fundweg ART [--db --query] [--fundweg-von SLUG]
                 [--at ZEIT] [--by AKTEUR]] [--aus-lauf LAUF --kandidat N] [--trotz-rueckzug]
                 (deutsche Optionen: --titel, --beschreibung, --quellen)
  okfw.py new-source <slug> …   (= new source <slug> …)
  okfw.py capture <slug> --url URL --file PFAD[=REPO-PFAD] [--file ...] [--commit SHA]
                 [--license NAME --license-url URL] [--note N]
  okfw.py hash <pfad> [--from URL]
  okfw.py verify <seite> [<seite> ...] --by human:<id> [--status stable] [--note N] [--force]
  okfw.py event <ereignisart> <seite> [detail] [--by AKTEUR]
  okfw.py body <seite> <datei|-> | --nachziehen [--neu] [--detail D]   (Seitenname hinter "--", wenn er mit "-" beginnt)
  okfw.py sources <seite> [--add a,ID=b] [--remove c]
  okfw.py init [--profile NAME]
  okfw.py fundweg <seite> --art suche|modellvorschlag|material|person|zitatverfolgung
                 [--db DB --query Q] [--von SLUG] [--at ZEIT] [--by AKTEUR] [--add]
  okfw.py fundweg <seite> --aus-lauf LAUF --kandidat N [--add]
  okfw.py abbildung <name> --file x.svg [--ersetzen]
  okfw.py rechte [<seite>] [--aktion abbildung] [--json]
  okfw.py lizenz <seite> --name N [--url U] [--class K] | --fetch [--force] | --missing --fetch [--dry-run]
  okfw.py dashboard [--out PFAD] [--oeffentlich] [--open]
  okfw.py serve [--port N] [--no-open]   (Dashboard mit Bearbeiten, nur lokal; Modul okfw_serve.py)

Globale Optionen: --config PFAD (sonst wiki.toml aufwärts ab cwd), --root DIR (Projektordner = Ordner der wiki.toml).
Als Bibliothek: Wiki.load(config=..., root=...) mit pages(), lint(), projections(),
rebuild(), new_page(), capture(), verify(), append_event():

  sys.path.insert(0, str(Path.home() / ".claude/skills/okfw")); import okfw
"""

import argparse
import calendar
import datetime as dt
import difflib
import hashlib
import html
import http.client
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import yaml

VERSION = "0.6.0"
SKILL_DIR = Path(__file__).resolve().parent
RESERVED = {"index.md", "log.md"}
STATUS = ("draft", "stable", "deprecated")
SOURCE_TYPE = "source"
# Prüfwert des Seitenkörpers in jedem verified-Eintrag, den `verify` schreibt (Erweiterung zu OKF §5.2)
VERIFIED_HASH = "body_sha256"

MAX_FRONTMATTER_BYTES = 64 * 1024
SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
ACTOR_RE = re.compile(r"^(human:[\w.-]+|process:[\w.-]+|[\w.-]+/[\w.:-]+)$")
FRONTMATTER_RE = re.compile(r"---[ \t]*\n(.*?)^---[ \t]*\n?", re.S | re.M)
LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
FOOTNOTE_TEXT_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]*(.*)$", re.M)
FENCE_RE = re.compile(r"```.*?```", re.S)
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
H2_RE = re.compile(r"^## +(.+?)\s*$", re.M)
CONTRADICTION_RE = re.compile(r"⚠️\s*\*\*Widerspruch:?\*\*")
# Dashboard: Codeblöcke dieser Sprachen (und unmarkierte mit Rahmenzeichen) sind Textdiagramme
DIAGRAM_LANGS = {"text", "txt", "plain", "plaintext", "ascii", "tree", "diagram"}
DIAGRAM_PRE_RE = re.compile(r'<pre><code(?: class="([^"]*)")?>(.*?)</code></pre>', re.S)
BOX_DRAWING_RE = re.compile(r"[─-╿]")
# ausdrücklicher Prüfvermerk: „zu prüfen“ am Blockanfang, in Anführungszeichen oder Klammern, nicht im Fließtext
CHECK_RE = re.compile(r"^\W*zu prüfen\b|[„“”\"‚‘(\[]\s*zu prüfen\b|\bzu prüfen\s*[)\]“”\"‘’]", re.I)
# Schaubilder: <bundle>/abbildungen/<name>.svg, eingebunden als ![Titel](/abbildungen/name.svg) und darunter *Abb. …*
FIGURES_DIR = "abbildungen"
FIGURE_SRC_RE = re.compile(r"^/abbildungen/([a-z0-9]+(?:-[a-z0-9]+)*)\.svg$")
IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FIGURE_CAPTION_RE = re.compile(r"^\s*\*Abb\.")
FIGURE_MAX_BYTES = 200 * 1024
FIGURE_MAX_ELEMENTS = 5000
FIGURE_MAX_EXPANSION = 20000   # Elemente nach dem Auflösen aller <use>: verschachteltes <use> wächst sonst exponentiell
# Datenklassen eines Wikis ([rollen] daten); dieselben prüft rollen.py
DATENKLASSEN = ("oeffentlich", "intern", "personenbezogen")

DEFAULT_CONFIG = {
    "wiki": {"title": "Wiki", "bundle": "wiki", "actor": "claude-code/unknown", "okf_version": "0.2",
             "requires_core": ""},
    "paths": {"events": "", "raw": "", "protected": []},
    "index": {"root": "overview", "sort": "title", "entry_tag": "einstieg", "badges": [], "star_field": "",
              "banner": "", "start_heading": "Zuerst lesen", "areas_heading": "Bereiche", "unit": "Seiten"},
    "log": {"title": "Aktivitätslog", "banner": "", "show_actor": False, "link_text": "title", "labels": {}},
    "enums": {},
    "lint": {"orphans": "inbound", "fulltext_access": ["fulltext"], "claims_section": "Kernaussagen",
             "priority_field": "", "severity": {}},
    "types": {},
    "export": {},
    "dashboard": {"path": "build/wiki-dashboard.html", "auto": False, "public_path": "",
                  "glossary": ["concept", "entity"],   # Typen, deren Seiten ins Glossar kommen
                  "design": ""},                       # CSS-Datei, die nach der Vorlage eingebettet wird; leer: design.css, falls vorhanden
    "taxonomy": {"field": "domain"},   # Feld, das die Dashboard-Suche als Themenbereich mitliest
    "labels": {},
    "licenses": {"patterns": {}},     # weitere Muster: {"example.org" = "vorbehalten"}
    "rights": {},                     # Matrix überschreiben: [rights.vorbehalten] abbildung = "nein: …"
    "scope": {"field": "", "label": "Umfang", "default": "", "derive": True},
    # Seiten mit einem Wert aus hidden fehlen in der öffentlichen Ausgabe (dashboard --oeffentlich) ganz
    "visibility": {"field": "", "label": "Sichtbarkeit", "default": "", "hidden": [], "derive": True},
    "fundweg": {"field": "fundweg"},
    # Freigabe: body auf einer stable-Seite setzt draft (die Freigabe gilt dem alten Inhalt)
    # signed_human: Lint human_unsigned; human: Kennung, die „Zur Freigabe“ im Dashboard vorbelegt (human:jdoe);
    # check_command: Aufruf der Gegenprüfung für die Liste „Gegenprüfung ohne Prüfwert“ (z. B. /lesen --pruefen)
    "trust": {"edit_resets_stable": True, "signed_human": False, "human": "", "check_command": ""},
    # new source --doi/--pmid/--arxiv/--manuell: wohin Metadatenprüfung und Crossref-Hinweise gehen
    "sources": {"checked_field": "metadaten_geprueft", "updates_field": "crossref_hinweise",
                "retraction_field": "rueckzug", "manual_csl_type": "document"},
    # Modellrollen (rollen.py): Datenklasse des Wikis und gesperrte Anbieter; je Rolle eine freie Tabelle
    # [rollen.lesen] harness, anbieter, modell, die rollen.py prüft
    "rollen": {"daten": "", "sperre": []},
}

TYPE_DEFAULTS = {"folder": "", "label": "", "description": "", "required": [], "recommended": ["title", "description"],
                 "sections": [], "min_links": 2, "needs_footnotes": False, "stale_months": 0, "template": "",
                 "defaults": {}, "todo": {}, "subtype": ""}
# Exporte, die der Kern schreibt, mit ihren Schlüsseln ([export.csl], [export.bibliography]); ohne Eintrag kein Export
EXPORT_KEYS = {"csl": ("path", "field", "style", "lang"), "bibliography": ("path", "title")}
# Abschnitte der wiki.toml mit Projektwerten als Schlüsseln (Feldnamen, Klassen): config_unknown prüft sie nicht.
# Ebenso frei, weil unknown_config in bekannte Schlüssel nicht absteigt: [log] labels, [licenses.patterns] und
# defaults und todo der Typen.
FREE_TABLES = {"enums", "labels", "rights"}

# Platzhalter für Kurzreferat und Kernaussagen neuer Quellenkarten je access; [types.source.todo.<access>] ersetzt sie.
TODO_BY_ACCESS = {
    "citation": {"kurzreferat": "> [!todo] Nur Metadaten geprüft – Inhalt noch nicht gelesen. Nichts erfinden.",
                 "kernaussagen": "> [!todo] Erst nach Lektüre (Abstract oder Volltext)."},
    "abstract": {"kurzreferat": "> [!todo] Kurzreferat aus dem Abstract (als *abstract-basiert* kennzeichnen).",
                 "kernaussagen": "> [!todo] Höchstens Abstract-Aussagen, mit „(Abstract)“ markiert."},
    "fulltext": {"kurzreferat": "> [!todo] Kurzreferat aus dem Volltext.",
                 "kernaussagen": "> [!todo] Kernaussagen mit Fundstelle (S./Tab./Abschn.)."},
    "originaldokument": {"kurzreferat": "> [!todo] Kurzreferat aus dem Originaldokument.",
                         "kernaussagen": "> [!todo] Kernaussagen mit Fundstelle (Art./Abs./Abschn./Zeile)."},
}
CROSSREF_TYPES = {"journal-article": "article-journal", "proceedings-article": "paper-conference",
                  "posted-content": "article", "book-chapter": "chapter"}

LOG_LABELS = {
    "init": "Initialisierung", "bootstrap": "Bootstrap", "capture": "Quelle erfasst", "create": "Erstellt",
    "creation": "Erstellt", "update": "Aktualisiert", "verify": "Verifiziert", "deprecate": "Als überholt markiert",
    "delete": "Entfernt", "merge": "Zusammengeführt", "enrich": "Angereichert", "export": "Export", "note": "Notiz",
}

# Schwere je Befund. Projekte überschreiben einzelne Codes in [lint.severity] (auch mit "off").
SEVERITY = {
    # OKF §8, §9, §11 – Konformität
    "bundle_missing": "error", "frontmatter_invalid": "error", "missing_type": "error",
    "root_index_frontmatter": "error", "okf_version_missing": "warn", "index_frontmatter": "error",
    "log_frontmatter": "error", "log_date_heading": "error", "log_order": "warn",
    # OKF §5, §7 – Herkunft, Vertrauen, Lebenszyklus
    "status_invalid": "error", "generated_invalid": "error", "verified_invalid": "error",
    "timestamp_invalid": "error", "actor_format": "warn", "stable_unverified": "warn", "stale": "warn",
    "verified_outdated": "warn",   # bei status stable error
    "order_invalid": "warn",
    "human_without_check": "warn", "human_unsigned": "warn", "verified_unhashed": "info", "check_outdated": "info",
    "source_invalid": "error", "source_duplicate_id": "error", "source_missing": "error",
    "footnote_unsourced": "error", "footnote_undefined": "warn", "source_uncited": "info",
    # Profil aus wiki.toml
    "missing_required": "error", "missing_recommended": "warn", "enum_invalid": "error",
    "type_unknown": "info", "type_folder_mismatch": "warn", "path_not_kebab": "warn",
    "sections_missing": "warn", "no_footnotes": "warn",
    # Graph
    "broken_link": "warn", "link_leaves_bundle": "warn", "few_links": "warn", "orphan": "warn",
    "contradiction_one_sided": "info", "projection_stale": "warn", "projection_unavailable": "info",
    # Quellenkarten und Rohquellen
    "raw_missing": "error", "raw_tampered": "error", "raw_uningested": "info",
    "fulltext_tampered": "error", "fulltext_absent": "warn", "fulltext_unanchored": "warn",
    "citation_with_claims": "error", "claim_on_citation_only": "warn",
    "priority_citation_only": "warn", "csl_incomplete": "error", "source_undated": "warn",
    # Lizenzen und Nutzungsrechte
    "license_missing": "warn", "license_unknown": "info", "license_unclassified": "info",
    "fulltext_rights": "warn", "fulltext_shared": "warn",
    # Umfang, Sichtbarkeit und Fundweg
    "scope_missing": "warn", "visibility_leak": "warn", "visibility_values_missing": "error",
    "fundweg_missing": "info", "fundweg_incomplete": "warn", "fundweg_invalid": "error",
    # Schaubilder
    "figure_caption": "warn", "figure_missing": "error", "figure_rights": "warn",
    # wiki.toml
    "config_unknown": "warn", "rollen_unknown": "error", "design_unsafe": "error",
}

# Wie eine Quelle gefunden wurde; gemeinsames Vokabular aller Wikis.
FUNDWEG_ARTEN = {
    "suche": "Suche", "modellvorschlag": "Modellvorschlag", "material": "Material",
    "person": "Von der Person", "zitatverfolgung": "Zitatverfolgung",
}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

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


class WikiError(Exception):
    """Bedienfehler: fehlende Konfiguration, ungültiger Slug, vorhandene Datei …"""

    exit_code = 2


STEUERZEICHEN_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class _Druckbar:
    """Ausgabe ohne Steuerzeichen außer Tab und Zeilenumbruch: Titel und Lizenzen können aus einem fremden Wiki stammen."""

    def __init__(self, strom):
        self.strom = strom

    def write(self, text):
        return self.strom.write(STEUERZEICHEN_RE.sub(chr(0xFFFD), text))

    def __getattr__(self, name):
        return getattr(self.strom, name)


class ConfigMissing(WikiError):
    """Keine wiki.toml: Hooks bleiben in solchen Ordnern still."""


class RetractionFound(WikiError):
    """Rückzug oder Bedenken zu einer Quelle gemeldet: keine Karte (Exit 3), außer mit --trotz-rueckzug."""

    exit_code = 3


class FigureError(Exception):
    """Abbildung fehlt, oder die Bereinigung lehnt sie ab."""


# ----------------------------------------------------------- Schaubilder (SVG) und Design (CSS)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
                "title", "desc", "defs", "linearGradient", "radialGradient", "stop", "marker", "clipPath", "use"}
SVG_TEXT_ELEMENTS = {"text", "tspan", "title", "desc"}
# Geometrie und Präsentation; kein class, kein on*, keine Animation. href nur auf use und Verläufen, nur als #…
SVG_ATTRIBUTES = set("""
    x y width height cx cy r rx ry x1 y1 x2 y2 fx fy fr points d viewBox preserveAspectRatio transform id
    offset gradientUnits gradientTransform spreadMethod clipPathUnits markerWidth markerHeight markerUnits refX refY
    orient dx dy rotate textLength lengthAdjust
    fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
    stroke-dasharray stroke-dashoffset opacity color stop-color stop-opacity clip-path clip-rule marker-start
    marker-mid marker-end font-family font-size font-weight font-style font-variant text-anchor text-decoration
    dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing writing-mode visibility display
    style role aria-label aria-hidden href
""".split())
SVG_HREF_ELEMENTS = {"use", "linearGradient", "radialGradient"}
# style nur mit dem, was eine Abbildung zeichnet: nichts, was sie aus ihrem Rahmen hebt (position, z-index, inset, width …)
SVG_STYLE_PROPERTIES = set("""
    fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
    stroke-dasharray stroke-dashoffset paint-order vector-effect opacity color stop-color stop-opacity clip-path
    clip-rule marker-start marker-mid marker-end font font-family font-size font-weight font-style font-variant
    font-stretch text-anchor text-decoration dominant-baseline alignment-baseline baseline-shift letter-spacing
    word-spacing writing-mode text-rendering shape-rendering visibility
""".split())
SVG_ID_RE = re.compile(r"[A-Za-z_][\w.-]*")
SVG_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.I)
SVG_VIEWBOX_RE = re.compile(r"\s*-?[\d.]+(?:[\s,]+-?[\d.]+){3}\s*")
SVG_NUMBER_RE = re.compile(r"\s*(\d+(?:\.\d+)?)(?:px)?\s*")
# CSS-Funktionen, die Fremdes laden können, auch ohne url(
CSS_LOADER_RE = re.compile(r"image-set\(|(?<![\w-])(?:src|image|cross-fade|element|paint)\(", re.I)
# SVG kommt nur durch clean_svg auf die Seite, nicht als data: im Design
CSS_DATA_RE = re.compile(r"data:(?:font/(?:woff2?|ttf|otf|sfnt|collection)"
                         r"|application/(?:x-)?font-(?:woff2?|ttf|otf|sfnt)"
                         r"|image/(?:png|jpeg|gif|webp|avif))[;,]", re.I)


def svg_value_ok(value: str, style: bool = False) -> bool:
    """Ein Attributwert, der nichts nachlädt und nichts ausführt: url(…) nur auf #id, keine Skript-Adressen, keine
    CSS-Maskierung und kein CSS-Kommentar, die eine Adresse verstecken könnten."""
    lower = value.lower()
    if "\\" in value or "/*" in value or CSS_LOADER_RE.search(value):
        return False
    if any(not m.group(2).strip().startswith("#") for m in SVG_URL_RE.finditer(value)):
        return False
    if lower.count("url(") != len(SVG_URL_RE.findall(value)):
        return False
    if re.search(r"javascript:|(?<!\w)data:|vbscript:|expression\(|behavior\s*:|binding\s*:|[<>]", lower):
        return False
    return not (style and "@" in value)


def svg_style(value: str) -> tuple[str, list[str]]:
    """style auf die erlaubten Eigenschaften gekürzt: (neuer Wert, weggefallene Eigenschaften)."""
    keep, dropped = [], []
    for declaration in value.split(";"):
        name, colon, val = declaration.partition(":")
        name = name.strip().lower()
        if colon and name in SVG_STYLE_PROPERTIES:
            keep.append(f"{name}:{val.strip()}")
        elif declaration.strip():
            dropped.append(name)
    return ";".join(keep), dropped


def svg_prefixed(value: str, prefix: str) -> str:
    return SVG_URL_RE.sub(lambda m: f"url(#{prefix}{m.group(2).strip()[1:]})", value)


def svg_expansion_check(root) -> None:
    """Zählt die Elemente der Abbildung nach dem Auflösen aller <use> über den Referenzgraphen (je Teilbaum einmal
    berechnet, jedes <use> zählt den ganzen Zielbaum mit) und lehnt Zyklen sowie mehr als FIGURE_MAX_EXPANSION ab."""
    ids: dict[str, object] = {}
    for el in root.iter():
        if el.get("id") and el.get("id") not in ids:
            ids[el.get("id")] = el   # bei doppelter id gilt die erste, wie im Browser
    sizes: dict[int, int] = {}
    stack: set[int] = set()

    def size(el) -> int:
        key = id(el)
        if key in sizes:
            return sizes[key]
        if key in stack:
            raise FigureError("use verweist auf sich selbst (Zyklus)")
        stack.add(key)
        n = 1 + sum(size(child) for child in el)
        href = el.get("href") if el.tag.rpartition("}")[2] == "use" else None
        target = ids.get(href[1:]) if href and href.startswith("#") else None
        if target is not None:
            n += size(target)
        stack.discard(key)
        if n > FIGURE_MAX_EXPANSION:
            raise FigureError(f"mehr als {FIGURE_MAX_EXPANSION} Elemente nach dem Auflösen von use")
        sizes[key] = n
        return n

    try:
        size(root)
    except RecursionError as exc:
        raise FigureError("use zu tief verschachtelt") from exc


def clean_svg(source, prefix: str = "", label: str = "") -> tuple[str, list[str]]:
    """Bereinigtes SVG als Text für die Seite (Standardbibliothek, xml.etree): (SVG, was entfernt wurde).

    Abgelehnt (FigureError) wird, was sich nicht sicher lesen lässt: größer als 200 KB, anderes als UTF-8 (auch
    UTF-16 ohne BOM, NUL-Bytes), `<!DOCTYPE` oder `<!ENTITY`, kein gültiges XML, Wurzel nicht svg, zu viele Elemente
    (auch nach dem Auflösen von `<use>`, Zyklen eingeschlossen), kein viewBox. Alles andere wird bereinigt: Elemente und Attribute außerhalb der Allowlist samt Inhalt entfallen,
    fremde Namensräume ebenso, href nur auf #id, url(…) nur auf #id, style nur mit SVG_STYLE_PROPERTIES, transform
    nicht an der Wurzel (es höbe die Abbildung über die Seite). Mit prefix werden ids und ihre Verweise je Abbildung
    umbenannt, damit sich mehrere nicht stören.
    """
    import xml.etree.ElementTree as ET
    data = source if isinstance(source, bytes) else str(source).encode("utf-8")
    if len(data) > FIGURE_MAX_BYTES:
        raise FigureError(f"größer als {FIGURE_MAX_BYTES // 1024} KB")
    if b"\x00" in data:
        raise FigureError("nicht UTF-8 (NUL-Bytes)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FigureError("nicht UTF-8") from exc
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise FigureError("DOCTYPE und ENTITY sind nicht erlaubt")
    try:
        root = ET.fromstring(text)   # als Text: eine encoding-Angabe im Kopf kann die Prüfung oben nicht mehr umgehen
    except (ET.ParseError, ValueError) as exc:
        raise FigureError(f"kein gültiges XML ({exc})") from exc

    def local(tag) -> tuple[str, str]:
        if not isinstance(tag, str):   # Kommentar oder Verarbeitungsanweisung
            return "?", ""
        if tag.startswith("{"):
            ns, _, name = tag[1:].partition("}")
            return ns, name
        return "", tag

    root_ns, root_name = local(root.tag)
    if root_name != "svg" or root_ns not in ("", SVG_NS):
        raise FigureError("Wurzelelement ist kein svg")
    removed: set[str] = set()
    count = 0

    def add_text(el, text: str | None) -> None:
        if text:
            if len(el):
                el[-1].tail = (el[-1].tail or "") + text
            else:
                el.text = (el.text or "") + text

    def build(el, depth: int):
        nonlocal count
        count += 1
        if count > FIGURE_MAX_ELEMENTS:
            raise FigureError(f"mehr als {FIGURE_MAX_ELEMENTS} Elemente")
        if depth > 60:
            raise FigureError("zu tief verschachtelt")
        name = local(el.tag)[1]
        out = ET.Element(f"{{{SVG_NS}}}{name}")
        for key, value in el.attrib.items():
            kns, kname = local(key)
            if kns == XLINK_NS and kname == "href":
                kns = ""
            if kns or kname not in SVG_ATTRIBUTES or kname.lower().startswith("on") \
                    or (depth == 0 and kname == "transform"):
                removed.add(f"Attribut {kname}")
                continue
            if kname == "href" and not (name in SVG_HREF_ELEMENTS and value.strip().startswith("#")
                                        and SVG_ID_RE.fullmatch(value.strip()[1:])):
                removed.add("Attribut href")
                continue
            if kname == "id" and not SVG_ID_RE.fullmatch(value):
                removed.add("Attribut id")
                continue
            if kname != "id" and not svg_value_ok(value, style=kname == "style"):
                removed.add(f"Attribut {kname}")
                continue
            if kname == "style":
                value, dropped = svg_style(value)
                removed.update(f"Stil {d}" for d in dropped)
                if not value:
                    continue
            if kname == "id":
                value = prefix + value
            elif kname == "href":
                value = "#" + prefix + value.strip()[1:]
            else:
                value = svg_prefixed(value, prefix)
            out.set(kname, value)
        in_text = name in SVG_TEXT_ELEMENTS
        if in_text:
            out.text = el.text
        for child in el:
            cns, cname = local(child.tag)
            if cns == root_ns and cname in SVG_ELEMENTS:
                sub = build(child, depth + 1)
                if cname == "use" and sub.get("href") is None:
                    removed.add("Element use")
                    if in_text:
                        add_text(out, child.tail)
                    continue
                out.append(sub)
                sub.tail = child.tail if in_text else None
            else:
                removed.add(f"Element {cname or 'Kommentar'}" + ("" if cns == root_ns else " (fremder Namensraum)"))
                if in_text:
                    add_text(out, child.tail)
        return out

    clean = build(root, 0)
    svg_expansion_check(clean)
    if not clean.get("viewBox") or not SVG_VIEWBOX_RE.fullmatch(clean.get("viewBox", "")):
        w, h = (SVG_NUMBER_RE.fullmatch(clean.get(k, "")) for k in ("width", "height"))
        if not (w and h):
            raise FigureError("viewBox fehlt (oder width und height als Zahlen)")
        clean.set("viewBox", f"0 0 {w.group(1)} {h.group(1)}")
    clean.set("role", "img")
    if label:
        clean.set("aria-label", label)
    ET.register_namespace("", SVG_NS)
    return ET.tostring(clean, encoding="unicode"), sorted(removed)


def css_plain(css: str) -> str:
    """CSS, wie der Browser es liest (CSS Syntax 3): ohne Kommentare, Maskierungen aufgelöst. `/*` in einer Zeichenkette
    und in url(…) ohne Anführungszeichen beginnt keinen Kommentar; eine Zeichenkette endet spätestens am Zeilenende."""
    out: list[str] = []
    i, n = 0, len(css)

    def escaped(at: int) -> tuple[str, int]:
        hex_ = re.compile(r"([0-9a-fA-F]{1,6})(?:\r\n|\s)?").match(css, at + 1)
        if hex_:
            code = int(hex_.group(1), 16)
            return (chr(code) if 0 < code < 0x110000 and not 0xD800 <= code < 0xE000 else "\ufffd"), hex_.end()
        return css[at + 1:at + 2], at + 2

    while i < n:
        c = css[i]
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "\"'":
            out.append(c)
            i += 1
            while i < n and css[i] != c and css[i] not in "\n\r\f":
                if css[i] == "\\":
                    piece, i = escaped(i)
                    out.append(piece)
                else:
                    out.append(css[i])
                    i += 1
            out.append(c)
            i += i < n and css[i] == c
        elif c == "\\":
            piece, i = escaped(i)
            out.append(piece)
        else:
            out.append(c)
            i += 1
            if c == "(" and "".join(out[-4:]).lower() == "url(":
                while i < n and css[i].isspace():
                    i += 1
                if i < n and css[i] in "\"'":   # url("…") liest der Zweig für Zeichenketten
                    continue
                while i < n and css[i] != ")":
                    if css[i] == "\\":
                        piece, i = escaped(i)
                        out.append(piece)
                    else:
                        out.append(css[i])
                        i += 1
    return "".join(out)


def css_problem(css: str) -> str | None:
    """Warum ein Design (CSS) nicht eingebettet wird: `@import`, `url(` auf etwas anderes als `#…` oder `data:`
    für Schriften und Rasterbilder, `</style>`. Kommentare zählen nicht (außer für `</style`), Zeichenketten schon;
    CSS-Maskierungen werden vorher aufgelöst (css_plain)."""
    if re.search(r"</\s*style", css, re.I):
        return "enthält </style"
    text = css_plain(css)
    if re.search(r"@\s*import", text, re.I):
        return "@import lädt Fremdes nach"
    if CSS_LOADER_RE.search(text) or re.search(r"expression\(|-moz-binding|behavior\s*:", text, re.I):
        return "image-set, src, image, expression, binding und behavior sind nicht erlaubt"
    urls = list(re.finditer(r"url\(\s*([\"']?)(.*?)\1\s*\)", text, re.I | re.S))
    if len(urls) != len(re.findall(r"url\(", text, re.I)):
        return "url( ohne Ende"
    for m in urls:
        target = m.group(2).strip()
        if target.startswith("#") or CSS_DATA_RE.match(target):
            continue
        return f"url({target[:40]}) lädt Fremdes nach oder ist kein erlaubtes data: (erlaubt: #…, data: für Schriften und Rasterbilder)"
    return None


NOTICE_RUECKZUG_RE = re.compile(r"retraction|withdrawal|removal|retracted", re.I)
NOTICE_KORREKTUR_RE = re.compile(r"correction|erratum|corrigendum|republished", re.I)


def notice_kind(art: str) -> str:
    """Art eines Hinweises (Crossref-Typ, PubMed-RefType oder -Publikationstyp): rueckzug, bedenken, korrektur oder ""."""
    if NOTICE_RUECKZUG_RE.search(art):
        return "rueckzug"
    if re.search(r"concern", art, re.I):
        return "bedenken"
    return "korrektur" if NOTICE_KORREKTUR_RE.search(art) else ""


def notice_text(h: dict) -> str:
    """Ein Hinweis für die Meldung: Art, Dienst und Hinweis-DOI (bei update-to: die Arbeit, auf die er sich bezieht)."""
    if h["quelle"].endswith("update-to"):
        return f"{h['art']} laut {h['quelle']}: diese DOI ist selbst ein Hinweis zu {h['doi'] or '?'}"
    return (f"{h['art']} laut {h['quelle']}" + (f", Hinweis-DOI {h['doi']}" if h.get("doi") else "")
            + (f", Hinweis-PMID {h['pmid']}" if h.get("pmid") else ""))


def crossref_notices(m: dict) -> list[dict]:
    """Hinweise der Crossref-Antwort `message`: `updated-by` (Hinweise auf diese Arbeit) und `update-to` (diese Arbeit
    ist selbst ein Hinweis auf eine andere)."""
    out = []
    for key in ("updated-by", "update-to"):
        for u in m.get(key) or []:
            if isinstance(u, dict) and u.get("type"):
                out.append({"art": str(u["type"]), "doi": str(u.get("DOI") or ""), "quelle": f"crossref {key}"})
    return out


def pubmed_notices(xml: bytes) -> list[dict]:
    """Hinweise aus dem efetch-XML eines PubMed-Eintrags: Publikationstyp „Retracted Publication“ und die Verweise
    RetractionIn, ExpressionOfConcernIn, ErratumIn, CorrectedandRepublishedIn."""
    import xml.etree.ElementTree as ET
    art = ET.fromstring(xml).find(".//PubmedArticle")
    if art is None:
        raise ValueError("PMID nicht gefunden")
    out = []
    if any((p.text or "") == "Retracted Publication" for p in art.findall(".//PublicationType")):
        out.append({"art": "Retracted Publication", "doi": "", "quelle": "pubmed"})
    for c in art.findall(".//CommentsCorrections"):
        typ = c.get("RefType", "")
        if typ in ("RetractionIn", "ExpressionOfConcernIn", "ErratumIn", "CorrectedandRepublishedIn"):
            source = c.findtext("RefSource") or ""
            doi = re.search(r"10\.\d{4,9}/[^\s]+", source)
            out.append({"art": typ, "doi": doi.group(0).rstrip(".,;") if doi else "", "pmid": c.findtext("PMID") or "",
                        "quelle": "pubmed"})
    return out


# ---------------------------------------------------------------- Grundlagen


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


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_months(moment: dt.datetime, months: int) -> dt.datetime:
    year, month = divmod(moment.month - 1 + months, 12)
    year, month = moment.year + year, month + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_lesen(pfad: Path) -> dict | None:
    """Eine JSON-Datei als Zuordnung, sonst None (OSError, ungültiges JSON, keine Zuordnung)."""
    try:
        data = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def replace_file(path: Path, text: str) -> None:
    """Schreibt über eine Zwischendatei im selben Ordner: Ein Abbruch hinterlässt die alte Fassung, und ein Symlink am
    Ziel wird ersetzt, nicht durchschrieben."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        regular = path.is_file() and not path.is_symlink()
        os.chmod(tmp, path.stat().st_mode & 0o777 if regular else 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# Schemata, die ein Link im Dashboard tragen darf
LINK_SCHEMES = ("http", "https", "mailto")


def link_scheme(href) -> str | None:
    """Schema eines Links, wie der Browser es liest (Zeichenreferenzen aufgelöst, Tab und Zeilenumbruch entfernt,
    Steuerzeichen und Leerzeichen am Anfang übergangen), klein geschrieben; None bei relativen Adressen."""
    text = re.sub(r"[\t\n\r]", "", html.unescape(str(href or ""))).lstrip("".join(map(chr, range(0x21))))
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", text)
    return m.group(1).lower() if m else None


def safe_link(href) -> str:
    """Die Adresse, wenn sie absolut ist und ein Schema aus LINK_SCHEMES trägt, sonst ""."""
    return str(href) if link_scheme(href) in LINK_SCHEMES else ""


class _EntryCleaner(HTMLParser):
    """Literaturangabe als HTML: nur Auszeichnung, class und Links aus LINK_SCHEMES; alles andere wird zu Text."""

    TAGS = {"a", "b", "code", "div", "em", "i", "small", "span", "strong", "sub", "sup"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.TAGS:
            return
        keep = [(k, v) for k, v in attrs if v and (k == "class" and re.fullmatch(r"[\w -]+", v)
                                                   or k == "href" and tag == "a" and safe_link(v))]
        self.out.append(f"<{tag}" + "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in keep) + ">")

    def handle_endtag(self, tag):
        if tag in self.TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))


def clean_entry(fragment: str) -> str:
    """Eine Literaturangabe von pandoc so kürzen, dass sie gefahrlos als HTML in die Seite darf."""
    cleaner = _EntryCleaner()
    cleaner.feed(fragment)
    cleaner.close()
    return "".join(cleaner.out)


def body_sha256(body: str) -> str:
    """Prüfwert des Markdown-Körpers ohne Frontmatter: Zeilenenden als LF, Leerzeilen an Anfang und Ende entfernt."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def one_line(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def is_timestamp(value) -> bool:
    return isinstance(value, str) and bool(ISO_TS_RE.match(value))


def as_datetime(value) -> dt.datetime | None:
    if is_timestamp(value):
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def split_frontmatter(text: str) -> tuple[str | None, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def set_frontmatter_key(raw: str, key: str, value) -> str:
    """Ersetzt einen Top-Level-Schlüssel im Frontmatter-Text, auch in Anführungszeichen; alles andere bleibt byte-genau."""
    block = dump_key(key, value)
    lines = raw.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"""(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*:""", ln)), None)
    if start is None:
        return (raw if not raw or raw.endswith("\n") else raw + "\n") + block
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t", "- ")) or lines[end].strip() in ("", "-")):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[:start]) + block + "".join(lines[end:])


def kurzbeleg(csl: dict, narrative: bool = False) -> str:
    """APA-7-Kurzbeleg aus CSL: 'Morath et al., 2024' oder erzählend 'Morath et al. (2024)'."""
    people = csl.get("author") or csl.get("editor") or []
    names = [a.get("family") or a.get("literal", "?") for a in as_list(people) if isinstance(a, dict)] or ["o. A."]
    joiner = "und" if narrative else "&"
    if len(names) == 1:
        who = names[0]
    elif len(names) == 2:
        who = f"{names[0]} {joiner} {names[1]}"
    else:
        who = f"{names[0]} et al."
    year = csl_year(csl)
    return f"{who} ({year})" if narrative else f"{who}, {year}"


def csl_year(csl: dict) -> str:
    """Erstes Datumsteil aus csl.issued als Text, „o. D.“, wenn es fehlt oder sich nicht lesen lässt. Kann beliebiger
    Text sein: vor der Ausgabe als HTML maskieren."""
    issued = csl.get("issued")
    try:
        year = issued["date-parts"][0][0]
    except (TypeError, KeyError, IndexError):
        return "o. D."
    return str(year) if year not in (None, "") else "o. D."


INITIALS_RE = re.compile(r"(?:[A-ZÄÖÜ][a-zäöü]?\.?[\s-]*)+")
LOCATOR_RE = re.compile(
    r"(?:S\.|Seite|Tab\.|Abb\.|Abschn\.|Kap\.|Art\.|Abs\.|§|Folie|Zeilen|Zeile|Z\.|Rn\.|Anhang|Erwägungsgrund)\s*[\dIVXLC][\w.–-]*"
    r"(?:(?:,\s*|\s+)(?:(?:S\.|Seite|Tab\.|Abb\.|Abschn\.|Kap\.|Art\.|Abs\.|§|Nr\.|Anhang|und|u\.)\s*)?\d[\w.–-]*)*")


def csl_names(raw) -> list[dict]:
    """Autorenliste aus Strings: "Nachname, V." oder abwechselnd "Nachname", "V. M."."""
    items = [one_line(a).strip(",; ") for a in as_list(raw) if one_line(a).strip(",; ")]
    out, i = [], 0
    while i < len(items):
        name = items[i]
        if "," in name:
            family, given = name.split(",", 1)
            out.append({"family": family.strip(), "given": given.strip()})
            i += 1
        elif i + 1 < len(items) and INITIALS_RE.fullmatch(items[i + 1]):
            out.append({"family": name, "given": items[i + 1]})
            i += 2
        else:
            out.append({"literal": name})
            i += 1
    return out


def csl_date(value) -> dict | None:
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(value or ""))
    if not m:
        return None
    return {"date-parts": [[int(x) for x in m.groups() if x]]}


LICENSE_HINT = (" (unknown ist gültig; `lizenz <seite> --fetch` sucht über Crossref, DataCite, Europe PMC und "
                "OpenAlex)")
UNKNOWN_LICENSE_RE = re.compile(r"^(unknown|unbekannt|unklar|n/?a|none|keine angabe|-+)?$", re.I)
# bewusst undatierte Quelle (date: o. J.): keine Warnung source_undated
UNDATED_RE = re.compile(r"^\s*(o\.\s*j\.?|n\.\s*d\.?|undatiert|ohne datum)\s*$", re.I)


def claim_lines(text: str) -> list[str]:
    """Nicht leere Zeilen eines Abschnitts außer Hinweiskästen (`> [!todo] …` samt `>`-Folgezeilen bis zur Leerzeile)
    und Fußnotendefinitionen samt eingerückter Folgezeilen (die stehen am Seitenende, auch wenn der Abschnitt der
    letzte ist)."""
    out, box, note = [], False, False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            box = False
        elif note and line[0] in " \t":
            continue
        elif re.match(r">\s*\[!", stripped):
            box, note = True, False
        elif FOOTNOTE_DEF_RE.match(stripped):
            box, note = False, True
        elif not (box and stripped.startswith(">")):
            box, note = False, False
            out.append(stripped)
    return out


def kennungen(doi="", pmid="", arxiv="", url="") -> dict[str, str]:
    """Vergleichbare Kennungen einer Arbeit: DOI klein ohne Vorsatz, PMID, arXiv-ID ohne Fassung; was fehlt, aus url."""
    url = str(url or "")
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", str(doi or "").strip(), flags=re.I).lower()
    if not doi and (m := re.search(r"doi\.org/(10\.\S+)", url, re.I)):
        doi = m.group(1).lower()
    pmid = str(pmid or "").strip().rstrip("/").rsplit("/", 1)[-1]
    if not pmid and (m := re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)):
        pmid = m.group(1)
    arxiv = re.sub(r"^arxiv:", "", str(arxiv or "").strip(), flags=re.I).lower()
    if not arxiv and doi.startswith("10.48550/arxiv."):
        arxiv = doi.removeprefix("10.48550/arxiv.")
    if not arxiv and (m := re.search(r"arxiv\.org/abs/([^\s?#]+)", url, re.I)):
        arxiv = m.group(1).lower()
    arxiv = re.sub(r"v\d+$", "", arxiv)
    return {k: v for k, v in (("doi", doi), ("pmid", pmid), ("arxiv", arxiv)) if v}


def kandidat_abgleich(kandidat: dict, ziel: dict[str, str], name: str) -> str:
    """Ist der Kandidat eines Laufs dieselbe Arbeit wie `ziel` (kennungen)? WikiError, wenn sich alle gemeinsamen
    Kennungen unterscheiden; ein Hinweistext, wenn es keine gemeinsame gibt; sonst ""."""
    eigen = kennungen(kandidat.get("doi"), kandidat.get("pmid"), kandidat.get("arxiv"))
    gemeinsam = [k for k in eigen if k in ziel]
    if not gemeinsam:
        return f"{name} und Kandidat {kandidat.get('nr')} haben keine vergleichbare Kennung: Zuordnung nicht geprüft"
    if any(eigen[k] == ziel[k] for k in gemeinsam):
        return ""
    raise WikiError(f"Kandidat {kandidat.get('nr')} ist eine andere Arbeit als {name}: "
                    + ", ".join(f"{k} {eigen[k]} ≠ {ziel[k]}" for k in gemeinsam) + "; nichts übernommen")


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


def http_lesen(req: urllib.request.Request, timeout: int) -> bytes:
    """Liest die Antwort; eine abgebrochene Verbindung wird wiederholt und zuletzt zum Netzfehler wie jeder andere."""
    for versuch in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except http.client.HTTPException as exc:
            if versuch == 2:
                raise urllib.error.URLError(f"Antwort abgebrochen: {exc}") from exc
            time.sleep(1)


def http_json(url: str, timeout: int = 20, accept: str = "application/json"):
    """GET auf eine offizielle API (Crossref, Europe PMC, DataCite, OpenAlex); ohne Kennungen der Person im Kopf."""
    req = urllib.request.Request(url, headers={"User-Agent": f"okfw/{VERSION}", "Accept": accept})
    return json.loads(http_lesen(req, timeout).decode("utf-8"))


def http_bytes(url: str, timeout: int = 30) -> bytes:
    return http_lesen(urllib.request.Request(url, headers={"User-Agent": f"okfw/{VERSION}"}), timeout)


def today_parts() -> dict:
    return {"date-parts": [[int(x) for x in dt.date.today().isoformat().split("-")]]}


def crossref_csl(m: dict) -> dict:
    """CSL-JSON ohne id aus der Crossref-Antwort `message`."""
    def year(key):
        return ((m.get(key) or {}).get("date-parts") or [[None]])[0][0]
    y = year("published-print") or year("issued")
    csl = {
        "type": CROSSREF_TYPES.get(m.get("type"), m.get("type") or "article-journal"),
        "title": re.sub(r"<[^>]+>", "", (m.get("title") or [""])[0]),
        "author": [{k: a[k] for k in ("family", "given") if k in a} or {"literal": a.get("name", "")}
                   for a in m.get("author", [])],
        "issued": {"date-parts": [[y]]} if y else None,
        "container-title": (m.get("container-title") or [""])[0],
        "volume": m.get("volume"), "issue": m.get("issue"), "page": m.get("page"), "DOI": m.get("DOI"),
    }
    return {k: v for k, v in csl.items() if v}


DATACITE_CSL_FIELDS = ("type", "title", "author", "issued", "container-title", "publisher", "volume", "issue",
                       "page", "number", "version", "URL")


def datacite_csl(doi: str) -> dict:
    """CSL-JSON ohne id von DataCite (Zenodo, arXiv, OSF, Datensätze) für DOIs, die Crossref nicht kennt.

    DataCite liefert CSL per Inhaltsaushandlung; übernommen werden nur bibliographische Felder, kein Abstract.
    """
    raw = http_json(f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}",
                    accept="application/vnd.citationstyles.csl+json")
    csl = {k: raw[k] for k in DATACITE_CSL_FIELDS if raw.get(k)}
    csl["DOI"] = doi
    return csl


def datacite_rights(doi: str) -> list[dict]:
    """rightsList des DataCite-Eintrags ([{rights, rightsUri, …}]); leer, wenn DataCite die DOI nicht kennt."""
    try:
        return http_json(f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}")["data"]["attributes"].get(
            "rightsList") or []
    except (OSError, ValueError, KeyError):
        return []


def pubmed_csl(pmid: str) -> dict:
    """CSL aus PubMed-esummary, für Artikel mit lückenhaftem Crossref-Eintrag oder ohne DOI."""
    q = urllib.parse.urlencode({"db": "pubmed", "id": pmid, "retmode": "json"})
    r = http_json(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{q}")["result"][pmid]
    authors = []
    for a in r.get("authors", []):
        if a.get("authtype") != "Author":
            continue
        family, _, initials = a["name"].rpartition(" ")
        authors.append({"family": family or initials, "given": ". ".join(initials) + "." if family else ""})
    csl = {"type": "article-journal", "title": r["title"].rstrip("."), "author": authors,
           "issued": {"date-parts": [[int(r["pubdate"][:4])]]}, "container-title": r.get("fulljournalname"),
           "volume": r.get("volume"), "issue": r.get("issue"), "page": r.get("pages"), "PMID": pmid}
    doi = next((x["value"] for x in r.get("articleids", []) if x["idtype"] == "doi"), None)
    if doi:
        csl["DOI"] = doi
    else:
        csl["URL"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        csl["accessed"] = today_parts()
    return {k: v for k, v in csl.items() if v}


def arxiv_csl(aid: str) -> dict:
    import xml.etree.ElementTree as ET
    q = urllib.parse.urlencode({"id_list": aid, "max_results": 1})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = ET.fromstring(http_bytes(f"https://export.arxiv.org/api/query?{q}")).find("a:entry", ns)
    if e is None or not e.findtext("a:title", "", ns).strip():
        raise WikiError(f"arXiv:{aid} nicht gefunden")
    authors = []
    for a in e.findall("a:author", ns):
        parts = a.findtext("a:name", "", ns).rsplit(" ", 1)
        authors.append({"family": parts[-1], "given": parts[0]} if len(parts) == 2 else {"literal": parts[0]})
    bare = aid.split("v")[0]
    return {"type": "article", "title": " ".join(e.findtext("a:title", "", ns).split()), "author": authors,
            "issued": {"date-parts": [[int(e.findtext("a:published", "", ns)[:4])]]},
            "publisher": "arXiv", "number": f"arXiv:{bare}", "DOI": f"10.48550/arXiv.{bare}"}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def capture_file(item, source_path: str | None = None) -> tuple[Path, str | None]:
    """(lokaler Pfad, Pfad im Quell-Repository) aus --file PFAD oder PFAD=REPO-PFAD; ohne REPO-PFAD gilt --source-path."""
    if isinstance(item, (tuple, list)):
        local, repo = item
    else:
        local, repo = str(item), None
        if "=" in local and not Path(local).expanduser().exists():
            local, _, repo = local.partition("=")
    return Path(local).expanduser(), repo or source_path


def known_config_keys() -> set[str]:
    """Schlüssel der wiki.toml, die der Kern kennt: „abschnitt.schlüssel“, „export.<export>.<schlüssel>“,
    „types.*.<schlüssel>“; Tabellen mit Projektwerten als Schlüsseln (FREE_TABLES) nur mit ihrem Namen."""
    keys = {f"export.{name}.{k}" for name, ks in EXPORT_KEYS.items() for k in ks}
    keys |= {f"types.*.{k}" for k in TYPE_DEFAULTS}
    for sec, spec in DEFAULT_CONFIG.items():
        if sec not in ("export", "types"):
            keys |= {f"{sec}.{k}" for k in spec} if spec and sec not in FREE_TABLES else {sec}
    return keys


def unknown_config(cfg: dict) -> list[tuple[str, str]]:
    """(Name, Vorschlag) je Schlüssel der wiki.toml, den der Kern nicht kennt, und je unbekanntem Code in
    [lint.severity]; Vorschlag ist der ähnlichste bekannte Name oder "". Tabellen aus FREE_TABLES bleiben ungeprüft."""
    out = []

    def check(name: str, key: str, choices) -> None:
        if key not in choices:
            close = difflib.get_close_matches(key, list(choices), n=1, cutoff=0.75)
            out.append((name, close[0] if close else ""))

    for sec, spec in cfg.items():
        if sec in FREE_TABLES:
            continue
        if sec not in DEFAULT_CONFIG:
            check(f"[{sec}]", sec, DEFAULT_CONFIG)
        elif sec == "export":
            for name, table in spec.items():
                check(f"[export.{name}]", name, EXPORT_KEYS)
                if name in EXPORT_KEYS and isinstance(table, dict):
                    for key in table:
                        check(f"[export.{name}] {key}", key, EXPORT_KEYS[name])
        elif sec == "types":
            for name, table in spec.items():
                for key in table if isinstance(table, dict) else ():
                    check(f"[types.{name}] {key}", key, TYPE_DEFAULTS)
        elif sec == "rollen":
            # Tabellen unter [rollen] sind Rollen ([rollen.lesen] …), deren Felder rollen.py prüft; geprüft wird der Rest
            for key, value in spec.items():
                if not isinstance(value, dict):
                    check(f"[rollen] {key}", key, DEFAULT_CONFIG["rollen"])
        else:
            for key in spec:
                check(f"[{sec}] {key}", key, DEFAULT_CONFIG[sec])
            if sec == "lint":
                for code in spec.get("severity") or {}:
                    check(f"[lint.severity] {code}", code, SEVERITY)
    return out


def find_config(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        candidate = directory / "wiki.toml"
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Page:
    path: Path
    id: str
    fm: dict | None
    body: str
    raw_fm: str | None
    error: str | None = None

    @property
    def type(self) -> str:
        return one_line((self.fm or {}).get("type"))

    @property
    def title(self) -> str:
        return one_line((self.fm or {}).get("title")) or self.path.stem.replace("-", " ")

    @property
    def description(self) -> str:
        return one_line((self.fm or {}).get("description"))

    @property
    def folder(self) -> str:
        return self.id.split("/", 1)[0] if "/" in self.id else ""


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "path": self.path, "message": self.message}


# ---------------------------------------------------------------------- Wiki


class Wiki:
    def __init__(self, root: Path, cfg: dict, config_path: Path | None = None):
        no_table = [f"[{sec}]" for sec in DEFAULT_CONFIG if not isinstance(cfg.get(sec), dict)]
        if no_table:
            raise WikiError(f"{', '.join(no_table)} muss eine Tabelle sein")
        self.root = root.resolve()
        self.cfg = cfg
        self.config_path = config_path
        self._pruefe_schreibpfade()
        self.bundle = self.root / cfg["wiki"]["bundle"]
        events = cfg["paths"].get("events") or f"{cfg['wiki']['bundle']}/.events.jsonl"
        self.events_path = self.root / events
        self.raw = self.root / cfg["paths"]["raw"] if cfg["paths"].get("raw") else None
        self.actor = os.environ.get("WIKI_ACTOR") or cfg["wiki"]["actor"]
        self.types: dict[str, dict] = {}
        for name, spec in cfg["types"].items():
            merged = {**TYPE_DEFAULTS, **({"min_links": 0} if name == SOURCE_TYPE else {}), **spec}
            merged["folder"] = merged["folder"] or name
            merged["label"] = merged["label"] or merged["folder"]
            self.types[name] = merged
        self.folder_type = {spec["folder"]: name for name, spec in self.types.items()}
        self._pages: list[Page] | None = None
        self.crossref_seen: dict[str, list[dict]] = {}   # DOI → Hinweise aus der Antwort von fetch_source
        self.figure_count = 0             # Dashboard: Abbildungen dieses Baus, für eindeutige ids
        self.bad_links: list[Path] = []   # pages(): Symlinks, die nicht auf eine Datei im Bundle zeigen
        self.public = False               # public_view(): öffentliche Ausgabe ohne verborgene Seiten
        self.withheld: set[Path] = set()  # public_view(): Pfade der weggelassenen Seiten
        required = cfg["wiki"].get("requires_core")
        if required:
            if not re.fullmatch(r"\d+\.\d+(\.\d+)?", str(required)):
                raise WikiError(f"[wiki] requires_core {required!r} ist keine Version MAJOR.MINOR, z. B. \"0.3\"")
            if tuple(map(int, VERSION.split(".")[:2])) < tuple(map(int, str(required).split(".")[:2])):
                raise WikiError(f"wiki.toml verlangt okfw {required}, installiert ist {VERSION}")
        wrong = {c: v for c, v in cfg["lint"]["severity"].items() if v not in (*SEVERITY_ORDER, "off")}
        if wrong:
            raise WikiError("[lint.severity] erlaubt error, warn, info oder off: "
                            + ", ".join(f"{c} = {v!r}" for c, v in wrong.items()))

    def _pruefe_schreibpfade(self) -> None:
        """Jeder Pfad der wiki.toml, in den der Kern schreibt, muss aufgelöst (auch über Symlinks) im Projektordner
        liegen: Ein geklontes Wiki darf über `..`, einen absoluten Pfad oder einen Symlink im Pfad keine Datei außerhalb
        überschreiben."""
        cfg = self.cfg
        pfade = [("[wiki] bundle", cfg["wiki"].get("bundle")), ("[paths] events", cfg["paths"].get("events")),
                 ("[paths] raw", cfg["paths"].get("raw")), ("[dashboard] path", cfg["dashboard"].get("path")),
                 ("[dashboard] public_path", cfg["dashboard"].get("public_path"))]
        pfade += [(f"[export.{name}] path", spec.get("path")) for name, spec in cfg["export"].items()
                  if isinstance(spec, dict)]
        for name, wert in pfade:
            if wert in (None, ""):
                continue
            if not isinstance(wert, str):
                raise WikiError(f"{name} muss ein Pfad (Text) sein, nicht {wert!r}")
            pfad = self.root / wert
            # ein Symlink als letztes Glied wird beim Schreiben ersetzt (replace_file), nicht verfolgt
            aufgeloest = pfad.parent.resolve() / pfad.name if pfad.is_symlink() else pfad.resolve()
            if not aufgeloest.is_relative_to(self.root):
                raise WikiError(f"{name} = {wert!r} liegt außerhalb des Projektordners {self.root}: "
                                "Pfade der wiki.toml müssen im Projektordner bleiben (auch über Symlinks)")
            if {".git", ".claude"} & set(aufgeloest.relative_to(self.root).parts):
                raise WikiError(f"{name} = {wert!r} liegt in .git oder .claude: dorthin schreibt der Kern nicht")

    @classmethod
    def load(cls, config: str | Path | None = None, root: str | Path | None = None,
             start: str | Path | None = None) -> "Wiki":
        if config:
            config_path = Path(config).expanduser().resolve()
        elif root:
            config_path = Path(root).expanduser().resolve() / "wiki.toml"
        else:
            config_path = find_config(Path(start or os.getcwd()).resolve())
        if not config_path or not config_path.is_file():
            raise ConfigMissing("keine wiki.toml gefunden (Projektordner mit --root oder Datei mit --config angeben)")
        if config and root and Path(root).expanduser().resolve() != config_path.parent:
            # Projektordner ist der Ordner der wiki.toml: alle Pfade darin gelten relativ zu ihm, auch im Guard
            raise WikiError(f"--root {root} ist nicht der Ordner von --config {config}: Projektordner ist der Ordner "
                            "der wiki.toml; nur --root DIR (nimmt DIR/wiki.toml) oder nur --config angeben")
        try:
            with config_path.open("rb") as f:
                cfg = deep_merge(DEFAULT_CONFIG, tomllib.load(f))
        except tomllib.TOMLDecodeError as exc:
            raise WikiError(f"{config_path.name} nicht lesbar: {exc}") from exc
        return cls(config_path.parent, cfg, config_path)

    # ------------------------------------------------------------ Lesen

    def rel(self, path) -> str:
        if not isinstance(path, Path):
            return str(path)
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def page_id(self, path: Path) -> str:
        """Konzept-ID nach OKF §2: Pfad im Bundle ohne .md."""
        return path.relative_to(self.bundle).with_suffix("").as_posix()

    def _hidden(self, path: Path) -> bool:
        """Versteckte Ordner und Dateien (Punkt) und der Ablageort der Schaubilder zählen nicht als Seiten."""
        parts = path.relative_to(self.bundle).parts
        return any(part.startswith(".") for part in parts) or parts[:1] == (FIGURES_DIR,) and not self._figures_typed()

    def _figures_typed(self) -> bool:
        """Ein Typ nutzt den Ordner der Schaubilder (Wikis von vor 0.5): .md-Seiten darin bleiben normale Seiten."""
        return any(f"{spec['folder']}/".startswith(f"{FIGURES_DIR}/") for spec in self.types.values())

    def _in_bundle(self, path: Path) -> bool:
        """Liegt der Pfad nach Auflösen aller Symlinks im Bundle?"""
        return path.resolve().is_relative_to(self.bundle.resolve())

    def _im_projekt(self, rel) -> Path | None:
        """Pfad einer Karte oder eines Manifests im Projektordner, sonst None: Sie stammen womöglich aus einem fremden Wiki."""
        ziel = self.root / str(rel)
        return ziel if ziel.resolve().is_relative_to(self.root.resolve()) else None

    def parse(self, path: Path) -> Page:
        pid = self.page_id(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return Page(path, pid, None, "", None, "Datei ist nicht UTF-8")
        raw, body = split_frontmatter(text)
        if raw is None:
            return Page(path, pid, None, text, None, "kein Frontmatter-Block")
        if len(raw.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
            return Page(path, pid, None, body, raw, "Frontmatter größer als 64 KB")
        try:
            fm = load_yaml(raw)
        except yaml.YAMLError as exc:
            return Page(path, pid, None, body, raw, f"YAML nicht lesbar: {exc}".splitlines()[0])
        if fm is None:
            fm = {}
        if not isinstance(fm, dict):
            return Page(path, pid, None, body, raw, "Frontmatter ist keine Zuordnung")
        return Page(path, pid, fm, body, raw)

    def pages(self) -> list[Page]:
        if self._pages is None:
            found, self.bad_links = [], []
            if self.bundle.is_dir():
                for path in sorted(self.bundle.rglob("*.md")):
                    if path.name in RESERVED or self._hidden(path) or path.is_dir():
                        continue
                    if self._in_bundle(path) and path.is_file():
                        found.append(self.parse(path))
                    else:
                        self.bad_links.append(path)
            self._pages = found
        return self._pages

    def resolve(self, src: Path, target: str) -> Path | None:
        """Interner Linkziel-Pfad oder None für externe Links, Anker und Beschreibungen."""
        target = str(target).strip()
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#") or " " in target:
            return None
        target = target.split("#", 1)[0]
        if not target:
            return None
        base = self.bundle if target.startswith("/") else src.parent
        resolved = (base / target.lstrip("/")).resolve()
        if target.endswith("/"):
            resolved = resolved / "index.md"
        return resolved

    def read_events(self) -> list[dict]:
        """Ereignisse nach Zeit, jede Zeile einmal. Nach einem Merge paralleler Zweige (merge=union) stehen sie in
        der Datei nicht mehr nach Zeit, und eine Zeile kann doppelt kommen. Gleichstand: Reihenfolge der Datei."""
        if not self.events_path.exists():
            return []
        events = []
        for line in dict.fromkeys(self.events_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                continue
            if isinstance(event, dict):
                events.append(event)
        undated = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        return sorted(events, key=lambda e: as_datetime(e.get("ts")) or undated)

    # ------------------------------------------------------------ Schreiben

    def append_event(self, kind: str, page: str, detail: str = "", by: str | None = None) -> dict:
        if self.events_path.is_symlink() and not self.events_path.resolve().is_relative_to(self.root):
            raise WikiError(f"{self.events_path} ist ein Symlink aus dem Projekt heraus: kein Ereignis geschrieben")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": now_iso(), "kind": kind, "page": page, "by": by or self.actor, "detail": one_line(detail)}
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def page_path(self, page: str) -> Path:
        page = page.removeprefix("/").removesuffix(".md")
        path = self.bundle / f"{page}.md"
        if not path.exists():
            raise WikiError(f"Seite fehlt: {self.rel(path)}")
        if not self._in_bundle(path):
            raise WikiError(f"{path} liegt außerhalb des Bundles (Symlink?): nicht gelesen, nicht geschrieben")
        return path

    def _kein_symlink(self, path: Path) -> None:
        """Eine Karte als Symlink auf eine andere: Vermerk und Text landeten sonst in der anderen."""
        if path.is_symlink():
            raise WikiError(f"{path.name} ist ein symbolischer Link; nicht geschrieben. Bitte durch eine Datei ersetzen.")

    def _write_frontmatter(self, path: Path, updates: dict) -> None:
        """Setzt Top-Level-Schlüssel chirurgisch und prüft das Ergebnis vor dem Schreiben."""
        self._kein_symlink(path)
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            raise WikiError(f"{self.rel(path)}: kein Frontmatter")
        raw = m.group(1)
        for key, value in updates.items():
            raw = set_frontmatter_key(raw, key, value)
        try:
            check = load_yaml(raw)
        except yaml.YAMLError:
            check = None
        if not isinstance(check, dict) or any(check.get(k) != v for k, v in updates.items()):
            raise WikiError(f"{self.rel(path)}: Frontmatter ließ sich nicht sicher ändern, bitte von Hand")
        path.write_text(text[:m.start(1)] + raw + text[m.end(1):], encoding="utf-8")
        self._pages = None

    def verify(self, page: str, by: str, status: str | None = None, note: str = "", force: bool = False) -> Path:
        return self.verify_pages([page], by, status=status, note=note, force=force)[0]

    def verify_pages(self, pages, by: str, status: str | None = None, note: str = "",
                     force: bool = False) -> list[Path]:
        """Prüfvermerk mit Prüfwert für mehrere Seiten (eine Charge), ein Ereignis je Seite.

        Erst werden alle Seiten geprüft, dann geschrieben: Scheitert eine, ändert sich keine. Eine Freigabe
        (human:, --status stable) verlangt je Seite eine Gegenprüfung durch einen anderen Akteur mit Prüfwert
        zum jetzigen Inhalt; force schreibt trotzdem und vermerkt das im Ereignis.
        """
        if not ACTOR_RE.match(by):
            raise WikiError("--by muss der Akteurskonvention folgen, z. B. human:<id> (OKF §7)")
        if status == "stable" and not by.startswith("human:"):
            raise WikiError("status stable nur mit --by human:<id>, also nach Prüfung durch einen Menschen; "
                            "ohne --status hält der Vermerk die Prüfung fest, ohne den Status zu heben")
        targets = []
        for page in dict.fromkeys(pages):
            path = self.page_path(page)
            parsed = self.parse(path)
            if parsed.error:
                raise WikiError(f"{parsed.id}: Frontmatter nicht lesbar: {parsed.error}")
            targets.append((path, parsed))
        release = status == "stable" and by.startswith("human:")
        unchecked = [p.id for _, p in targets if release and self.release_state(p)["check"] is None]
        if unchecked and not force:
            raise WikiError("keine Gegenprüfung zum jetzigen Inhalt (verify durch Claude oder einen Prozess) für "
                            + ", ".join(unchecked) + "; erst gegenprüfen lassen, oder mit --force trotzdem freigeben")
        for path, parsed in targets:
            verified = as_list(parsed.fm.get("verified")) + [{"by": by, "at": now_iso(),
                                                               VERIFIED_HASH: body_sha256(parsed.body)}]
            updates = {"verified": verified}
            if status:
                updates["status"] = status
            self._write_frontmatter(path, updates)
            detail = note + (" (freigegeben ohne Gegenprüfung, --force)" if parsed.id in unchecked else "")
            self.append_event("verify", parsed.id, detail.strip(), by=by)
        return [path for path, _ in targets]

    def generated_now(self, fm: dict | None) -> dict:
        """generated {by, at} für eine inhaltliche Änderung jetzt; weitere Schlüssel im Block bleiben."""
        old = (fm or {}).get("generated")
        return {**(old if isinstance(old, dict) else {}), "by": self.actor, "at": now_iso()}

    def replace_body(self, page: str, body: str, detail: str = "") -> Path:
        """Körper ersetzen und generated nachziehen. Gleicher Inhalt (gleicher Prüfwert) ändert nichts.

        Auf einer stable-Seite setzt das status: draft ([trust] edit_resets_stable): Die Freigabe galt dem
        alten Inhalt. verified bleibt stehen, das Ereignis nennt die verfallene Freigabe."""
        path = self.page_path(page)
        self._kein_symlink(path)
        parsed = self.parse(path)
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            raise WikiError(f"{self.rel(path)}: kein Frontmatter")
        if parsed.fm is not None and body_sha256(body) == body_sha256(parsed.body):
            return path
        path.write_text(text[:m.end()] + "\n" + body.strip() + "\n", encoding="utf-8")
        self._pages = None
        try:
            return self.body_changed(page, detail or "Inhalt geschrieben")
        except WikiError:   # sonst bliebe eine stable-Seite mit neuem Text stable
            path.write_text(text, encoding="utf-8")
            self._pages = None
            raise

    def body_changed(self, page: str, detail: str, kind: str = "update") -> Path:
        """Nach einer Änderung am Körper (body, oder Edit/Write über den PostToolUse-Hook): generated nachziehen,
        Ereignis update (create, wenn Write die Seite neu angelegt hat). Auf einer stable-Seite, deren Freigabe nicht
        mehr zum Inhalt passt, status: draft ([trust] edit_resets_stable); passt sie (Text wieder wie freigegeben),
        bleibt stable."""
        path = self.page_path(page)
        parsed = self.parse(path)
        if parsed.fm is not None:   # unlesbares Frontmatter bleibt unangetastet, der Lint meldet es
            self._write_frontmatter(path, {"generated": self.generated_now(parsed.fm)})
            parsed = self.parse(path)
            state = self.release_state(parsed)
            if parsed.fm.get("status") == "stable" and not state["valid"] \
                    and self.cfg["trust"].get("edit_resets_stable", True):
                self._write_frontmatter(path, {"status": "draft"})
                human = state["human"]
                detail += ("; status stable → draft, Freigabe durch "
                           + (f"{human['by']} vom {str(human['at'])[:10]} verfallen" if human else "niemanden")
                           + ", neu prüfen und freigeben (verify)")
        self._pages = None
        self.append_event(kind, self.page_id(path), detail)
        return path

    def release_state(self, page: Page) -> dict:
        """Freigabe und Gegenprüfung einer Seite gegen ihren jetzigen Inhalt.

        Ein verified-Eintrag gilt, solange er zum Inhalt passt: mit Prüfwert, wenn der Prüfwert gleich ist; ohne
        Prüfwert (Eintrag aus der Zeit davor, nur Zeitvergleich), wenn weder generated.at noch ein späterer
        Eintrag mit anderem Prüfwert eine Änderung nach ihm zeigt.

        human: letzter Eintrag durch human:<id>; outdated/why: er gilt nicht mehr; valid: er gilt. check: letzter
        gültige Eintrag eines anderen Akteurs (Claude, Prozess) mit Prüfwert, nur er zählt als Gegenprüfung für
        eine Freigabe. machine: letzter gültige Eintrag eines anderen Akteurs, auch ohne Prüfwert; unhashed: es
        gibt nur solche ohne Prüfwert und keine gültige Freigabe. machine_outdated: Einträge anderer Akteure, aber
        keiner gilt. tier: Vertrauensstufe aus gültigen Einträgen (human-reviewed, machine-confirmed), sonst
        outdated (Einträge, aber keiner gilt) oder unverified.
        """
        fm = page.fm or {}
        sha = body_sha256(page.body)
        entries = [v for v in as_list(fm.get("verified")) if isinstance(v, dict) and as_datetime(v.get("at"))]
        gen = fm.get("generated")
        changed = as_datetime(gen.get("at")) if isinstance(gen, dict) else None
        mismatch = max((as_datetime(v["at"]) for v in entries if v.get(VERIFIED_HASH) and v[VERIFIED_HASH] != sha),
                       default=None)

        def why_not(v: dict, word: str) -> str:
            if v.get(VERIFIED_HASH):
                return "" if v[VERIFIED_HASH] == sha else f"Inhalt seit der {word} geändert (Prüfwert passt nicht)"
            at = as_datetime(v["at"])
            if changed and changed > at:
                return f"generated.at jünger als die {word} (Eintrag ohne Prüfwert, nur Zeitvergleich)"
            if mismatch and mismatch >= at:
                return "spätere Prüfung galt anderem Inhalt (Eintrag ohne Prüfwert, nur Zeitvergleich)"
            return ""

        def latest(items):   # bei gleichem Zeitpunkt der spätere in der Liste
            return max(reversed(items), key=lambda v: as_datetime(v["at"]), default=None)

        humans = [v for v in entries if str(v.get("by", "")).startswith("human:")]
        others = [v for v in entries if not str(v.get("by", "")).startswith("human:")]
        human = latest(humans)
        why = why_not(human, "Freigabe") if human else ""
        machine = latest([v for v in others if not why_not(v, "Prüfung")])
        check = latest([v for v in others if v.get(VERIFIED_HASH) == sha])
        valid = bool(human) and not why
        tier = ("human-reviewed" if valid else "machine-confirmed" if machine
                else "outdated" if entries else "unverified")
        last_other = latest(others)
        return {"sha": sha, "human": human, "check": check, "outdated": bool(why), "why": why, "valid": valid,
                "machine": machine, "unhashed": bool(machine) and not check and not valid,
                "machine_outdated": bool(others) and not machine,
                "machine_why": why_not(last_other, "Prüfung") if others and not machine else "", "last_other": last_other,
                "tier": tier}

    def set_sources(self, page: str, add=(), remove=()) -> tuple[Path, list[str], list[str]]:
        """sources[] chirurgisch ergänzen oder kürzen: (Pfad, hinzugefügte IDs, Hinweise).

        Ein Eintrag in add ist ein Quellen-Slug oder ID=SLUG (eigene Fußnote je Fundstelle auf dieselbe Karte).
        Entfernt wird nur, was der Text nicht mehr als [^id] zitiert.
        """
        path = self.page_path(page)
        parsed = self.parse(path)
        if parsed.fm is None:
            raise WikiError(f"Frontmatter nicht lesbar: {parsed.error}")
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        entries = as_list(parsed.fm.get("sources"))
        ids = [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]
        added, notes = [], []
        for item in add:
            sid, _, slug = item.partition("=") if "=" in item else (item, "", item)
            if not (SLUG_RE.fullmatch(sid) and SLUG_RE.fullmatch(slug)):
                raise WikiError(f"--add {item!r}: Quellen-Slug oder ID=SLUG, beides kebab-case")
            card = self.bundle / folder / f"{slug}.md"
            if not card.exists():
                raise WikiError(f"Quellenkarte {self.rel(card)} fehlt")
            if sid in ids:
                notes.append(f"{sid} steht schon in sources[]")
                continue
            entries.append({"id": sid, "resource": f"/{folder}/{slug}.md"})
            ids.append(sid)
            added.append(sid)
        cited = set(FOOTNOTE_REF_RE.findall(CODE_RE.sub("", parsed.body)))
        for sid in remove:
            if sid not in ids:
                raise WikiError(f"{sid} steht nicht in sources[]")
            if sid in cited:
                raise WikiError(f"[^{sid}] steht noch im Text; erst den Beleg aus dem Text nehmen, dann entfernen")
            entries = [e for e in entries if not (isinstance(e, dict) and str(e.get("id")) == sid)]
            ids.remove(sid)
        if added or remove:
            self._write_frontmatter(path, {"sources": entries, "generated": self.generated_now(parsed.fm)})
            self.append_event("update", parsed.id, "Quellen: " + ", ".join([f"+{s}" for s in added]
                                                                           + [f"−{s}" for s in remove]))
        return path, added, notes

    def new_page(self, type_: str, slug: str, title: str = "", description: str = "", sources=(), tags=(),
                 access: str | None = None, resource: str | None = None, csl: dict | None = None,
                 extra: dict | None = None) -> Path:
        """Neue Karte anlegen; status und verified gehören zur Freigabe (verify), nicht zu extra."""
        spec = self.types.get(type_)
        if spec is None:
            raise WikiError(f"Typ {type_!r} fehlt in wiki.toml [types] ({', '.join(self.types) or 'keine'})")
        if not SLUG_RE.fullmatch(slug):
            raise WikiError(f"Ungültiger Slug {slug!r}: nur kebab-case (a-z, 0-9, -)")
        path = self.bundle / spec["folder"] / f"{slug}.md"
        if os.path.lexists(path):   # auch ein Symlink ins Leere: Schreiben ginge durch ihn hindurch
            raise WikiError(f"{self.rel(path)} existiert schon – Karte von Hand bearbeiten")
        if not self._in_bundle(path):
            raise WikiError(f"{path} liegt außerhalb des Bundles (Symlink?): nicht angelegt")
        if csl:
            csl = {k: v for k, v in csl.items() if k != "id"}
            title = title or one_line(csl.get("title"))
            if not resource:
                resource = f"https://doi.org/{csl['DOI']}" if csl.get("DOI") else csl.get("URL")
        if not title:
            raise WikiError("--title fehlt")
        fm = {"type": type_, "title": title, "description": description}
        if resource:
            fm["resource"] = resource
        fm["status"] = "draft"
        fm["tags"] = list(tags)
        if csl:
            fm["aliases"] = [kurzbeleg(csl)]
        if access:
            fm["access"] = access
        if type_ == SOURCE_TYPE:
            fm["license"] = self.manifest_license((extra or {}).get("raw_packet")) or {"name": "unknown", "url": None}
        if csl:
            fm[self.cfg["export"].get("csl", {}).get("field", "csl")] = csl
        entries = []
        source_folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        for sid in sources:
            card = self.bundle / source_folder / f"{sid}.md"
            if not card.exists():
                raise WikiError(f"Quellenkarte {self.rel(card)} fehlt")
            entries.append({"id": sid, "resource": f"/{source_folder}/{sid}.md", "title": self.parse(card).title})
        if entries:
            fm["sources"] = entries
        for key, value in spec.get("defaults", {}).items():   # [types.X] defaults, z. B. evidenz = "peer-reviewed"
            if key not in fm and key not in (extra or {}):
                fm[key] = value
        gesperrt = sorted({"status", "verified"} & set(extra or {}))
        if gesperrt:
            raise WikiError(f"--set {', '.join(gesperrt)}: setzt nur die Freigabe (verify), nicht new")
        fm.update(extra or {})
        titles = {e["id"]: e.pop("title") for e in entries}
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        if spec["stale_months"]:
            moment = add_months(dt.datetime.now(dt.timezone.utc), int(spec["stale_months"]))
            fm["stale_after"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self.render_template(type_, fm, csl, titles)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + dump_frontmatter(fm) + "---\n\n" + body.lstrip(), encoding="utf-8")
        self._pages = None
        self.append_event("create", self.page_id(path), title if self.cfg["log"].get("link_text") == "id" else "")
        return path

    def render_template(self, type_: str, fm: dict, csl: dict | None, titles: dict | None = None) -> str:
        spec = self.types[type_]
        access = fm.get("access") or ""
        todo = {**TODO_BY_ACCESS.get(access, {}), **spec.get("todo", {}).get(access, {})}
        flags = as_list(fm.get(self.cfg["sources"]["updates_field"]))
        check = fm.get(self.cfg["sources"]["retraction_field"])
        found = [h for h in as_list((check if isinstance(check, dict) else {}).get("hinweise")) if isinstance(h, dict)]
        pubmed = [str(h.get("art")) for h in found if h.get("quelle") == "pubmed"]
        notices = [h for h in found if str(h.get("quelle")).endswith("update-to")]
        values = {
            "title": fm.get("title", ""), "description": fm.get("description", ""), "access": access,
            "kurzbeleg": kurzbeleg(csl, narrative=True) if csl else fm.get("title", ""),
            "apa": (self.render_apa([dict(csl, id="x")]) or "") if csl else "",
            "footnotes": "\n".join(f"[^{s['id']}]: [{(titles or {}).get(s['id'], s['id'])}]({s['resource']})"
                                   for s in fm.get("sources", [])),
            "todo_kurzreferat": todo.get("kurzreferat", "> [!todo] Ausstehend."),
            "todo_kernaussagen": todo.get("kernaussagen", "> [!todo] Ausstehend."),
            "hinweise": (f"\n- ⚠ Crossref meldet: {', '.join(map(str, flags))} – vor Nutzung ansehen." if flags else "")
                        + (f"\n- ⚠ PubMed meldet: {', '.join(pubmed)} – vor Nutzung ansehen." if pubmed else "")
                        + "".join(f"\n- ⚠ Crossref meldet: Diese Karte ist selbst ein Hinweis ({h.get('art')}) zu "
                                  f"{h.get('doi') or '?'}, nicht die Arbeit – vor Nutzung ansehen." for h in notices),
        }
        if spec["template"]:
            template = self.template_path(spec["template"]).read_text(encoding="utf-8")

            def fill(m):
                key = m.group(1)
                if key.startswith("fm."):
                    return one_line(fm.get(key[3:], ""))
                return str(values.get(key, m.group(0)))
            return re.sub(r"\{\{([\w.]+)\}\}", fill, template)
        lines = [f"# {values['kurzbeleg'] + ': ' if csl else ''}{values['title']}", ""]
        if values["apa"]:
            lines += [f"> {values['apa']}", ""]
        elif values["description"] and type_ != SOURCE_TYPE:
            lines += [values["description"], ""]
        if values["hinweise"]:   # Crossref-Hinweise (Rückzug, Korrektur) unter dem APA-Block
            lines += [values["hinweise"].strip(), ""]
        claims = self.cfg["lint"]["claims_section"].lower()
        for section in spec["sections"]:
            text = "> [!todo] Ausstehend."
            if type_ == SOURCE_TYPE and section.lower().startswith("kurzreferat"):
                text = values["todo_kurzreferat"]
            elif type_ == SOURCE_TYPE and section.lower().startswith(claims):
                text = values["todo_kernaussagen"]
            lines += [f"## {section}", "", text, ""]
        if values["footnotes"]:
            lines += [values["footnotes"], ""]
        return "\n".join(lines)

    def template_path(self, name: str) -> Path:
        """Vorlage aus [types.X] template: neben der wiki.toml, sonst im Skill (profiles/, templates/)."""
        base = self.config_path.parent if self.config_path else self.root
        for ordner, candidate in ((base, base / name), (SKILL_DIR, SKILL_DIR / "profiles" / name),
                                  (SKILL_DIR, SKILL_DIR / "templates" / name)):
            if candidate.is_file():
                # sonst holt ein geklontes Wiki über `..` oder einen absoluten Pfad fremde Dateien in neue Karten
                if not candidate.resolve().is_relative_to(ordner.resolve()):
                    raise WikiError(f"Vorlage {name} liegt außerhalb des Projektordners: "
                                    "Vorlagen müssen neben der wiki.toml oder im Skill liegen")
                return candidate
        raise WikiError(f"Vorlage {name} fehlt (gesucht neben der wiki.toml und im Skill unter profiles/, templates/)")

    def fetch_source(self, doi: str = "", pmid: str = "", arxiv: str = "") -> tuple[dict, str, dict, list]:
        """(csl, resource, Metadatenprüfung, Crossref-Hinweise) über Crossref (sonst DataCite), PubMed oder arXiv."""
        try:
            if doi:
                try:
                    m = http_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")["message"]
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    # Crossref kennt die DOI nicht: DataCite-DOIs (Zenodo, arXiv, OSF) dort nachschlagen
                    csl = datacite_csl(doi)
                    return csl, f"https://doi.org/{doi}", {"by": "process:datacite", "at": now_iso()}, []
                csl = crossref_csl(m)
                flags = [u.get("type") for u in m.get("updated-by", []) if u.get("type")]
                # dieselbe Antwort dient der Rückzugsprüfung (check_retraction), ohne zweiten Abruf
                self.crossref_seen[str(csl.get("DOI") or doi).lower()] = crossref_notices(m)
                return csl, f"https://doi.org/{csl['DOI']}", {"by": "process:crossref", "at": now_iso()}, flags
            if pmid:
                csl = pubmed_csl(pmid)
                resource = f"https://doi.org/{csl['DOI']}" if csl.get("DOI") else csl["URL"]
                return csl, resource, {"by": "process:pubmed", "at": now_iso()}, []
            if arxiv:
                return arxiv_csl(arxiv), f"https://arxiv.org/abs/{arxiv}", {"by": "process:arxiv", "at": now_iso()}, []
        except (OSError, ValueError, KeyError) as exc:
            raise WikiError(f"Metadatenabruf fehlgeschlagen ({doi or pmid or arxiv}): {exc}") from exc
        raise WikiError("--doi, --pmid oder --arxiv angeben")

    def check_retraction(self, doi: str = "", pmid: str = "", flags=()) -> dict:
        """Rückzugsprüfung vor der Aufnahme einer Quelle: Crossref (`updated-by`, `update-to`; die Antwort von
        fetch_source wird wiederverwendet) und PubMed (efetch: RetractionIn, ExpressionOfConcernIn, ErratumIn …).

        Ergebnis {geprueft, via, ergebnis, hinweise, warnungen}; ergebnis ist keiner, korrektur, bedenken,
        zurueckgezogen, nicht_geprueft (kein Dienst hat geantwortet) oder, wenn die DOI selbst nur der Hinweis auf eine
        andere Arbeit ist (`update-to`), rueckzugsnotiz oder bedenkennotiz. Ein Netzfehler bricht nichts ab, er steht in
        warnungen, und via nennt nur die Dienste, die geantwortet haben."""
        import xml.etree.ElementTree as ET
        via, notices, warnings = [], [], []
        eutils = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        if doi:
            seen = self.crossref_seen.get(doi.lower())
            if seen is None and flags:   # fetch_source ersetzt: nur die Typen sind bekannt
                seen = [{"art": str(t), "doi": "", "quelle": "crossref updated-by"} for t in flags]
            if seen is None:
                try:
                    seen = crossref_notices(http_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                                                      timeout=15)["message"])
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:   # 404: Crossref kennt die DOI nicht (DataCite), kein Fehler
                        warnings.append(f"Crossref nicht erreichbar (HTTP {exc.code})")
                except (OSError, ValueError, KeyError) as exc:
                    warnings.append(f"Crossref nicht erreichbar ({exc})")
            if seen is not None:
                via.append("crossref")
                notices += seen
            if not pmid:
                try:
                    q = urllib.parse.urlencode({"db": "pubmed", "term": f'"{doi}"[doi]', "retmode": "json", "tool": "okfw"})
                    ids = http_json(f"{eutils}esearch.fcgi?{q}", timeout=15)["esearchresult"].get("idlist") or []
                    pmid = str(ids[0]) if ids else ""
                except (OSError, ValueError, KeyError) as exc:
                    warnings.append(f"PubMed nicht erreichbar ({exc})")
        if pmid:
            try:
                q = urllib.parse.urlencode({"db": "pubmed", "id": pmid, "retmode": "xml", "tool": "okfw"})
                notices += pubmed_notices(http_bytes(f"{eutils}efetch.fcgi?{q}", timeout=15))
                via.append("pubmed")
            except (OSError, ValueError, ET.ParseError) as exc:
                warnings.append(f"PubMed nicht erreichbar ({exc})")
        kinds = {notice_kind(n["art"]) for n in notices if not n["quelle"].endswith("update-to")} - {""}
        own = {notice_kind(n["art"]) for n in notices if n["quelle"].endswith("update-to")}
        if not via:
            warnings.append("Rückzug und Bedenken nicht geprüft: weder Crossref noch PubMed haben geantwortet")
        result = ("nicht_geprueft" if not via else "zurueckgezogen" if "rueckzug" in kinds
                  else "bedenken" if "bedenken" in kinds else "rueckzugsnotiz" if "rueckzug" in own
                  else "bedenkennotiz" if "bedenken" in own
                  else "korrektur" if "korrektur" in kinds | own else "keiner")
        return {"geprueft": now_iso(), "via": via, "ergebnis": result,
                "hinweise": [n for n in notices if notice_kind(n["art"])], "warnungen": warnings}

    def capture(self, slug: str, url: str, files, commit: str | None = None, license_name: str | None = None,
                license_url: str | None = None, note: str | None = None, source_path: str | None = None) -> Path:
        """Rohquelle unveränderlich als Paket <raw>/<slug>/original/ + manifest.json ablegen."""
        if self.raw is None:
            raise WikiError("wiki.toml hat kein [paths] raw – Volltexte mit `hash` als fulltext-Block binden")
        if not SLUG_RE.fullmatch(slug):
            raise WikiError(f"Ungültiger Slug {slug!r}: nur kebab-case")
        self.capture_warnings(commit, license_name, license_url)   # bricht ab, wenn Ablegen nicht erlaubt ist
        packet = self.raw / slug
        if packet.exists():
            raise WikiError(f"{self.rel(packet)} existiert. Rohquellen sind unveränderlich; neue Fassung als {slug}-v2")
        given = [capture_file(item, source_path) for item in files]
        for f, _ in given:
            # vor dem Auflösen prüfen: nach resolve() ist kein Pfad mehr ein Symlink
            if f.is_symlink() or not f.is_file():
                raise WikiError(f"Keine reguläre Datei (Symlinks werden nicht erfasst): {f}")
        (packet / "original").mkdir(parents=True)
        entries, used = [], set()
        for f, repo_path in ((f.resolve(), r) for f, r in given):
            name = f.name
            while name in used:
                name = f"{f.parent.name}-{name}"
            used.add(name)
            target = packet / "original" / name
            shutil.copyfile(f, target)
            os.chmod(target, 0o444)
            entries.append({"path": f"original/{name}", "sha256": sha256_of(target),
                            "bytes": target.stat().st_size, "source_path": repo_path})
        manifest = {"id": slug, "url": url, "retrieved_at": now_iso(), "retrieved_by": self.actor, "commit": commit,
                    "license": {"name": license_name, "url": license_url} if license_name else None,
                    "note": note, "files": entries}
        (packet / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        os.chmod(packet / "manifest.json", 0o444)
        self.append_event("capture", self.rel(packet), note or url)
        return packet

    def capture_warnings(self, commit: str | None, license_name: str | None, license_url: str | None) -> list[str]:
        """Hinweise vor dem Ablegen einer Rohquelle; WikiError, wenn die Lizenz das Ablegen nicht erlaubt.

        Geprüft wird „Volltext lokal ablegen“ in der Rechtematrix (mit [rights.<klasse>] der wiki.toml) für die
        Lizenz aus --license/--license-url: „nicht erlaubt“ bricht ab, „erst fragen“ und Auflagen werden gemeldet.
        """
        lic = {"name": license_name or "", "url": license_url or ""}
        cls = classify_license(lic, self.cfg["licenses"].get("patterns"))[0]
        value, note = self.rights_matrix()[cls]["volltext_ablegen"]
        what = f"Lizenz {license_name or license_url}" if license_name or license_url else "keine Lizenz angegeben"
        if value == "nein":
            raise WikiError(f"Volltext ablegen ist nicht erlaubt ({what}, Klasse {LICENSE_CLASSES[cls]}): "
                            f"{note or 'laut Rechtematrix'}. Nichts erfasst; nur Metadaten auf die Karte")
        warnings = []
        if commit and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit.lower()):
            warnings.append(f"--commit {commit!r} ist kein voller Commit-SHA (40 Hex-Zeichen); "
                            f"`git rev-parse {commit}` im Klon nennt ihn")
        if value == "fragen":
            warnings.append(f"Volltext ablegen: erst fragen ({what}, Klasse {LICENSE_CLASSES[cls]}: {note}). "
                            "Erfasst, aber die Quelle erst nach der Entscheidung weiter nutzen")
        elif value == "bedingt":
            warnings.append(f"Volltext ablegen mit Auflage ({LICENSE_CLASSES[cls]}): {note}")
        return warnings

    def manifest_license(self, packet) -> dict | None:
        """license-Block aus dem manifest.json eines Rohquellen-Pakets (capture --license), sonst None."""
        manifest = self._im_projekt(str(packet) + "/manifest.json") if packet else None
        if manifest is None or not manifest.is_file():
            return None
        data = _manifest_lesen(manifest)
        if data is None:
            return None
        lic = data.get("license")
        if not isinstance(lic, dict) or not lic.get("name"):
            return None
        return {"name": lic["name"], "url": lic.get("url"), "checked": str(data.get("retrieved_at") or "")[:10] or None,
                "via": "capture"}

    def fulltext_block(self, path: str | Path, retrieved_from: str | None = None) -> dict:
        target = (self.root / path).resolve()
        if not target.is_file():
            raise WikiError(f"Keine Datei: {target}")
        block = {"path": self.rel(target), "sha256": sha256_of(target), "retrieved_at": now_iso()}
        if retrieved_from:
            block["retrieved_from"] = retrieved_from
        return {"fulltext": block}

    # ------------------------------------------------------------ Projektionen

    def _sort_key(self, page: Page):
        return page.path.stem if self.cfg["index"]["sort"] == "slug" else page.title.lower()

    def _entry(self, page: Page, href: str) -> str:
        idx = self.cfg["index"]
        title = page.title
        star = idx.get("star_field")
        if star and (page.fm or {}).get(star):
            title += " ★"
        badges = "".join(f" `{v}`" for v in (self._badge(page, b) for b in idx.get("badges", [])) if v)
        return f"* [{title}]({href}){badges}" + (f" - {page.description}" if page.description else "")

    def _badge(self, page: Page, field: str) -> str:
        fm = page.fm or {}
        if field == "license":  # Lizenzname, bei langen Mischangaben die Klasse
            if page.type != SOURCE_TYPE and "license" not in fm:
                return ""
            lic = self.license_of(page)
            if lic["cls"] == "unbekannt":
                return "Lizenz unbekannt" if lic["known"] else "Lizenz nicht eingeordnet"
            return lic["name"] if lic["name"] and len(lic["name"]) <= 28 else lic["label"]
        return str(fm[field]) if fm.get(field) else ""

    def _folder_label(self, name: str) -> tuple[str, str]:
        spec = self.types.get(self.folder_type.get(name, ""), {})
        return spec.get("label") or name, spec.get("description", "")

    def _ordered_folders(self) -> list[str]:
        present = [d.name for d in sorted(self.bundle.iterdir())
                   if d.is_dir() and not d.name.startswith(".") and (d.name != FIGURES_DIR or self._figures_typed())]
        known = [spec["folder"] for spec in self.types.values() if spec["folder"] in present]
        return known + [d for d in present if d not in known]

    @staticmethod
    def order_rank(value) -> int | float:
        """Rang für `order`: Zahlen und ganzzahliger Text zählen, alles andere steht wie eine Seite ohne `order` am Ende."""
        if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
            return int(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
        return 99

    def learning_path(self) -> list[Page]:
        """Einstiegsseiten (Schlagwort aus [index] entry_tag) nach `order`, dann Titel: „Zuerst lesen“
        im Root-Index und Lernpfad im Dashboard."""
        tag = self.cfg["index"].get("entry_tag")
        start = [p for p in self.pages() if p.fm is not None and tag and tag in as_list(p.fm.get("tags"))]
        return sorted(start, key=lambda p: (self.order_rank(p.fm.get("order")), p.title.lower()))

    def render_root_index(self) -> str:
        idx = self.cfg["index"]
        lines = ["---", f'okf_version: "{self.cfg["wiki"]["okf_version"]}"', "---", "",
                 f"# {self.cfg['wiki']['title']}", ""]
        if idx.get("banner"):
            lines += [idx["banner"], ""]
        pages = [p for p in self.pages() if p.fm is not None]
        start = self.learning_path()
        if start:
            lines += [f"# {idx['start_heading']}", ""] if idx.get("start_heading") else []
            for p in start:
                lines.append(self._entry(p, p.path.relative_to(self.bundle).as_posix()))
            lines.append("")
        folders = self._ordered_folders()
        if idx["root"] == "full":
            for name in folders:
                label, _ = self._folder_label(name)
                members = sorted((p for p in pages if p.folder == name), key=self._sort_key)
                lines += [f"# {label}", "", f"* [{label}]({name}/) - {len(members)} {idx['unit']}"]
                lines += [self._entry(p, f"/{p.id}.md") for p in members] + [""]
        else:
            lines += [f"# {idx['areas_heading']}", ""]
            for name in folders:
                label, desc = self._folder_label(name)
                n = sum(1 for p in self.pages() if p.folder == name)
                lines.append(f"* [{label}]({name}/) - " + (f"{desc} " if desc else "") + f"({n} {idx['unit']})")
            lines.append("")
        root_pages = [p for p in pages if not p.folder]
        if root_pages:
            lines += ["# Seiten im Wurzelordner", ""]
            lines += [self._entry(p, p.path.name) for p in sorted(root_pages, key=self._sort_key)] + [""]
        return "\n".join(lines).rstrip() + "\n"

    def render_dir_index(self, directory: Path) -> str:
        rel = directory.relative_to(self.bundle).as_posix()
        label = self._folder_label(rel)[0] if "/" not in rel else directory.name
        lines = [f"# {label}", ""]
        if self.cfg["index"].get("banner"):
            lines += [self.cfg["index"]["banner"], ""]
        members = [p for p in self.pages() if p.path.parent == directory]
        lines += [self._entry(p, p.path.name) for p in sorted(members, key=self._sort_key)]
        subdirs = [d for d in sorted(directory.iterdir())
                   if d.is_dir() and not d.name.startswith(".") and any(p.path.is_relative_to(d) for p in self.pages())]
        if subdirs:
            lines += ["", "# Unterordner", ""] if members else []
            lines += [f"* [{d.name}]({d.name}/)" for d in subdirs]
        if not members and not subdirs:
            lines.append("_(noch keine)_")
        return "\n".join(lines).rstrip() + "\n"

    def _event_target(self, page: str, titles: dict[str, str], stems: dict[str, list[str]]) -> str:
        pid = page.removeprefix("/").removesuffix(".md")
        if pid not in titles and len(stems.get(pid, [])) == 1:
            pid = stems[pid][0]  # ältere Ereignisse nennen nur den Slug
        if pid in titles:
            text = pid if self.cfg["log"].get("link_text") == "id" else titles[pid]
            return f"[{text}](/{pid}.md)"
        return f"`{page}`"

    def render_log(self) -> str:
        logcfg = self.cfg["log"]
        labels = {**LOG_LABELS, **logcfg.get("labels", {})}
        titles = {p.id: p.title for p in self.pages()}
        stems: dict[str, list[str]] = {}
        for pid in titles:
            stems.setdefault(pid.rsplit("/", 1)[-1], []).append(pid)
        seen: set[str] = set()
        days: dict[str, list[str]] = {}
        for e in self.read_events():
            day = str(e.get("ts", ""))[:10]
            page = str(e.get("page", ""))
            kind = str(e.get("kind", "note"))
            detail = one_line(e.get("detail"))
            if kind in ("write", "edit"):
                label = labels["update"] if page in seen else labels["create"]
                detail = ""
            else:
                label = labels.get(kind, kind.capitalize())
            seen.add(page)
            text = f"* **{label}**: {self._event_target(page, titles, stems)}" + (f" – {detail}" if detail else "")
            if logcfg.get("show_actor") and e.get("by"):
                text += f" ({e['by']})"
            days.setdefault(day, []).append(text)
        lines = [f"# {logcfg['title']}", ""]
        if logcfg.get("banner"):
            lines += [logcfg["banner"], ""]
        for day in sorted(days, reverse=True):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            lines += [f"## {day}", ""]
            done: set[str] = set()
            for text in reversed(days[day]):  # OKF §9: neueste zuerst
                if text not in done:
                    done.add(text)
                    lines.append(text)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def csl_items(self) -> list[dict]:
        """CSL-JSON aller Quellenkarten über csl_item, also dieselbe Grundlage wie die APA-Angaben im Dashboard."""
        return sorted((self.csl_item(p) for p in self.pages() if p.fm and p.type == SOURCE_TYPE), key=lambda r: r["id"])

    def csl_style(self) -> Path:
        """Zitierstil für pandoc: [export.csl] style, sonst templates/apa.csl (APA 7)."""
        spec = self.cfg["export"].get("csl", {})
        return self.root / spec["style"] if spec.get("style") else SKILL_DIR / "templates" / "apa.csl"

    def render_apa(self, items: list[dict]) -> str | None:
        """Literaturangaben über pandoc --citeproc mit csl_style(); None ohne pandoc."""
        spec = self.cfg["export"].get("csl", {})
        pandoc = shutil.which("pandoc")
        if not pandoc or not items:
            return None
        doc = f"---\nnocite: '@*'\nlang: {spec.get('lang', 'de-DE')}\n---\n"
        with tempfile.TemporaryDirectory() as tmp:
            bib = Path(tmp) / "refs.json"
            bib.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            cmd = [pandoc, "--citeproc", f"--bibliography={bib}", "-f", "markdown",
                   "-t", "markdown-smart-fenced_divs-native_divs-raw_html-citations", "--wrap=none"]
            cmd.append(f"--csl={self.csl_style()}")
            try:
                out = subprocess.run(cmd, input=doc, capture_output=True, text=True, timeout=180, check=True).stdout
            except (OSError, subprocess.SubprocessError):
                return None
        return out.strip()

    def projections(self, include_slow: bool = True) -> dict[Path, str]:
        """{Pfad: Inhalt} aller generierten Dateien: index.md je Ordner, log.md, Exporte."""
        out = {self.bundle / "index.md": self.render_root_index(), self.bundle / "log.md": self.render_log()}
        for d in sorted(p for p in self.bundle.rglob("*") if p.is_dir()):
            if not self._hidden(d) and any(p.path.is_relative_to(d) for p in self.pages()):
                out[d / "index.md"] = self.render_dir_index(d)
        export = self.cfg["export"]
        if export.get("csl", {}).get("path"):
            out[self.root / export["csl"]["path"]] = (
                json.dumps(self.csl_items(), ensure_ascii=False, indent=1) + "\n")
        bib = export.get("bibliography", {})
        if bib.get("path") and include_slow:
            rendered = self.render_apa(self.csl_items())
            if rendered is not None:
                title = bib.get("title", "Literaturverzeichnis")
                out[self.root / bib["path"]] = (f"# {title}\n\n> Generiert aus den Quellenkarten – "
                                                f"nicht von Hand bearbeiten.\n\n{rendered}\n")
        return out

    def rebuild(self, check: bool = False) -> list[Path]:
        if not self.bundle.is_dir():
            return []
        changed = []
        for path, content in self.projections().items():
            if path.exists() and not path.is_symlink() and path.read_text(encoding="utf-8", errors="replace") == content:
                continue
            changed.append(path)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                replace_file(path, content)
        if self.cfg["dashboard"].get("auto") and not check:
            self.write_dashboard()
        return changed

    # ------------------------------------------------------------ Lint

    def edges(self) -> list[tuple[Page, Page, str]]:
        """Gerichtete Kanten im Bundle: Links im Text (link) und sources[].resource (source).

        OKF §5.1: Zeigt eine Quelle auf ein Konzept im Bundle, ist sie bereits eine Kante im Graphen.
        """
        by_path = {p.path.resolve(): p for p in self.pages()}
        out = []
        for p in self.pages():
            if p.fm is None:
                continue
            targets = [(t, "link") for t in LINK_RE.findall(CODE_RE.sub("", p.body))]
            targets += [(str(s["resource"]), "source") for s in as_list(p.fm.get("sources"))
                        if isinstance(s, dict) and s.get("resource")]
            seen = set()
            for target, kind in targets:
                q = by_path.get(self.resolve(p.path, target))
                if q is not None and q is not p and (q.id, kind) not in seen:
                    seen.add((q.id, kind))
                    out.append((p, q, kind))
        return out

    def severity(self, code: str) -> str:
        return self.cfg["lint"]["severity"].get(code, SEVERITY.get(code, "warn"))

    def lint(self) -> list[Finding]:
        findings: list[Finding] = []

        def add(code, path, msg, sev=None):
            sev = sev or self.severity(code)
            if sev != "off":
                findings.append(Finding(sev, code, self.rel(path), msg))

        for name, hint in unknown_config(self.cfg):
            add("config_unknown", self.config_path or self.root / "wiki.toml",
                f"{name} kennt der Kern nicht und wirkt nicht" + (f" (gemeint: {hint}?)" if hint else ""))
        self._lint_rollen(add)
        self._lint_design(add)
        if not self.bundle.is_dir():
            add("bundle_missing", self.bundle, "Bundle-Ordner fehlt")
            return findings
        self._lint_reserved(add)
        pages = self.pages()
        for path in self.bad_links:
            add("link_leaves_bundle", path, "Symlink zeigt nicht auf eine Datei im Bundle: keine Seite, nicht gelesen")
        by_path = {p.path.resolve(): p for p in pages}
        inbound: Counter = Counter()
        edges: dict[Path, set] = {}
        for p in pages:
            if p.error:
                add("frontmatter_invalid", p.path, p.error)
        for p, q, _ in self.edges():
            out = edges.setdefault(p.path.resolve(), set())
            if q.path.resolve() not in out:
                out.add(q.path.resolve())
                inbound[q.path.resolve()] += 1
        for p in pages:
            if p.fm is not None:
                self._lint_page(p, add, by_path, inbound, edges)
        self._lint_scope(add, pages)
        self._lint_visibility(add, pages)
        self._lint_raw(add, pages)
        self._lint_signatures(add, pages)
        return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.path, f.code))

    def _lint_rollen(self, add) -> None:
        """[rollen] daten und sperre (Modellrollen, rollen.py): ein unbekannter Wert wäre für rollen.py die strengste
        Datenklasse, eine Zeichenkette statt Liste bei sperre sperrte nichts."""
        spec, where = self.cfg["rollen"], self.config_path or self.root / "wiki.toml"
        daten = spec.get("daten")
        if daten and daten not in DATENKLASSEN:
            add("rollen_unknown", where, f"[rollen] daten {daten!r} nicht in {' | '.join(DATENKLASSEN)}")
        sperre = spec.get("sperre")
        if not (isinstance(sperre, list) and all(isinstance(s, str) for s in sperre)):
            add("rollen_unknown", where, "[rollen] sperre muss eine Liste von Anbieter-Kennungen sein, z. B. [\"openai\"]")

    def _lint_design(self, add) -> None:
        try:
            self.design_css()
        except WikiError as exc:
            add("design_unsafe", self.config_path or self.root / "wiki.toml", str(exc))

    def design_css(self) -> str:
        """CSS aus [dashboard] design (Pfad relativ zum Projektordner), sonst design.css im Projektordner, geprüft."""
        rel = self.cfg["dashboard"].get("design")
        if not rel:
            if not (self.root / "design.css").is_file():
                return ""
            rel = "design.css"
        path = (self.root / str(rel)).resolve()
        if not path.is_relative_to(self.root):
            raise WikiError(f"[dashboard] design {rel!r} liegt außerhalb des Projektordners")
        if not path.is_file():
            raise WikiError(f"[dashboard] design: {rel} fehlt")
        try:
            css = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WikiError(f"[dashboard] design: {rel} ist kein UTF-8") from exc
        problem = css_problem(css)
        if problem:
            raise WikiError(f"[dashboard] design: {rel} {problem}")
        return css

    def _lint_reserved(self, add) -> None:
        okf = self.cfg["wiki"]["okf_version"]
        for path in sorted(self.bundle.rglob("index.md")):
            if self._hidden(path) or not (self._in_bundle(path) and path.is_file()):
                continue
            raw, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if path.parent == self.bundle:
                if raw is None:
                    add("okf_version_missing", path, f'Root-index.md ohne okf_version: "{okf}" (OKF §12)')
                    continue
                try:
                    fm = load_yaml(raw)
                except yaml.YAMLError:
                    fm = None
                if not isinstance(fm, dict) or set(fm) != {"okf_version"} or str(fm["okf_version"]) != okf:
                    add("root_index_frontmatter", path, f'Root-index.md darf nur okf_version: "{okf}" tragen (OKF §8)')
            elif raw is not None:
                add("index_frontmatter", path, "index.md unterhalb der Wurzel darf kein Frontmatter haben (OKF §8)")
        for path in sorted(self.bundle.rglob("log.md")):
            if self._hidden(path) or not (self._in_bundle(path) and path.is_file()):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if split_frontmatter(text)[0] is not None:
                add("log_frontmatter", path, "log.md darf kein Frontmatter haben (OKF §9)")
            heads = [h.strip() for h in re.findall(r"^## (.+)$", text, re.M)]
            for h in heads:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", h):
                    add("log_date_heading", path, f"Datumsüberschrift nicht ISO 8601: {h}")
            iso = [h for h in heads if re.fullmatch(r"\d{4}-\d{2}-\d{2}", h)]
            if iso != sorted(iso, reverse=True):
                add("log_order", path, "Datumsgruppen nicht absteigend (OKF §9: neueste zuerst)")
        projections = self.projections()
        for path, content in projections.items():
            if not path.exists() or path.is_symlink() or path.read_text(encoding="utf-8", errors="replace") != content:
                add("projection_stale", path, "veraltet – `okfw.py rebuild` ausführen")
        bib = self.cfg["export"].get("bibliography", {})
        if bib.get("path") and self.root / bib["path"] not in projections:
            why = "pandoc fehlt" if not shutil.which("pandoc") else "keine Quellenkarte"
            add("projection_unavailable", self.root / bib["path"], f"Literaturverzeichnis nicht erzeugbar: {why}")

    def _lint_page(self, p: Page, add, by_path, inbound, edges) -> None:
        fm, path = p.fm, p.path
        body = CODE_RE.sub("", p.body)
        spec = self.types.get(p.type)
        if not p.type:
            add("missing_type", path, "Pflichtfeld type fehlt oder ist leer (OKF §4.1)")
        elif spec is None and self.types:
            add("type_unknown", path, f"type {p.type!r} ist in wiki.toml nicht beschrieben")
        expected = self.folder_type.get(p.folder)
        if expected and p.type and expected != p.type:
            add("type_folder_mismatch", path, f"type {p.type!r} liegt im Ordner von {expected!r}")
        if not SLUG_RE.fullmatch(path.stem):
            add("path_not_kebab", path, "Dateiname nicht kebab-case")
        spec = spec or {**TYPE_DEFAULTS, "min_links": 0}
        # eine Meldung je fehlender Lizenz: führt das Profil license, trägt diese Meldung den Hinweis von license_missing
        hint = {"license": LICENSE_HINT} if p.type == SOURCE_TYPE else {}
        for key in spec["required"]:
            if is_empty(fm.get(key)):
                add("missing_required", path, f"Pflichtfeld {key} fehlt (Profil){hint.get(key, '')}")
        for key in spec["recommended"]:
            if key not in spec["required"] and is_empty(fm.get(key)):
                add("missing_recommended", path, f"{key} fehlt (empfohlen){hint.get(key, '')}")
        for key, allowed in self.cfg["enums"].items():
            for value in as_list(fm.get(key)):
                if value not in allowed:
                    add("enum_invalid", path, f"{key} {value!r} nicht in {' | '.join(map(str, allowed))}")

        # Vertrauen und Lebenszyklus (OKF §5.2–5.5, §7)
        status = fm.get("status")
        if status is not None and status not in STATUS:
            add("status_invalid", path, f"status {status!r} nicht in draft | stable | deprecated")
        gen = fm.get("generated")
        if gen is not None:
            if not isinstance(gen, dict) or not gen.get("by"):
                add("generated_invalid", path, "generated braucht by")
            else:
                if not ACTOR_RE.match(str(gen["by"])):
                    add("actor_format", path, f"generated.by {gen['by']!r} folgt nicht der Akteurskonvention")
                if "at" in gen and not is_timestamp(gen["at"]):
                    add("timestamp_invalid", path, "generated.at ist kein ISO-8601-Zeitpunkt mit Offset")
        verified = as_list(fm.get("verified"))
        for v in verified:
            if not isinstance(v, dict) or not v.get("by") or not is_timestamp(v.get("at")):
                add("verified_invalid", path, "verified-Einträge brauchen by und at (ISO 8601 mit Offset)")
            elif not ACTOR_RE.match(str(v["by"])):
                add("actor_format", path, f"verified.by {v['by']!r} folgt nicht der Akteurskonvention")
        if status == "stable" and not verified:
            add("stable_unverified", path, "status stable ohne verified-Eintrag")
        elif status == "stable" and not any(isinstance(v, dict) and str(v.get("by", "")).startswith("human:")
                                            for v in verified):
            add("stable_unverified", path, "status stable ohne verified-Eintrag durch human:<id>")
        order = fm.get("order")
        if (order is not None and self.cfg["index"].get("entry_tag") in as_list(fm.get("tags"))
                and (isinstance(order, (str, bool)) or self.order_rank(order) == 99 and order != 99)):
            add("order_invalid", path, f"order {order!r} ist keine Zahl; die Reihenfolge im Lernpfad braucht eine Zahl")
        release = self.release_state(p)
        if release["outdated"] and self.severity("verified_outdated") != "off":
            human = release["human"]
            msg = f"Freigabe durch {human['by']} vom {str(human['at'])[:10]} verfallen: {release['why']}"
            if status == "stable":   # ohne eigene Schwere in [lint.severity] ein Fehler
                add("verified_outdated", path, msg + "; status ist noch stable",
                    self.cfg["lint"]["severity"].get("verified_outdated", "error"))
            else:
                add("verified_outdated", path, msg)
        elif status == "stable" and release["human"] and release["human"].get(VERIFIED_HASH) and not release["check"]:
            # nur Freigaben mit Prüfwert: ältere sind nicht vergleichbar und lösen hier nichts aus
            add("human_without_check", path, f"Freigabe durch {release['human']['by']} ohne Gegenprüfung zum selben "
                "Inhalt (verify durch Claude oder einen Prozess)")
        if release["unhashed"]:
            m = release["machine"]
            add("verified_unhashed", path, f"Gegenprüfung durch {m['by']} vom {str(m['at'])[:10]} ohne Prüfwert "
                f"({VERIFIED_HASH}): gilt weiter, zählt aber nicht als Gegenprüfung für eine Freigabe; neu "
                "gegenprüfen lassen und mit verify vermerken")
        elif release["machine_outdated"] and not release["human"]:
            m = release["last_other"]
            add("check_outdated", path, f"Gegenprüfung durch {m['by']} vom {str(m['at'])[:10]} verfallen: "
                f"{release['machine_why']}")
        if "stale_after" in fm:
            stale = as_datetime(fm["stale_after"])
            if stale is None:
                add("timestamp_invalid", path, "stale_after ist kein ISO-8601-Zeitpunkt mit Offset")
            elif dt.datetime.now(dt.timezone.utc) >= stale:
                add("stale", path, f"seit {stale.date()} veraltet – Inhalt prüfen")

        # Herkunft: sources[] und Fußnoten (OKF §5.1)
        ids, sources = [], {}
        for s in as_list(fm.get("sources")):
            if not isinstance(s, dict) or not s.get("resource"):
                add("source_invalid", path, f"sources-Eintrag ohne resource: {s!r} (OKF §5.1)")
                continue
            if s.get("id"):
                ids.append(str(s["id"]))
                sources[str(s["id"])] = s
            r = self.resolve(path, str(s["resource"]))
            if r is not None and r.suffix == ".md" and not r.exists():
                add("source_missing", path, f"Quelle {s['resource']} existiert nicht")
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            add("source_duplicate_id", path, f"doppelte sources-IDs: {dupes}")
        refs = set(FOOTNOTE_REF_RE.findall(body))
        defs = set(FOOTNOTE_DEF_RE.findall(body))
        for label in sorted(refs - set(ids)):
            add("footnote_unsourced", path, f"Fußnote [^{label}] hat keinen sources-Eintrag")
        for label in sorted(refs - defs):
            add("footnote_undefined", path, f"Fußnote [^{label}] ohne Definition im Text")
        for label in sorted(set(ids) - refs):
            add("source_uncited", path, f"sources-Eintrag {label} wird im Text nicht zitiert")
        if spec["needs_footnotes"] and not refs and status != "deprecated":
            add("no_footnotes", path, "keine Fußnoten – Sachaussagen brauchen [^id] (Profil)")
        for label in sorted(refs & set(sources)):
            r = self.resolve(path, str(sources[label]["resource"]))
            target = by_path.get(r)
            if target and target.fm and target.fm.get("access") == "citation":
                add("claim_on_citation_only", path,
                    f"[^{label}] belegt mit einer Quelle, deren Karte nur access: citation hat")

        # Aufbau
        if spec["sections"]:
            heads = [h.lower() for h in H2_RE.findall(p.body)]
            missing = [s for s in spec["sections"] if not any(h.startswith(s.lower()) for h in heads)]
            if missing:
                add("sections_missing", path, "Abschnitte fehlen: " + ", ".join(missing))

        self._lint_figures(p, add, sources, by_path)

        # Links und Graph (OKF §6)
        outgoing = 0
        bundle = self.bundle.resolve()
        for target in LINK_RE.findall(body):
            r = self.resolve(path, target)
            if r is None:
                continue
            if not r.is_relative_to(bundle):
                add("link_leaves_bundle", path, f"Link verlässt das Bundle: {target}")
            elif not r.exists():
                add("broken_link", path, f"Linkziel fehlt: {target}")
            elif r != path.resolve():
                outgoing += 1
        if outgoing < int(spec["min_links"]):
            add("few_links", path, f"nur {outgoing} interne Links (Profil: mindestens {spec['min_links']})")
        me = path.resolve()
        isolated = self.cfg["lint"]["orphans"] == "isolated"
        if inbound[me] == 0 and (not isolated or not edges.get(me)):
            add("orphan", path, "keine eingehenden Links" + (" und keine ausgehenden" if isolated else ""))
        self._lint_contradictions(p, add, by_path)

        if p.type == SOURCE_TYPE:
            self._lint_source(p, add, spec)

    # ------------------------------------------------------------ Schaubilder

    def figure_file(self, name: str) -> Path:
        return self.bundle / FIGURES_DIR / f"{name}.svg"

    def figure_refs(self, body: str) -> list[dict]:
        """Bildverweise auf Schaubilder im Text (nicht in Code): {name, alt, caption} mit der Zeile darunter."""
        lines = CODE_RE.sub("", body).splitlines()
        out = []
        for i, line in enumerate(lines):
            for m in IMAGE_RE.finditer(line):
                src = FIGURE_SRC_RE.match(m.group(2))
                if src:
                    out.append({"name": src.group(1), "alt": m.group(1),
                                "caption": lines[i + 1] if i + 1 < len(lines) else ""})
        return out

    def figure_svg(self, name: str, prefix: str = "", label: str = "") -> tuple[str, list[str]]:
        """Bereinigtes SVG der Abbildung `name` (clean_svg); FigureError, wenn die Datei fehlt oder abgelehnt wird."""
        path = self.figure_file(name)
        if not path.is_file():
            raise FigureError(f"{self.rel(path)} fehlt")
        if not self._in_bundle(path):
            raise FigureError(f"{self.rel(path)} liegt außerhalb des Bundles")
        if path.stat().st_size > FIGURE_MAX_BYTES:   # gar nicht erst einlesen
            raise FigureError(f"größer als {FIGURE_MAX_BYTES // 1024} KB")
        return clean_svg(path.read_bytes(), prefix, label)

    def _lint_figures(self, p: Page, add, sources: dict, by_path: dict) -> None:
        for ref in self.figure_refs(p.body):
            name, caption = ref["name"], ref["caption"]
            try:
                self.figure_svg(name)
            except FigureError as exc:
                add("figure_missing", p.path, f"Abbildung {name}: {exc}")
            labels = FOOTNOTE_REF_RE.findall(caption)
            if not (FIGURE_CAPTION_RE.match(caption) and labels):
                add("figure_caption", p.path, f"Abbildung {name}: direkt darunter fehlt eine Zeile `*Abb.: …*` mit "
                    "Fußnote, etwa `*Abb.: Was sie zeigt. Eigene Darstellung nach Müller[^mueller].*`")
                continue
            if not re.search(r"übernommen", caption, re.I):
                continue
            for label in labels:
                entry = sources.get(label)
                card = by_path.get(self.resolve(p.path, str(entry["resource"]))) if entry else None
                if card is None or card.fm is None:
                    continue
                value, note = self.rights_of(card)["abbildung"]
                if value != "ja":
                    add("figure_rights", p.path, f"Abbildung {name} ist aus {card.title} übernommen, Recht dazu "
                        f"({RIGHTS_ACTIONS['abbildung']}): {RIGHT_WORDS[value]}" + (f" – {note}" if note else ""))

    def _lint_contradictions(self, p: Page, add, by_path) -> None:
        """Ein Widerspruchsblock braucht mindestens eine verlinkte Gegenseite mit eigenem Marker."""
        body = CODE_RE.sub("", p.body)
        for m in CONTRADICTION_RE.finditer(body):
            block = body[m.start():].split("\n\n", 1)[0]
            others = [by_path.get(self.resolve(p.path, t)) for t in LINK_RE.findall(block)]
            others = [o for o in others if o and o is not p]
            if others and not any(CONTRADICTION_RE.search(CODE_RE.sub("", o.body)) for o in others):
                add("contradiction_one_sided", p.path, "Widerspruch nur hier markiert, nicht auf "
                    + ", ".join(f"/{o.id}.md" for o in others))

    def _lint_source(self, p: Page, add, spec: dict) -> None:
        fm, path = p.fm, p.path
        lint = self.cfg["lint"]
        access = fm.get("access")
        packet = fm.get("raw_packet")
        if packet:
            manifest = self._im_projekt(str(packet) + "/manifest.json")
            if manifest is None:
                add("raw_missing", path, f"{packet} liegt außerhalb des Projekts")
            elif not manifest.is_file():
                add("raw_missing", path, f"{packet}/manifest.json fehlt")
            else:
                data = _manifest_lesen(manifest)
                if data is None or not isinstance(data.get("files"), list):
                    add("raw_tampered", path, f"{packet}/manifest.json nicht lesbar")
                else:
                    for f in data["files"]:
                        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not f["path"]:
                            add("raw_tampered", path, f"{packet}: Manifest-Eintrag ohne path")
                            continue
                        target = self._im_projekt(f"{packet}/{f['path']}")
                        if target is None:
                            add("raw_tampered", path, f"{packet}/{f['path']} liegt außerhalb des Projekts")
                        elif not target.is_file() or sha256_of(target) != f.get("sha256"):
                            add("raw_tampered", path, f"{packet}/{f['path']} weicht vom Manifest ab")
        fulltexts = [f for f in as_list(fm.get("fulltext")) if isinstance(f, dict)]
        for ft in fulltexts:
            target = self._im_projekt(str(ft.get("path", "")))
            if not ft.get("path") or not ft.get("sha256"):
                add("fulltext_unanchored", path, "fulltext braucht path und sha256")
            elif target is None:
                add("fulltext_absent", path, f"Volltext {ft['path']} liegt außerhalb des Projekts (sha256 ungeprüft)")
            elif not target.is_file():
                add("fulltext_absent", path, f"Volltext {ft['path']} lokal nicht vorhanden (sha256 ungeprüft)")
            elif sha256_of(target) != ft["sha256"]:
                add("fulltext_tampered", path, f"Volltext {ft['path']} weicht vom gespeicherten sha256 ab")
        if access in lint["fulltext_access"] and not packet and not fulltexts:
            add("fulltext_unanchored", path, f"access {access}, aber weder raw_packet noch fulltext-Block")
        if access == "citation":
            section = re.search(rf"^## +{re.escape(lint['claims_section'])}.*?$(.*?)(?=^## |\Z)", p.body, re.M | re.S)
            if section and claim_lines(section.group(1)):
                add("citation_with_claims", path,
                    f"{lint['claims_section']} trotz access citation – Inhalt nicht belegt")
        lic = self.license_of(p)
        by_profile = is_empty(fm.get("license")) and "license" in [*spec["required"], *spec["recommended"]]
        if not lic["name"] and not lic["url"]:
            if not by_profile:   # sonst meldet missing_required bzw. missing_recommended, mit demselben Hinweis
                add("license_missing", path, f"Quelle ohne license.name{LICENSE_HINT}")
        elif lic["cls"] == "unbekannt" and lic["known"]:
            add("license_unknown", path, "Lizenz unbekannt: vor Volltextablage, Abbildungen oder Weitergabe klären")
        elif lic["cls"] == "unbekannt":
            add("license_unclassified", path, f"Lizenz {lic['name'] or lic['url']!r} nicht eingeordnet: license.class "
                f"setzen ({', '.join(LICENSE_CLASSES)}) oder Muster in [licenses.patterns]")
        self._lint_fundweg(p, add)
        stored = self.fulltext_locations(p)
        if stored:
            rights = self.rights_of(p)
            keep, share = rights["volltext_ablegen"], rights["volltext_weitergeben"]
            if keep[0] in ("nein", "fragen"):
                add("fulltext_rights", path, f"Volltext abgelegt ({', '.join(s['path'] for s in stored)}), "
                    f"Recht dazu nicht geklärt: {keep[1] or keep[0]}")
            shared = [s["path"] for s in stored if s["tracked"]]
            if shared and share[0] in ("nein", "fragen"):
                add("fulltext_shared", path, f"Volltext im Git-Repository versioniert ({', '.join(shared)}), "
                    f"Weitergabe nicht erlaubt: {share[1] or share[0]}")
        priority = lint.get("priority_field")
        if priority and fm.get(priority) and access == "citation":
            add("priority_citation_only", path, f"{priority}: true, aber nur access citation")
        csl = fm.get(self.cfg["export"].get("csl", {}).get("field", "csl"))
        if isinstance(csl, dict):
            if not (csl.get("title") and csl.get("issued") and (csl.get("author") or csl.get("editor"))):
                add("csl_incomplete", path, "csl braucht title, issued und author oder editor")
            if not csl.get("DOI") and not csl.get("accessed"):
                add("csl_incomplete", path, "Quelle ohne DOI braucht csl.accessed (Abrufdatum)")
        elif not self.csl_item(p).get("issued") and not UNDATED_RE.match(str(fm.get("date") or "")):
            add("source_undated", path, "Quellenkarte ohne Datum: erscheint im Beleg als „o. J.“; date, created, "
                "published oder year setzen (Commit-, Veröffentlichungs- oder Stand-Datum), bei echt undatierten "
                "Quellen date: o. J.")

    # ------------------------------------------------------------ Umfang, Sichtbarkeit und Fundweg

    def scope_values(self) -> list[str]:
        """Werte des Umfangsfelds in Rangfolge vom Kern nach außen (aus [enums])."""
        field = self.cfg["scope"].get("field")
        return [str(v) for v in self.cfg["enums"].get(field, [])] if field else []

    def scopes(self) -> dict[str, tuple[str, bool]]:
        """{Seiten-ID: (Umfang, abgeleitet)}.

        Eigener Wert geht vor. Quellen ohne Wert erben den engsten Umfang der Seiten, die sie verlinken
        oder in sources[] führen ([scope] derive); abgeleitet wird nur hier, geschrieben wird nichts.
        """
        field = self.cfg["scope"].get("field")
        if not field:
            return {}
        own = {}
        for p in self.pages():
            values = [str(v) for v in as_list((p.fm or {}).get(field)) if not is_empty(v)]
            if values:
                own[p.id] = values[0]
        out = {pid: (v, False) for pid, v in own.items()}
        if self.cfg["scope"].get("derive", True):
            order = self.scope_values()
            rank = lambda v: order.index(v) if v in order else len(order)
            sources = {q.id for q in self.source_cards()}
            best: dict[str, str] = {}
            for p, q, _ in self.edges():
                if q.id in sources and q.id not in own and p.id not in sources and p.id in own:
                    if q.id not in best or rank(own[p.id]) < rank(best[q.id]):
                        best[q.id] = own[p.id]
            out.update({qid: (v, True) for qid, v in best.items()})
        return out

    def _lint_scope(self, add, pages) -> None:
        field = self.cfg["scope"].get("field")
        if not field:
            return
        scopes = self.scopes()
        values = " | ".join(self.scope_values()) or "Wert laut Profil"
        for p in pages:
            if p.fm is None or p.id in scopes:
                continue
            if p.type == SOURCE_TYPE and self.cfg["scope"].get("derive", True):
                add("scope_missing", p.path, f"{field} fehlt und lässt sich nicht ableiten "
                    f"(keine Seite mit {field} nutzt diese Quelle; {values})")
            else:
                add("scope_missing", p.path, f"{field} fehlt ({values})")

    def visibility_values(self) -> list[str] | None:
        """Erlaubte Werte des Sichtbarkeitsfelds: aus [enums], sonst default und hidden; None ohne Angabe."""
        spec = self.cfg["visibility"]
        field = spec.get("field")
        if field in self.cfg["enums"]:
            return [str(v) for v in self.cfg["enums"][field]]
        if spec.get("default"):
            return [str(spec["default"]), *map(str, as_list(spec.get("hidden")))]
        return None

    def visibility(self) -> dict[str, tuple[str, bool]]:
        """{Seiten-ID: (Sichtbarkeit, abgeleitet)}; leer ohne [visibility] field.

        Eigener Wert geht vor, sonst [visibility] default. Quellenkarten ohne eigenen Wert, die nur von
        verborgenen Seiten verlinkt oder in sources[] geführt werden, gelten selbst als verborgen ([visibility]
        derive), damit die Quellenauswahl eine interne Linie nicht verrät. Abgeleitet wird nur hier.
        """
        spec = self.cfg["visibility"]
        field = spec.get("field")
        if not field:
            return {}
        default = str(spec.get("default") or "")
        hidden = {str(v) for v in as_list(spec.get("hidden"))}
        out, own = {}, set()
        for p in self.pages():
            values = [str(v) for v in as_list((p.fm or {}).get(field)) if not is_empty(v)]
            if values:
                own.add(p.id)
            out[p.id] = (values[0] if values else default, False)
        if spec.get("derive", True) and hidden:
            sources = {q.id for q in self.source_cards()}
            users: dict[str, set[str]] = {}
            for p, q, _ in self.edges():
                if q.id in sources and q.id not in own:
                    users.setdefault(q.id, set()).add(p.id)
            # Quellen, die nur verborgene Quellen nutzen, erst im nächsten Durchgang
            changed = True
            while changed:
                changed = False
                for qid, ids in users.items():
                    if out[qid][0] not in hidden and all(out[i][0] in hidden for i in ids):
                        out[qid] = (min(out[i][0] for i in ids), True)
                        changed = True
        return out

    def hidden_ids(self) -> set[str]:
        """IDs der Seiten, die in der öffentlichen Ausgabe fehlen: Wert aus [visibility] hidden oder unbekannter
        Wert (ein Tippfehler soll eine Seite nicht veröffentlichen; der Lint meldet ihn als enum_invalid). Ohne
        bekannte Werte ist jeder Wert unbekannt (Lint visibility_values_missing)."""
        hidden = {str(v) for v in as_list(self.cfg["visibility"].get("hidden"))}
        allowed = self.visibility_values()
        return {pid for pid, (v, _) in self.visibility().items()
                if v in hidden or (v and (allowed is None or v not in allowed))}

    def _lint_visibility(self, add, pages) -> None:
        field = self.cfg["visibility"].get("field")
        if not field:
            return
        allowed = self.visibility_values()
        if allowed is None:
            add("visibility_values_missing", self.config_path or self.root / "wiki.toml",
                f"[visibility] field = {field!r} ohne bekannte Werte: [enums] {field} oder [visibility] default setzen; "
                "bis dahin fehlt jede Seite mit diesem Feld in der öffentlichen Ausgabe, dashboard --oeffentlich bricht ab")
        elif field not in self.cfg["enums"]:  # sonst prüft das [enums] schon
            for p in pages:
                for value in as_list((p.fm or {}).get(field)):
                    if str(value) not in allowed:
                        add("enum_invalid", p.path, f"{field} {value!r} nicht in {' | '.join(allowed)}")
        for p, targets in self.visibility_leaks():
            add("visibility_leak", p.path, f"öffentliche Seite verweist auf verborgene: {', '.join(targets)} "
                "(in der öffentlichen Ausgabe wird der Link zu Text, der Beleg entfällt)")

    def visibility_leaks(self) -> list[tuple[Page, list[str]]]:
        """Öffentliche Seiten, die verborgene verlinken oder in sources[] führen, mit den Zielen."""
        hidden = self.hidden_ids()
        leaks: dict[str, tuple[Page, set[str]]] = {}
        for p, q, _ in self.edges():
            if p.id not in hidden and q.id in hidden:
                leaks.setdefault(p.id, (p, set()))[1].add(f"/{q.id}.md")
        return [(p, sorted(targets)) for p, targets in leaks.values()]

    def fundweg_of(self, page: Page) -> list[dict]:
        return [e for e in as_list((page.fm or {}).get(self.cfg["fundweg"].get("field") or "fundweg"))
                if isinstance(e, dict)]

    def fundweg_entry(self, art: str, db: str | None = None, query: str | None = None, von: str | None = None,
                      at: str | None = None, by: str | None = None) -> dict:
        """Ein Fundweg-Eintrag {art, db, query, von, at, by}; at und by stehen ohne Angabe auf jetzt und [wiki] actor."""
        if art not in FUNDWEG_ARTEN:
            raise WikiError(f"Fundweg-Art {art!r}: eine von {', '.join(FUNDWEG_ARTEN)}")
        if art == "zitatverfolgung" and not von:
            raise WikiError("zitatverfolgung braucht die Quelle, deren Literatur verfolgt wurde: "
                            "new … --fundweg-von <slug> bzw. fundweg … --von <slug>")
        if at and not is_timestamp(at):
            raise WikiError("--at muss ein ISO-8601-Zeitpunkt mit Offset sein, z. B. 2026-10-25T09:30:00Z")
        if by and not ACTOR_RE.match(by):
            raise WikiError("--by folgt der Akteurskonvention, z. B. claude-code/claude-opus-5-5 oder human:<id>")
        entry = {"art": art}
        entry.update({k: v for k, v in (("db", db), ("query", query), ("von", von)) if v})
        entry.update({"at": at or now_iso(), "by": by or self.actor})
        return entry

    def set_fundweg(self, page: str, entry: dict, append: bool = False) -> Path:
        return self.set_fundwege(page, [entry], append=append)

    def set_fundwege(self, page: str, entries: list[dict], append: bool = False) -> Path:
        """Einen oder mehrere Fundwege setzen (ein Eintrag als Zuordnung, mehrere als Liste) oder anhängen; ein Schreibvorgang."""
        path = self.page_path(page)
        parsed = self.parse(path)
        if parsed.type != SOURCE_TYPE:
            raise WikiError(f"{parsed.id} ist keine Quellenkarte (type {parsed.type or '–'}): Fundweg gibt es nur für Quellen")
        field = self.cfg["fundweg"].get("field") or "fundweg"
        old = self.fundweg_of(parsed) if append else []
        entries = [e for e in entries if e not in old]
        if append and not entries:   # dieselben Einträge stehen schon da
            return path
        value = old + entries if old else entries[0] if len(entries) == 1 else entries
        self._write_frontmatter(path, {field: value, "generated": self.generated_now(parsed.fm)})
        self.append_event("update", parsed.id, "Fundweg: " + ", ".join(
            e["art"] + (f" ({e['db']})" if e.get("db") else "") for e in entries))
        return path

    def fundwege_aus_lauf(self, lauf: str | Path, kandidat: str) -> tuple[dict, list[dict], list[str]]:
        """(Kandidat, Fundwege, Hinweise) aus LAUF/kandidaten.json der Recherche (`api.py zusammenfuehren`): je Fund ein
        Eintrag mit dem echten Zeitpunkt, db, query und by des Laufs. Ein Kandidat wird über seine Nr, DOI oder PMID
        gewählt; ein reiner Websuche-Treffer ohne Kennung aus Weg 1 oder 2 gilt nicht. Ein Fund, dessen weg selbst eine
        Fundweg-Art ist, behält sie (zitatverfolgung nur mit von); alle anderen Wege sind eine Suche."""
        datei = Path(lauf).expanduser() / "kandidaten.json"
        if not datei.is_file():
            raise WikiError(f"{datei} fehlt: erst `api.py zusammenfuehren LAUF`")
        try:
            kandidaten = json.loads(datei.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise WikiError(f"{datei} nicht lesbar: {exc}") from exc
        wahl = str(kandidat).strip()
        norm_doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", wahl, flags=re.I).lower()
        k = next((k for k in kandidaten if isinstance(k, dict) and (
            str(k.get("nr")) == wahl or norm_doi == str(k.get("doi") or "").lower()
            or wahl.rstrip("/").rsplit("/", 1)[-1] == str(k.get("pmid") or "-"))), None)
        if k is None:
            raise WikiError(f"Kandidat {wahl!r} nicht in {datei}")
        if k.get("nur_websuche") and not k.get("aufgeloest"):
            raise WikiError(f"Kandidat {k.get('nr')} kommt nur aus der Websuche und ist nicht über Weg 1 oder 2 aufgelöst")
        entries, notes = [], []
        for f in (f for f in as_list(k.get("funde")) if isinstance(f, dict)):
            art = f["weg"] if str(f.get("weg")) in FUNDWEG_ARTEN else "suche"
            if art == "zitatverfolgung" and not f.get("von"):
                raise WikiError(f"Kandidat {k.get('nr')}: Fund zitatverfolgung ohne von, die Quelle dazu fehlt im Lauf; "
                                "Fundweg von Hand setzen: `fundweg <karte> --art zitatverfolgung --von <slug>`")
            entries.append(self.fundweg_entry(art, db=f.get("db"), query=f.get("query"), von=f.get("von"),
                                              at=f.get("at"), by=f.get("by")))
            if f.get("at_aus_auftrag"):
                notes.append(f"{f.get('db')}: Zeitpunkt = Startzeit laut Auftrag (Agent ohne Uhr)")
            if f.get("aufgeloest_via"):
                notes.append(f"{f.get('db')}: Websuche-Treffer, aufgelöst über {f['aufgeloest_via']}")
        if not entries:
            raise WikiError(f"Kandidat {k.get('nr')} hat keine Funde in {datei}")
        return k, entries, notes

    def _lint_fundweg(self, p: Page, add) -> None:
        field = self.cfg["fundweg"].get("field") or "fundweg"
        raw = (p.fm or {}).get(field)
        if is_empty(raw):
            add("fundweg_missing", p.path, f"{field} fehlt: Wie wurde die Quelle gefunden? ({' | '.join(FUNDWEG_ARTEN)})")
            return
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        for e in as_list(raw):
            if not isinstance(e, dict) or e.get("art") not in FUNDWEG_ARTEN:
                add("fundweg_invalid", p.path, f"{field}.art muss eine von {' | '.join(FUNDWEG_ARTEN)} sein")
                continue
            if e["art"] == "zitatverfolgung" and not e.get("von"):
                add("fundweg_invalid", p.path, f"{field}: zitatverfolgung braucht von: <slug>")
            if e.get("von") and not (self.bundle / folder / f"{e['von']}.md").exists():
                add("fundweg_invalid", p.path, f"{field}.von {e['von']!r} ist keine Quellenkarte")
            if e["art"] == "suche" and not (e.get("db") and e.get("query")):
                add("fundweg_incomplete", p.path, f"{field}: Suche ohne db und query ist nicht reproduzierbar")
            if "at" in e and not is_timestamp(e["at"]):
                add("timestamp_invalid", p.path, f"{field}.at ist kein ISO-8601-Zeitpunkt mit Offset")
            if e.get("by") and not ACTOR_RE.match(str(e["by"])):
                add("actor_format", p.path, f"{field}.by {e['by']!r} folgt nicht der Akteurskonvention")

    # ------------------------------------------------------------ Lizenzen und Nutzungsrechte

    def source_keys(self, page: Page) -> dict[str, str]:
        """Vergleichbare Kennungen einer Quellenkarte (kennungen) aus csl-Block, flachen Feldern und resource."""
        fm = page.fm or {}
        csl = fm.get(self.cfg["export"].get("csl", {}).get("field", "csl"))
        csl = csl if isinstance(csl, dict) else {}
        number = str(csl.get("number") or "")
        return kennungen(csl.get("DOI") or fm.get("doi"), csl.get("PMID") or fm.get("pmid"),
                         fm.get("arxiv") or (number if number.lower().startswith("arxiv:") else ""),
                         fm.get("resource") or fm.get("url") or csl.get("URL"))

    def license_of(self, page: Page) -> dict:
        lic = (page.fm or {}).get("license")
        name, url = license_parts(lic)
        cls, known = classify_license(lic, self.cfg["licenses"].get("patterns"))
        return {"name": name, "url": url, "cls": cls, "label": LICENSE_CLASSES[cls], "known": known}

    def license_named(self, page: Page) -> bool:
        """Trägt die Karte eine Lizenz mit Namen (außer unknown und gleichwertigen)? Dann überschreibt `lizenz --fetch`
        sie nur mit --force: eine benannte, nicht eingeordnete Lizenz braucht eine Klasse, keinen neuen Abruf."""
        return not UNKNOWN_LICENSE_RE.match(self.license_of(page)["name"])

    def rights_matrix(self) -> dict[str, dict[str, tuple[str, str]]]:
        """Rechte je Lizenzklasse, mit den Abweichungen aus [rights.<klasse>] der wiki.toml."""
        return {cls: {a: parse_right({**row, **self.cfg["rights"].get(cls, {})}.get(a)) for a in RIGHTS_ACTIONS}
                for cls, row in RIGHTS_MATRIX.items()}

    def rights_of(self, page: Page) -> dict[str, tuple[str, str]]:
        """Nutzungsrechte einer Quelle: Klasse laut Lizenz, darüber `rights:` der Karte (z. B. eine Genehmigung)."""
        rights = dict(self.rights_matrix()[self.license_of(page)["cls"]])
        own = (page.fm or {}).get("rights")
        if isinstance(own, dict):
            rights.update({a: parse_right(v) for a, v in own.items() if a in RIGHTS_ACTIONS})
        return rights

    def fulltext_locations(self, page: Page) -> list[dict]:
        """Wo Volltexte der Quelle liegen (Rohquellen-Paket, fulltext-Blöcke) und ob Git sie versioniert."""
        fm = page.fm or {}
        tracked = self._git_tracked()
        out = []
        if fm.get("raw_packet"):
            prefix = str(fm["raw_packet"]).rstrip("/") + "/"
            out.append({"path": prefix.rstrip("/"), "tracked": bool(tracked) and any(t.startswith(prefix) for t in tracked)})
        for ft in as_list(fm.get("fulltext")):
            if isinstance(ft, dict) and ft.get("path"):
                out.append({"path": str(ft["path"]), "tracked": bool(tracked) and str(ft["path"]) in tracked})
        return out

    def _lint_signatures(self, add, pages) -> None:
        """[trust] signed_human: Jeder verified-Eintrag durch human:<id> kam mit einem signierten Commit ins Wiki
        (git blame je Seite, Signatur über %G?: G oder U). Ohne Git-Repo oder ohne Opt-in läuft nichts."""
        if not self.cfg["trust"].get("signed_human") or self.severity("human_unsigned") == "off" \
                or self._git_tracked() is None:
            return
        found = []   # (Seite, Eintrag, Commit oder None)
        for p in pages:
            humans = [v for v in as_list((p.fm or {}).get("verified")) if isinstance(v, dict) and v.get("at")
                      and str(v.get("by", "")).startswith("human:")]
            if not humans:
                continue
            lines = self._blame(p.path)
            for v in humans:
                at, by = str(v["at"]), str(v["by"])
                hits = [c for c, line in lines if at in line and by in line] or [c for c, line in lines if at in line]
                found.append((p, v, hits[0] if hits else None))
        commits = sorted({c for _, _, c in found if c and c.strip("0")})
        signed = {}
        if commits:
            out = subprocess.run(["git", "-C", str(self.root), "log", "--no-walk", "--format=%H %G?", *commits],
                                 capture_output=True, text=True, timeout=60)
            signed = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
        for p, v, commit in found:
            mark = signed.get(commit or "", "")
            if mark in ("G", "U"):
                continue
            where = ("noch nicht committet" if not commit or not commit.strip("0")
                     else f"aus Commit {commit[:10]} ohne gültige Signatur (%G? {mark or '?'})")
            add("human_unsigned", p.path, f"Freigabe durch {v['by']} vom {str(v['at'])[:10]} {where}")

    def _blame(self, path: Path) -> list[tuple[str, str]]:
        """(Commit, Zeile) je Zeile der Datei laut git blame; nicht committete Zeilen mit Commit 000…; [] bei Fehler."""
        try:
            out = subprocess.run(["git", "-C", str(self.root), "blame", "--porcelain", "--", str(path)],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return []
        rows, commit = [], ""
        for line in out.stdout.splitlines() if out.returncode == 0 else []:
            if line.startswith("\t"):
                rows.append((commit, line[1:]))
            elif re.match(r"[0-9a-f]{40} \d+ \d+", line):
                commit = line.split(" ", 1)[0]
        return rows

    def _git_tracked(self) -> set[str] | None:
        """Von Git versionierte Dateien relativ zum Projektordner; None ohne Git."""
        if not hasattr(self, "_tracked"):
            try:
                out = subprocess.run(["git", "-C", str(self.root), "ls-files", "-z"], capture_output=True, timeout=30)
                self._tracked = (set(out.stdout.decode("utf-8", "replace").split("\0")) - {""}
                                 if out.returncode == 0 else None)
            except (OSError, subprocess.SubprocessError):
                self._tracked = None
        return self._tracked

    def identifiers(self, page: Page) -> tuple[str, str, str]:
        """(DOI, PMID, PMCID) aus csl-Block, Frontmatter, resource oder Lizenz-URL."""
        fm = page.fm or {}
        csl = fm.get(self.cfg["export"].get("csl", {}).get("field", "csl"))
        csl = csl if isinstance(csl, dict) else {}
        urls = " ".join(str(u) for u in (fm.get("resource"), csl.get("URL"), license_parts(fm.get("license"))[1]) if u)
        doi = str(csl.get("DOI") or fm.get("doi") or "").strip()
        if not doi:
            m = re.search(r"doi\.org/(10\.[^\s?#]+)", urls)
            doi = m.group(1) if m else ""
        pmcid = str(csl.get("PMCID") or fm.get("pmcid") or "").strip()
        if not pmcid:
            m = re.search(r"\b(PMC\d{5,})\b", urls)
            pmcid = m.group(1) if m else ""
        return doi, str(csl.get("PMID") or fm.get("pmid") or "").strip(), pmcid

    def lookup_license(self, doi: str = "", pmid: str = "", pmcid: str = "") -> dict | None:
        """Lizenz über Crossref oder DataCite (DOI), Europe PMC (DOI, PMID oder PMCID) und OpenAlex (DOI oder PMID).

        Nur offizielle APIs, keine Webseiten. Die erste eindeutige Angabe in dieser Reihenfolge gewinnt; sonst die
        erste unklare (Lizenzangabe ohne bekanntes Muster).
        """
        today = dt.date.today().isoformat()
        unclear = []
        if doi:
            in_crossref = True
            try:
                entries = http_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")["message"].get("license") or []
            except urllib.error.HTTPError as exc:
                entries, in_crossref = [], exc.code != 404
            except (OSError, ValueError, KeyError):
                entries = []
            rank = {"vor": 0, "unspecified": 1, "am": 2, "tdm": 3}
            for e in sorted(entries, key=lambda e: rank.get(e.get("content-version"), 9)):
                name = license_name_from(e.get("URL", ""))
                if name:
                    return {"name": name, "url": e["URL"], "checked": today,
                            "via": f"crossref ({e.get('content-version', '?')})"}
            if entries:
                unclear.append({"name": "Lizenz laut Verlag, siehe URL", "url": entries[0].get("URL"), "checked": today,
                                "via": f"crossref ({entries[0].get('content-version', '?')})"})
            # DataCite-DOIs (Zenodo, arXiv, OSF) kennt Crossref nicht; ihre Lizenz steht in der rightsList
            for r in [] if in_crossref else datacite_rights(doi):
                uri, text = str(r.get("rightsUri") or ""), str(r.get("rights") or "").strip()
                named = license_name_from(uri) or license_name_from(text)
                lic = {"name": named if named.startswith(("CC", "Public")) else text or named or "Lizenz laut DataCite",
                       "url": uri or None, "checked": today, "via": "datacite"}
                if named or classify_license(lic)[0] != "unbekannt":
                    return lic
                unclear.append(lic)
        if doi or pmid or pmcid:
            query = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED" if pmid else f"PMCID:{pmcid}"
            url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
                {"query": query, "resultType": "core", "format": "json", "pageSize": 1})
            try:
                hits = http_json(url).get("resultList", {}).get("result", [])
            except (OSError, ValueError):
                hits = []
            if hits and hits[0].get("license"):
                raw = str(hits[0]["license"])
                return {"name": license_name_from(raw) or raw, "url": None, "checked": today, "via": "europepmc"}
        if doi or pmid:
            # OpenAlex führt die Lizenz je Fundort (Verlag, Repositorium) und Fassung; die Verlagsfassung geht vor
            key = f"doi:{doi}" if doi else f"pmid:{pmid}"
            try:
                work = http_json(f"https://api.openalex.org/works/{urllib.parse.quote(key, safe=':/')}")
            except (OSError, ValueError):
                work = {}
            rank = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2}
            for loc in sorted((x for x in work.get("locations") or [] if x.get("license")),
                              key=lambda x: rank.get(x.get("version"), 9)):
                raw = str(loc["license"])
                name = license_name_from(raw) or ("Public Domain" if raw == "public-domain" else "")
                via = f"openalex ({loc.get('version') or '?'})"
                if name:
                    return {"name": name, "url": None, "checked": today, "via": via}
                unclear.append({"name": f"Lizenz laut OpenAlex: {raw}", "url": None, "checked": today, "via": via})
        return unclear[0] if unclear else None

    def set_license(self, page: str, lic: dict, detail: str = "") -> Path:
        path = self.page_path(page)
        lic = {k: v for k, v in lic.items() if v not in ("", None) or k in ("name", "url")}
        self._write_frontmatter(path, {"license": lic, "generated": self.generated_now(self.parse(path).fm)})
        self.append_event("update", self.page_id(path), detail or f"Lizenz: {lic.get('name')}")
        return path

    def _lint_raw(self, add, pages) -> None:
        if not self.raw or not self.raw.is_dir():
            return
        linked = {str((p.fm or {}).get("raw_packet", "")).rstrip("/") for p in pages}
        stems = {p.path.stem for p in pages if p.type == SOURCE_TYPE}
        for packet in sorted(d for d in self.raw.iterdir() if d.is_dir()):
            if self.rel(packet) not in linked and packet.name not in stems:
                add("raw_uningested", packet, "Rohquelle ohne Quellenkarte")

    # ------------------------------------------------------------ Zitieren (APA 7 über CSL)

    def source_cards(self) -> list[Page]:
        """Quellenkarten: type source oder eine Karte im Quellenordner (z. B. Altlasten mit anderem type)."""
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        return [p for p in self.pages() if p.fm and (p.type == SOURCE_TYPE or p.folder == folder)]

    def page_refs(self, page: Page, by_path: dict) -> list[tuple[str, "Page | None", object]]:
        """(Label, Quellenkarte oder None, Eintrag) je sources-Eintrag. Bloße Slugs (Altform) werden
        nachsichtig als /<quellenordner>/<slug>.md gelesen; der Lint meldet sie trotzdem."""
        out = []
        for entry in as_list((page.fm or {}).get("sources")):
            label = str(entry.get("id") or entry.get("resource", "")) if isinstance(entry, dict) else str(entry)
            out.append((label, by_path.get(self.ref_path(page, entry)), entry))
        return out

    def ref_path(self, page: Page, entry) -> Path | None:
        """Zielpfad eines sources-Eintrags (None für externe Ressourcen)."""
        if isinstance(entry, dict):
            return self.resolve(page.path, str(entry.get("resource", "")))
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        return (self.bundle / folder / f"{entry}.md").resolve()

    def _drop_withheld(self, page: Page, body: str, refs: list) -> tuple[str, list]:
        """Öffentliche Ausgabe: Belege auf weggelassene Seiten entfallen samt Fußnote und Literaturangabe."""
        gone = {label for label, _, entry in refs if self.ref_path(page, entry) in self.withheld}
        if not gone:
            return body, refs
        alt = "|".join(map(re.escape, sorted(gone)))

        def clean(segment: str) -> str:
            segment = re.sub(rf"^\[\^(?:{alt})\]:.*\n?(?:[ \t]+\S.*\n?)*", "", segment, flags=re.M)
            return re.sub(rf"[ \t]*\[\^(?:{alt})\](?!:)", "", segment)

        parts = re.split(f"({CODE_RE.pattern})", body, flags=re.S)
        body = "".join(clean(seg) if i % 2 == 0 else seg for i, seg in enumerate(parts))
        return body, [r for r in refs if r[0] not in gone]

    def _apa_cites(self, body: str, labels: dict, catalog: dict) -> tuple[str, list[str]]:
        """Fußnoten [^id] auf Quellenkarten werden zu APA-Klammerbelegen; ihre Definitionen entfallen.

        Fundstellen aus der Fußnote (S., Art., § …) wandern in den Beleg, der übrige Fußnotentext in den
        Tooltip. Satzzeichen direkt vor dem Beleg rücken dahinter, außer nach Abkürzungen wie „z. B.“.
        """
        notes, cites = {}, []

        def drop_def(m):
            label, text = m.group(1), m.group(2).strip()
            if label not in labels:
                return m.group(0)
            if text and not re.fullmatch(r"\[[^\]]*\]\([^)]*\)", text):
                notes[label] = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
            return ""

        run_re = re.compile(r"(?P<pre>[.!?,;:])?(?P<run>\[\^[^\]\s]+\](?!:)(?:[ \t]*\[\^[^\]\s]+\](?!:))*)")

        def abbreviation(text: str) -> bool:
            word = re.search(r"(\S+)$", text)
            w = word.group(1) if word else ""
            return len(w) <= 2 or bool(re.fullmatch(r"(?:\w\.)+\w?", w)) or w.lower() in {
                "bzw", "vgl", "ca", "etc", "abb", "nr", "art", "abs", "tab", "kap", "inkl", "ggf", "evtl", "usw"}

        def convert(segment: str) -> str:
            segment = re.sub(r"^\[\^([^\]\s]+)\]:[ \t]*(.*)$\n?", drop_def, segment, flags=re.M)

            def repl(m):
                ids = re.findall(r"\[\^([^\]\s]+)\]", m.group("run"))
                known = [i for i in ids if i in labels]
                if not known:
                    return m.group(0)
                parts = []
                for i in known:
                    key = labels[i].path.stem
                    text = catalog[key]["cite"]
                    loc = LOCATOR_RE.search(notes.get(i, ""))
                    if loc:
                        # Satzpunkt am Ende der Fußnote („Zeile 41–54.“) gehört nicht zur Fundstelle
                        text += ", " + html.escape(re.sub(r"(?<=\d)\.$", "", loc.group(0)))
                    tip = html.escape(notes.get(i, ""), quote=True)
                    parts.append(f'<a class="cite-ref" href="#lit-{html.escape(key, quote=True)}"'
                                 + (f' title="{tip}"' if tip else "") + f">{text}</a>")
                cites.append('<span class="cite">(' + "; ".join(parts) + ")</span>")
                token = f"⟦c{len(cites) - 1}⟧" + "".join(f"[^{u}]" for u in ids if u not in labels)
                pre, before = m.group("pre") or "", segment[:m.start()]
                if pre and not (pre == "." and abbreviation(before)):
                    return f" {token}{pre}"
                gap = "" if (pre or before[-1:].isspace() or not before) else " "
                return f"{pre}{' ' if pre else gap}{token}"

            return run_re.sub(repl, segment)

        parts = re.split(f"({CODE_RE.pattern})", body, flags=re.S)
        # Definitionen erst sammeln (stehen meist am Ende), dann Belege ersetzen
        for i in range(0, len(parts), 2):
            for m in re.finditer(r"^\[\^([^\]\s]+)\]:[ \t]*(.*)$", parts[i], re.M):
                drop_def(m)
        parts = [convert(seg) if i % 2 == 0 else seg for i, seg in enumerate(parts)]
        return "".join(parts), cites

    def csl_item(self, page: Page) -> dict:
        """CSL-JSON für eine Quellenkarte: der csl-Block, sonst aus flachen Feldern oder Repo-Angaben abgeleitet."""
        fm = page.fm or {}
        field = self.cfg["export"].get("csl", {}).get("field", "csl")
        if isinstance(fm.get(field), dict):
            return {"id": page.path.stem, **{k: v for k, v in fm[field].items() if k != "id"}}
        item = {"id": page.path.stem, "title": one_line(fm.get("title")) or page.title}
        url = str(fm.get("url") or fm.get("resource") or "")
        if fm.get("authors") or fm.get("doi") or fm.get("year"):
            worktype = str(fm.get("worktype") or fm.get("type") or "")
            item["type"] = {"article-review": "article-journal", "standard": "report", "web-page": "webpage",
                            "conference": "paper-conference", "journal_article": "article-journal"}.get(
                worktype, worktype if worktype in ("article-journal", "book", "report", "thesis", "dataset") else "article-journal")
            if csl_names(fm.get("authors")):
                item["author"] = csl_names(fm.get("authors"))
            for key, target in (("container", "container-title"), ("venue", "container-title"), ("volume", "volume"),
                                ("issue", "issue"), ("pages", "page")):
                if fm.get(key) and target not in item:
                    item[target] = str(fm[key])
            issued = csl_date(fm.get("year") or fm.get("date") or fm.get("created") or fm.get("published"))
            if issued:
                item["issued"] = issued
            if fm.get("doi"):
                item["DOI"] = str(fm["doi"])
            elif url:
                item["URL"] = url
            return item
        fmt = str(fm.get("format") or "")
        item["type"] = {"code": "software", "dataset": "dataset"}.get(fmt, "webpage")
        if fmt == "pull-request":
            item["genre"] = "Pull Request"
        pinned = fm.get("pinned") if isinstance(fm.get("pinned"), dict) else {}
        if fm.get("author"):
            item["author"] = [{"literal": str(a)} for a in as_list(fm["author"])]
        elif pinned.get("repo") and fmt in ("documentation", "code", "specification", "dataset"):
            item["author"] = [{"literal": str(pinned["repo"]).split("/")[0]}]
        if "github.com" in url:
            item["publisher" if item["type"] in ("software", "dataset") else "container-title"] = "GitHub"
        issued = csl_date(fm.get("date") or fm.get("created") or fm.get("published"))
        if issued:
            item["issued"] = issued
        if url:
            item["URL"] = url
        packet = fm.get("raw_packet")
        manifest = self._im_projekt(str(packet) + "/manifest.json") if packet else None
        if manifest and manifest.is_file():
            data = _manifest_lesen(manifest)
            accessed = csl_date(data.get("retrieved_at")) if data else None
            if accessed:
                item["accessed"] = accessed
        return item

    def apa_catalog(self) -> dict[str, dict]:
        """{Slug: {cite, entry, order}} für alle Quellenkarten; ein einziger pandoc-Aufruf mit APA-7-CSL.

        cite ist der Klammerbeleg ohne Klammern ("Sarkar &amp; Drosos, 2025"), entry die Literaturangabe als HTML.
        Ohne pandoc einfache Ersatzform aus Autor, Jahr, Titel, URL.
        """
        if getattr(self, "_apa", None) is not None:
            return self._apa
        items = [self.csl_item(p) for p in self.source_cards()]
        spec = self.cfg["export"].get("csl", {})
        style = self.csl_style()
        pandoc = shutil.which("pandoc")
        out = None
        if pandoc and items:
            doc = (f"---\nnocite: '@*'\nlang: {spec.get('lang', 'de-DE')}\n---\n\n"
                   + "\n\n".join(f"::: {{#cite-{it['id']}}}\n[@{it['id']}]\n:::" for it in items))
            with tempfile.TemporaryDirectory() as tmp:
                bib = Path(tmp) / "refs.json"
                bib.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                cmd = [pandoc, "--citeproc", f"--bibliography={bib}", f"--csl={style}", "-f", "markdown-raw_html",
                       "-t", "html", "--wrap=none"]
                try:
                    out = subprocess.run(cmd, input=doc, capture_output=True, text=True, timeout=180, check=True).stdout
                except (OSError, subprocess.SubprocessError):
                    out = None
        catalog = {}
        if out:
            cites = dict(re.findall(r'<div id="cite-([^"]+)">\s*<p><span class="citation"[^>]*>(.*?)</span></p>', out, re.S))
            for order, (key, entry) in enumerate(re.findall(r'<div id="ref-([^"]+)" class="csl-entry"[^>]*>(.*?)</div>', out, re.S)):
                cite = " ".join(cites.get(key, "").split())
                catalog[key] = {"cite": clean_entry(cite[1:-1] if cite.startswith("(") and cite.endswith(")") else cite),
                                "entry": clean_entry(" ".join(entry.split())), "order": order}
        for order, it in enumerate(sorted(items, key=lambda i: i["id"])):
            if it["id"] in catalog:
                continue
            year = csl_year(it)
            who = kurzbeleg(it).rsplit(",", 1)[0] if it.get("author") else str(it.get("title", it["id"]))
            link = str(it.get("URL") or (f"https://doi.org/{it['DOI']}" if it.get("DOI") else ""))
            entry = f"{html.escape(who)} ({html.escape(year)}). <em>{html.escape(str(it.get('title', '')))}</em>."
            if safe_link(link):
                entry += f' <a href="{html.escape(link)}">{html.escape(link)}</a>'
            elif link:
                entry += f" {html.escape(link)}"
            catalog[it["id"]] = {"cite": html.escape(f"{who}, {year}"), "entry": entry, "order": 10000 + order}
        self._apa = catalog
        return catalog

    # ------------------------------------------------------------ Dashboard

    def subtype_field(self, type_: str, fm: dict) -> str:
        """Feld mit der Unterart eines Typs: [types.X] subtype, sonst das erste vorhandene Standardfeld."""
        spec = self.types.get(type_, {})
        if spec.get("subtype"):
            return spec["subtype"]
        return next((f for f in ("category", "format", "evidenz", "worktype") if fm.get(f)), "")

    def dashboard_data(self) -> dict:
        """Was das Dashboard zum inhaltlichen Erkunden braucht: Seiten mit Vorschau und Kategorien, Kanten.

        Bewusst ohne Lint-Befunde und Prüfstatus (dafür gibt es `lint` und `status`), außer der Freigabe gegen
        den jetzigen Inhalt, und die nur in der normalen Ausgabe. Dateipfade stehen nur als
        Ablageort der Volltexte einer Quelle darin (`stored`, mit Git-Status), und nur in der normalen Ausgabe.
        """
        edges = self.edges()
        inbound: Counter = Counter()
        outbound: Counter = Counter()
        pairs = set()
        for p, q, _ in edges:
            if (p.id, q.id) not in pairs:
                pairs.add((p.id, q.id))
                inbound[q.id] += 1
                outbound[p.id] += 1
        reader = self.reader_html()
        catalog = self.apa_catalog()
        sources = {q.id for q in self.source_cards()}
        domain_field = self.cfg["taxonomy"].get("field", "domain")
        scopes = self.scopes()
        vis_field = self.cfg["visibility"].get("field")
        vis = self.visibility() if not self.public else {}
        hidden = self.hidden_ids() if vis else set()
        nodes = []
        for p in self.pages():
            fm = p.fm or {}
            sub_field = self.subtype_field(p.type, fm)
            cite = catalog.get(p.path.stem, {}).get("cite") if p.id in sources else None
            is_source = p.id in sources
            rights = self.rights_of(p) if is_source else {}
            nodes.append({
                **({"visibility": vis[p.id][0], "visibility_derived": vis[p.id][1], "internal": p.id in hidden}
                   if vis else {}),
                "license": {**(lic := self.license_of(p)), "url": safe_link(lic["url"])} if is_source else None,
                "rights": [[a, *rights[a]] for a in RIGHTS_ACTIONS] if is_source else [],
                "stored": self.fulltext_locations(p) if is_source and not self.public else [],
                # Fundweg-Zeitpunkt nur als Datum: das Dashboard zeigt Tage, keine Uhrzeiten
                "fundweg": [{**f, "at": str(f["at"])[:10]} if f.get("at") else f for f in self.fundweg_of(p)]
                           if is_source else [],
                "scope": scopes.get(p.id, ("", False))[0], "scope_derived": scopes.get(p.id, ("", False))[1],
                "id": p.id, "title": p.title, "type": p.type or "?", "description": p.description,
                "access": str(fm.get("access") or "") if p.id in sources else "",
                "in": inbound[p.id], "out": outbound[p.id],
                "domain": [str(v) for v in as_list(fm.get(domain_field)) if not is_empty(v)],
                "subtype": str(fm.get(sub_field) or "") if sub_field else "", "subfield": sub_field,
                "tags": [str(t) for t in as_list(fm.get("tags"))][:12],
                "aliases": [str(a) for a in as_list(fm.get("aliases"))][:6],
                "cite": html.unescape(re.sub(r"<[^>]+>", "", cite)) if cite else "",
                "ris": ris_record(self.csl_item(p)) if p.id in sources else "",
                "heading": reader[p.id]["heading"], "html": reader[p.id]["html"],
                "excerpt": reader[p.id]["excerpt"], "outline": reader[p.id]["outline"],
                "refs": reader[p.id]["refs"], "apa": reader[p.id]["apa"], "open": reader[p.id]["open"],
                "updated": str((fm.get("generated") or {}).get("at", ""))[:10] if isinstance(fm.get("generated"), dict) else "",
                # Freigabe gegen den jetzigen Inhalt: valid, outdated (verfallen) oder leer; trust: Vertrauensstufe
                # aus gültigen Vermerken (outdated = Vermerke, aber keiner passt). Beides nur in der normalen Ausgabe
                **({"release": "", "trust": ""} if self.public or p.fm is None else
                   {"release": "outdated" if (st := self.release_state(p))["outdated"] else "valid" if st["valid"] else "",
                    "trust": st["tier"]}),
                "stable": fm.get("status") == "stable",
            })
        types, labels = self.types, self.cfg.get("labels", {})
        scope_values = self.scope_values() or sorted({v for v, _ in scopes.values()})
        if self.public:
            # Nur Namen, die öffentliche Seiten brauchen: kein Typ, Umfang oder lesbarer Wert, den allein
            # weggelassene Seiten tragen (etwa ein Themenbereich nur für interne Seiten).
            used = {str(v) for p in self.pages() for value in (p.fm or {}).values()
                    for v in as_list(value) if isinstance(v, (str, int, float))}
            types = {n: spec for n, spec in types.items() if any(p.type == n for p in self.pages())}
            labels = {f: {v: text for v, text in m.items() if v in used} for f, m in labels.items()
                      if f != vis_field and isinstance(m, dict)}
            scope_values = [v for v in scope_values if any(s == v for s, _ in scopes.values())]
        return {
            "title": self.dashboard_title(), "generated": now_iso()[:10], "version": VERSION,
            "types": [{"name": n, "label": spec["label"], "folder": spec["folder"]} for n, spec in types.items()],
            "labels": labels,
            "scope": ({"field": self.cfg["scope"]["field"], "label": self.cfg["scope"].get("label") or "Umfang",
                       "default": self.cfg["scope"].get("default") or "",
                       "values": [[v, self.cfg["labels"].get(self.cfg["scope"]["field"], {}).get(v, v)]
                                  for v in scope_values]}
                      if self.cfg["scope"].get("field") else None),
            "visibility": ({"field": vis_field, "label": self.cfg["visibility"].get("label") or "Sichtbarkeit",
                            "values": [[v, self.cfg["labels"].get(vis_field, {}).get(v, v)]
                                       for v in (self.visibility_values() or sorted({v for v, _ in vis.values()}))]}
                           if vis else None),
            "fundweg_arten": FUNDWEG_ARTEN,
            "path": [p.id for p in self.learning_path()],
            "glossary": [str(t) for t in as_list(self.cfg["dashboard"].get("glossary"))],
            "license_classes": LICENSE_CLASSES, "rights_actions": RIGHTS_ACTIONS,
            "rights_matrix": {cls: [[a, *row[a]] for a in RIGHTS_ACTIONS] for cls, row in self.rights_matrix().items()},
            "nodes": nodes,
            "edges": [{"s": p.id, "t": q.id, "kind": kind} for p, q, kind in edges],
            "release": None if self.public else self.release_queue(),
        }

    def release_queue(self) -> dict:
        """„Zur Freigabe“ im Dashboard: Seiten mit Gegenprüfung zum jetzigen Inhalt, aber ohne gültige Freigabe
        durch einen Menschen (mit belegten Aussagen und der Notiz der Gegenprüfung), verfallene Freigaben ohne
        neue Gegenprüfung und Seiten, deren Gegenprüfung keinen Prüfwert hat (unhashed, Lint verified_unhashed).
        command ist der Anfang des verify-Aufrufs, human die Kennung aus [trust] human, sonst die häufigste;
        check_command der Aufruf der Gegenprüfung aus [trust] check_command, nur in der Form eines Slash-Befehls
        (sonst leer: Hinweis auf den Ablauf; eine fremde wiki.toml unterlegt keine Shell-Zeile zum Kopieren)."""
        events: dict[tuple, list] = {}
        for e in self.read_events():
            if e.get("kind") == "verify" and as_datetime(e.get("ts")):
                events.setdefault((e.get("page"), e.get("by")), []).append(e)

        def note_of(pid: str, entry: dict) -> str:
            # die Notiz steht im Ereignis, nicht im verified-Eintrag: das Ereignis zum selben Zeitpunkt
            at = as_datetime(entry["at"])
            gap = lambda e: abs((as_datetime(e["ts"]) - at).total_seconds())
            best = min(reversed(events.get((pid, entry["by"]), [])), key=gap, default=None)   # Gleichstand: das spätere
            return str(best.get("detail") or "") if best and gap(best) <= 300 else ""

        pending, outdated, unhashed, humans = [], [], [], Counter()
        for p in self.pages():
            if p.fm is None or p.fm.get("status") == "deprecated":
                continue
            humans.update(str(v["by"]) for v in as_list(p.fm.get("verified"))
                          if isinstance(v, dict) and str(v.get("by", "")).startswith("human:"))
            state = self.release_state(p)
            if state["check"] and not state["valid"]:
                pending.append({"id": p.id, "check_by": str(state["check"]["by"]),
                                "check_at": str(state["check"]["at"])[:10], "note": note_of(p.id, state["check"]),
                                "renew": state["outdated"], "claims": self.claims_of(p)})
            elif state["outdated"]:
                outdated.append({"id": p.id, "by": str(state["human"]["by"]), "at": str(state["human"]["at"])[:10],
                                 "why": state["why"]})
            if state["unhashed"]:
                unhashed.append({"id": p.id, "by": str(state["machine"]["by"]), "at": str(state["machine"]["at"])[:10]})
        human = str(self.cfg["trust"].get("human") or "")
        if not (human.startswith("human:") and ACTOR_RE.match(human)):
            human = humans.most_common(1)[0][0] if humans else ""
        # Wert aus einer womöglich fremden wiki.toml: nur Slash-Befehle durchlassen, keine Shell-Zeile
        check = str(self.cfg["trust"].get("check_command") or "")
        if not re.fullmatch(r"/[\w:-]+(?: --?[\w-]+)*", check):
            check = ""
        return {"command": f"{self.cli_prefix()} verify", "human": human, "actor": self.actor,
                "check_command": check,
                "pending": pending, "outdated": outdated, "unhashed": unhashed}

    def claims_of(self, page: Page) -> list[dict]:
        """Belegte Aussagen: Absätze und Listenpunkte mit Fußnote (ohne Codeblöcke), Links als Text, je mit dem
        Text ihrer Fußnoten (Quelle und Fundstelle)."""
        def plain(text: str) -> str:
            text = re.sub(r"(?<!!)\[([^\]\n]*)\]\([^)\s]+(?:\s+\"[^\"]*\")?\)", r"\1", text)
            return one_line(re.sub(r"\*\*|__", "", text))

        body = FENCE_RE.sub("", page.body)
        notes = {label: plain(text) for label, text in FOOTNOTE_TEXT_RE.findall(body)}
        items: list[str] = []
        for block in re.split(r"\n\s*\n", FOOTNOTE_TEXT_RE.sub("", body)):
            for line in block.splitlines():
                line = re.sub(r"^\s*>\s?", "", line)
                if LIST_ITEM_RE.match(line) or not items or line.lstrip().startswith("#"):
                    items.append(LIST_ITEM_RE.sub("", line))
                else:
                    items[-1] += " " + line
            items.append("")   # Blockende: der nächste Block beginnt eine neue Aussage
        claims = []
        for item in items:
            labels = list(dict.fromkeys(FOOTNOTE_REF_RE.findall(item)))
            if labels and not item.lstrip().startswith("#"):
                claims.append({"text": plain(item), "notes": [[label, notes.get(label, "")] for label in labels]})
        return claims

    def cli_prefix(self) -> str:
        """Aufruf des Kerns mit --root zum Kopieren, Pfade unter dem Home-Ordner mit ~."""
        def short(path: Path) -> str:
            path, home = path.resolve(), Path.home().resolve()
            if path.is_relative_to(home) and re.fullmatch(r"[\w./-]+", rel := path.relative_to(home).as_posix()):
                return f"~/{rel}"
            return shlex.quote(str(path))
        return f"uv run --no-config --quiet {short(SKILL_DIR / 'okfw.py')} --root {short(self.root)}"

    def dashboard_title(self) -> str:
        """Wiki-Titel ohne den Index-Zusatz „– zuerst lesen“; der gehört in index.md, nicht ins Dashboard."""
        return re.sub(r"\s*[–—-]\s*zuerst lesen\s*$", "", self.cfg["wiki"]["title"], flags=re.I)

    def render_dashboard(self, csp: bool = True) -> str:
        """Das Dashboard als eine Datei. Mit csp trägt sie selbst eine Content-Security-Policy ohne Fremdquellen und
        einen Nonce je Bau an den ausführbaren Skripten; csp=False für `serve`, das Nonce und Kopfzeile selbst setzt."""
        template = (SKILL_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8")
        if csp:
            nonce = secrets.token_urlsafe(16)
            policy = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src data: blob:; "
                      "font-src data:; base-uri 'none'; form-action 'none'")
            template = template.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<meta http-equiv='
                                        f'"Content-Security-Policy" content="{policy}">', 1)
            template = template.replace("<script>", f'<script nonce="{nonce}">')
        data = json.dumps(self.dashboard_data(), ensure_ascii=False).replace("<", "\\u003c")
        title = html.escape(f"{self.dashboard_title()} – Dashboard")
        # `<script` im Design (auch im Kommentar) als CSS-Maskierung: die Datei hat genau eine Daten-Marke
        design = re.sub(r"<(?=script)", r"\\3c ", self.design_css(), flags=re.I)
        parts = {"/*__FONTS__*/": embedded_fonts(), "/*__DESIGN__*/": design, "__TITLE__": title, "__DATA__": data}
        # ein Durchgang über die Vorlage: Eingesetztes (Design, Daten) wird nicht noch einmal ersetzt
        return re.sub("|".join(map(re.escape, parts)), lambda m: parts[m.group(0)], template)

    def reader_html(self) -> dict[str, dict]:
        """{Konzept-ID: {heading, html, refs, apa, open}} für die Leseansicht des Dashboards.
        Belege erscheinen als APA-7-Klammerbelege, refs ist das Literaturverzeichnis der Seite.

        Rohes HTML in Seiten wird als Text gezeigt, interne Links zeigen auf Konzept-IDs,
        `> [!todo]` und `> ⚠️ **Widerspruch:**` werden zu gekennzeichneten Kästen, Textdiagramme
        (```text) zu `pre.diagram`. open sind die offenen Stellen der Seite (Widerspruch, ausdrückliches
        „zu prüfen“, [!todo]) mit Anker `offen-N` für den Sprung zur Fundstelle.
        """
        try:
            import markdown
            from markdown.treeprocessors import Treeprocessor
            from markdown.util import AMP_SUBSTITUTE
        except ImportError:
            return {p.id: {"heading": p.title, "html": f"<pre>{html.escape(p.body)}</pre>", "refs": [], "apa": None,
                           "excerpt": "", "outline": [], "open": []}
                    for p in self.pages()}
        wiki, by_path = self, {q.path.resolve(): q for q in self.pages()}
        catalog = self.apa_catalog()
        sources = {q.id for q in self.source_cards()}

        class Rewrite(Treeprocessor):
            page: Page
            cites: list[str]
            spots: list[dict]

            def plain(self, el, skip=("ul", "ol", "blockquote", "pre")) -> str:
                """Text eines Elements ohne Fußnotenzeichen und ohne verschachtelte Listen und Kästen."""
                parts = [el.text or ""]
                for child in el:
                    if not (child.tag in skip or child.tag == "sup" and child.find("a[@class='footnote-ref']") is not None):
                        parts.append(self.plain(child, skip))
                    parts.append(child.tail or "")
                text = re.sub(r"⟦c(\d+)⟧", lambda m: html.unescape(re.sub(r"<[^>]+>", "", cite_at(self.cites, m))),
                              "".join(parts))
                return one_line(text)

            def spot(self, el, kind: str) -> None:
                anchor = f"offen-{len(self.spots) + 1}"
                el.set("id", anchor)
                text = self.plain(el, skip=("pre",))
                if kind == "contra":
                    text = re.sub(r"^\W*Widerspruch:?\s*", "", text)
                if len(text) > 220:
                    text = text[:220].rsplit(" ", 1)[0].rstrip(",;:") + " …"
                self.spots.append({"kind": kind, "id": anchor, "text": text})

            def scan(self, parent) -> None:
                # Widersprüche und Prüfvermerke außerhalb der Kästen, je Absatz, Listenpunkt oder Zelle
                for el in parent:
                    if el.tag in ("pre", "code") or el.tag == "blockquote" and "callout" in el.get("class", "") \
                            or el.tag == "div" and el.get("class") == "footnote":
                        continue
                    if el.tag in ("p", "li", "td", "th", "dd", "h2", "h3", "h4"):
                        text = self.plain(el, skip=("ul", "ol", "blockquote", "pre", "code"))   # Marker in Code zählen nicht
                        if "⚠" in text and "Widerspruch" in text:
                            self.spot(el, "contra")
                            continue
                        if CHECK_RE.search(text) or self.emphasised(el):
                            self.spot(el, "check")
                            continue
                    self.scan(el)

            def split_callouts(self, root) -> None:
                """Teilt Zitate an jedem Absatz, der mit [!todo] oder ⚠️ Widerspruch beginnt: Python-Markdown fasst
                durch Leerzeilen getrennte `>`-Blöcke zu einem Zitat zusammen, gemeint sind getrennte Kästen."""
                from xml.etree import ElementTree

                def starts(el) -> bool:
                    text = (el.text or "").lstrip()
                    return el.tag == "p" and (text.startswith("[!todo]") or text.startswith("⚠")
                                              and "Widerspruch" in self.plain(el, skip=("ul", "ol", "blockquote", "pre", "code"))[:40])

                pairs = [(parent, bq) for parent in root.iter() for bq in parent if bq.tag == "blockquote"]
                for parent, bq in pairs:
                    parts: list[list] = []
                    for child in bq:
                        if not parts or starts(child) and parts[-1]:
                            parts.append([])
                        parts[-1].append(child)
                    if len(parts) < 2:
                        continue
                    at = list(parent).index(bq)
                    parent.remove(bq)
                    for i, part in enumerate(parts):
                        new = ElementTree.Element("blockquote")
                        new.extend(part)
                        new.text = bq.text if i == 0 else "\n"
                        new.tail = bq.tail if i == len(parts) - 1 else "\n"
                        parent.insert(at + i, new)

            def emphasised(self, el) -> bool:
                """**zu prüfen** oder *zu prüfen* im Element selbst, nicht in verschachtelten Listen."""
                for child in el:
                    if child.tag in ("ul", "ol", "blockquote", "pre"):
                        continue
                    if child.tag in ("strong", "em") and re.match(r"\W*zu prüfen\b", self.plain(child), re.I) \
                            or self.emphasised(child):
                        return True
                return False

            def run(self, root):
                linkify(root)
                for a in root.iter("a"):
                    href = a.get("href", "")
                    # <name@beispiel.de>: Python-Markdown legt die Adresse als Zeichenreferenzen ab
                    scheme = link_scheme(href.replace(AMP_SUBSTITUTE, "&"))
                    if scheme in LINK_SCHEMES:
                        a.set("target", "_blank")
                        a.set("rel", "noopener")
                        continue
                    if scheme:   # javascript:, data:, vbscript: … bleibt als Text stehen
                        a.tag = "span"
                        a.attrib.clear()
                        continue
                    if href.startswith("#"):
                        continue
                    resolved = wiki.resolve(self.page.path, href)
                    target = by_path.get(resolved)
                    if target is not None:
                        a.set("href", "#" + target.id)
                        a.set("data-page", target.id)
                    elif resolved in wiki.withheld:
                        # öffentliche Ausgabe: Link auf eine weggelassene Seite wird zu reinem Text
                        a.tag = "span"
                        a.attrib.clear()
                    else:
                        a.set("href", "#")
                        a.set("class", "missing")
                        a.set("title", "Seite gibt es (noch) nicht")
                self.split_callouts(root)
                for bq in root.iter("blockquote"):
                    first = bq.find("p")
                    text = (first.text or "").lstrip() if first is not None else ""
                    if text.startswith("[!todo]"):
                        first.text = text[len("[!todo]"):].lstrip()
                        bq.set("class", "callout todo")
                        self.spot(bq, "todo")
                    elif "⚠" in (t := self.plain(bq, skip=("pre", "code"))) and "Widerspruch" in t:
                        bq.set("class", "callout contra")
                        self.spot(bq, "contra")
                self.scan(root)

        def diagram(m: re.Match) -> str:
            langs = {c.removeprefix("language-") for c in (m.group(1) or "").split()}
            if langs & DIAGRAM_LANGS or not langs and BOX_DRAWING_RE.search(m.group(2)):
                return m.group(0).replace("<pre>", '<pre class="diagram">', 1)
            return m.group(0)

        # „extra“ ohne attr_list und md_in_html: `{: onclick=…}` in einer Seite bliebe sonst kein Text
        md = markdown.Markdown(extensions=["abbr", "def_list", "fenced_code", "footnotes", "tables", "sane_lists"],
                               output_format="html")
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        rewrite = Rewrite(md)
        md.treeprocessors.register(rewrite, "okf_rewrite", 1)
        out = {}
        for p in self.pages():
            body = p.body.lstrip("\n")
            heading = p.title
            m = re.match(r"# +(.+?)\s*\n", body)
            if m:
                heading, body = m.group(1).strip(), body[m.end():]
            if p.id in sources:
                # Quellenkarten: Titel statt Kurzbeleg- oder APA-Überschrift; die Literaturangabe steht
                # in der Leseansicht unten unter „Zitieren“, nicht noch einmal als Zitatblock oben.
                heading = p.title
                body = strip_reference_block(body)
            refs = self.page_refs(p, by_path)
            if self.withheld:
                body, refs = self._drop_withheld(p, body, refs)
            labels = {label: t for label, t, _ in refs if t is not None and t.path.stem in catalog}
            body, cites = self._apa_cites(body, labels, catalog)
            rewrite.page, rewrite.cites, rewrite.spots = p, cites, []
            md.reset()
            try:
                rendered = md.convert(body)
            except RecursionError:
                rendered = f"<pre>{html.escape(body)}</pre>"   # zu tief verschachtelt: als Text statt Abbruch des Baus
            rendered = re.sub(r"⟦c(\d+)⟧", lambda m, c=cites: cite_at(c, m), rendered)
            rendered = DIAGRAM_PRE_RE.sub(diagram, rendered)
            rendered = self.figures_html(rendered)
            literature, seen = [], set()
            for label, target, entry in refs:
                key = target.path.stem if target is not None else None
                if key in seen:
                    continue
                seen.add(key)
                if key in catalog:
                    literature.append({"key": key, "page": target.id, "entry": catalog[key]["entry"],
                                       "access": str((target.fm or {}).get("access") or ""), "order": catalog[key]["order"]})
                else:
                    resource = entry.get("resource", "") if isinstance(entry, dict) else entry
                    literature.append({"key": None, "page": None, "entry": html.escape(str(resource or label)),
                                       "access": "", "order": 10 ** 6, "missing": True})
            literature.sort(key=lambda r: r["order"])
            excerpt, outline = page_preview(rendered, p.description)
            out[p.id] = {"heading": heading, "html": rendered, "refs": literature, "excerpt": excerpt, "outline": outline,
                         "apa": catalog.get(p.path.stem, {}).get("entry") if p.id in sources else None,
                         "open": rewrite.spots}
        return out

    def figures_html(self, rendered: str) -> str:
        """Bilder im gerenderten Text: Schaubilder (`/abbildungen/<name>.svg`) werden bereinigt als `<figure>` mit
        `<figcaption>` eingebettet (die Zeile `*Abb. …*` darunter ist die Bildunterschrift); fehlt die Datei oder wird sie
        abgelehnt, steht „Abbildung nicht eingebunden: <name>“. Jedes andere Bild wird nie geladen, es erscheint als Text."""
        def attr(tag: str, name: str) -> str:
            m = re.search(rf'\b{name}="([^"]*)"', tag)
            return html.unescape(m.group(1)) if m else ""

        def figure(m: re.Match) -> str:
            tag, src = m.group("img"), attr(m.group("img"), "src")
            found = FIGURE_SRC_RE.match(src)
            if not found:
                return m.group(0)
            name, caption = found.group(1), m.group("cap") or m.group("cap2")
            self.figure_count += 1
            try:
                inner = self.figure_svg(name, prefix=f"abb{self.figure_count}-", label=attr(tag, "alt"))[0]
            except FigureError:
                inner = f'<span class="abb-fehlt">Abbildung nicht eingebunden: {html.escape(name)}</span>'
            return (f'<figure class="abbildung"{m.group("attrs")}>{inner}'
                    + (f"<figcaption>{caption}</figcaption>" if caption else "") + "</figure>")

        def as_text(m: re.Match) -> str:
            tag = m.group(0)
            return ('<span class="bildverweis">Bild nicht eingebunden: '
                    + html.escape(attr(tag, "alt") or attr(tag, "src")) + "</span>")

        rendered = re.sub(r"<p(?P<attrs>[^>]*)>\s*(?P<img><img\b[^>]*>)\s*"
                          r"(?:<em>(?P<cap>Abb\.(?:(?!</p>).)*?)</em>\s*)?</p>"
                          r"(?:\s*<p>\s*<em>(?P<cap2>Abb\.(?:(?!</p>).)*?)</em>\s*</p>)?", figure, rendered, flags=re.S)
        return re.sub(r"<img\b[^>]*>", as_text, rendered)

    def public_view(self) -> "Wiki":
        """Dasselbe Wiki ohne die Seiten aus hidden_ids(): Graph, Tabelle, Leseansicht, Übersichten, Kennzahlen,
        Literaturverzeichnis und Vorschauen entstehen dann nur aus öffentlichen Seiten."""
        if not self.cfg["visibility"].get("field") or not as_list(self.cfg["visibility"].get("hidden")):
            raise WikiError("öffentliche Ausgabe braucht [visibility] field und hidden in der wiki.toml")
        if self.visibility_values() is None:
            raise WikiError(f"öffentliche Ausgabe braucht bekannte Werte für {self.cfg['visibility']['field']}: "
                            "[enums] oder [visibility] default in der wiki.toml (lint: visibility_values_missing)")
        hidden = self.hidden_ids()
        view = Wiki(self.root, self.cfg, self.config_path)
        view.public = True
        view._pages = [p for p in self.pages() if p.id not in hidden]
        view.withheld = {p.path.resolve() for p in self.pages() if p.id in hidden}
        return view

    def dashboard_path(self, public: bool = False) -> Path:
        spec = self.cfg["dashboard"]
        if not public:
            return self.root / spec["path"]
        if spec.get("public_path"):
            return self.root / spec["public_path"]
        path = Path(spec["path"])
        return self.root / path.with_name(f"{path.stem}-oeffentlich{path.suffix}")

    def write_dashboard(self, out: str | Path | None = None, public: bool = False) -> Path:
        path = Path(out).expanduser().resolve() if out else self.dashboard_path(public)
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(path, (self.public_view() if public else self).render_dashboard())
        return path

    # ------------------------------------------------------------ Status

    def status(self) -> dict:
        by_type: Counter = Counter()
        tiers: Counter = Counter()
        drafts = stale = todos = 0
        release: Counter = Counter()
        now = dt.datetime.now(dt.timezone.utc)
        for p in self.pages():
            fm = p.fm or {}
            by_type[p.type or "?"] += 1
            drafts += fm.get("status") == "draft"
            when = as_datetime(fm.get("stale_after"))
            stale += bool(when and now >= when)
            todos += CODE_RE.sub("", p.body).count("[!todo]")   # Marker in Code-Spannen und -Blöcken zählen nicht
            # Vertrauensstufe nur aus Vermerken, die zum jetzigen Inhalt passen; verfallene zählen getrennt
            state = self.release_state(p) if p.fm is not None else None
            tiers[state["tier"] if state else "unverified"] += 1
            if state:
                release["valid"] += state["valid"]
                release["outdated"] += state["outdated"]
                release["stable_outdated"] += state["outdated"] and fm.get("status") == "stable"
                if fm.get("status") != "deprecated":   # wie „Zur Freigabe“ im Dashboard
                    release["pending"] += bool(state["check"]) and not state["valid"]
                    release["unhashed"] += state["unhashed"]
        pending = []
        if self.raw and self.raw.is_dir():
            linked = {str((p.fm or {}).get("raw_packet", "")).rstrip("/") for p in self.pages()}
            stems = {p.path.stem for p in self.pages() if p.type == SOURCE_TYPE}
            pending = [d.name for d in sorted(self.raw.iterdir())
                       if d.is_dir() and self.rel(d) not in linked and d.name not in stems]
        events = self.read_events()
        fundweg: Counter = Counter()
        for q in self.source_cards():
            if q.fm is not None:
                fundweg.update([e["art"] for e in self.fundweg_of(q) if e.get("art")][:1] or ["fehlt"])
        scope = Counter(v for v, _ in self.scopes().values())
        return {"bundle": self.rel(self.bundle), "pages": len(self.pages()), "by_type": dict(sorted(by_type.items())),
                "draft": drafts, "stale": stale, "todo": todos, "raw_pending": pending,
                "trust": {k: n for k, n in tiers.items() if k != "outdated"}, "trust_outdated": tiers["outdated"],
                "release": {k: release[k] for k in ("valid", "outdated", "stable_outdated", "pending", "unhashed")},
                "events": len(events), "last_event": next((e["ts"] for e in reversed(events) if is_timestamp(e.get("ts"))), ""),
                "fundweg": dict(fundweg), "scope": dict(scope), "hidden": len(self.hidden_ids())}


# ----------------------------------------------------------------------- CLI


URL_RE = re.compile(r"https?://[^\s<>\"']*[^\s<>\"'.,;:!?)\]]")


def cite_at(cites: list[str], m: re.Match) -> str:
    """Der Beleg zum Platzhalter ⟦cN⟧; einen Platzhalter ohne Beleg hat die Seite selbst geschrieben, er bleibt Text."""
    index = int(m.group(1))
    return cites[index] if index < len(cites) else m.group(0)


def linkify(parent) -> None:
    """Macht nackte URLs in einem Markdown-Elementbaum klickbar (nicht in a, code, pre)."""
    from xml.etree import ElementTree

    def anchors(text):
        pieces, last, out = [], 0, []
        for m in URL_RE.finditer(text):
            pieces.append(text[last:m.start()])
            a = ElementTree.Element("a", {"href": m.group(0)})
            a.text = m.group(0)
            out.append(a)
            last = m.end()
        pieces.append(text[last:])
        for a, tail in zip(out, pieces[1:], strict=True):
            a.tail = tail
        return pieces[0], out

    if parent.tag in ("a", "code", "pre"):
        return
    if parent.text and URL_RE.search(parent.text):
        parent.text, new = anchors(parent.text)
        for i, a in enumerate(new):
            parent.insert(i, a)
    for child in list(parent):
        linkify(child)
        if child.tail and URL_RE.search(child.tail):
            child.tail, new = anchors(child.tail)
            at = list(parent).index(child) + 1
            for i, a in enumerate(new):
                parent.insert(at + i, a)


def strip_reference_block(body: str) -> str:
    """Entfernt einen Zitatblock mit der Literaturangabe am Anfang einer Quellenkarte."""
    lines = body.lstrip("\n").split("\n")
    block = []
    for line in lines:
        if not line.startswith(">"):
            break
        block.append(line)
    text = " ".join(line.lstrip("> ") for line in block)
    if block and "[!todo]" not in text and "⚠" not in text and re.search(r"\(\d{4}[a-z]?\)|doi\.org|APA", text):
        return "\n".join(lines[len(block):]).lstrip("\n")
    return body


RIS_TYPES = {"article-journal": "JOUR", "article": "JOUR", "book": "BOOK", "chapter": "CHAP",
             "paper-conference": "CPAPER", "report": "RPRT", "thesis": "THES", "webpage": "ELEC",
             "post-weblog": "BLOG", "software": "COMP", "dataset": "DATA", "legislation": "STAT",
             "legal_case": "CASE", "standard": "STAND", "manuscript": "UNPB"}


def ris_record(item: dict) -> str:
    """RIS-Eintrag (für Zotero und andere Literaturverwaltungen) aus einem CSL-JSON-Eintrag."""
    def date(value) -> str:
        try:
            parts = value["date-parts"][0]
            y, m, d = [int(x) if x not in (None, "") else None for x in (list(parts) + [None, None])[:3]]
        except (TypeError, ValueError, KeyError, IndexError):
            return ""
        if y is None:
            return ""
        return f"{y}/{m:02d}/{d:02d}/" if d and m else f"{y}/{m:02d}//" if m else f"{y}///"

    lines = [("TY", RIS_TYPES.get(item.get("type", ""), "GEN"))]
    for role, tag in (("author", "AU"), ("editor", "ED")):
        for name in (n for n in as_list(item.get(role)) if isinstance(n, dict)):
            text = name.get("literal") or ", ".join(x for x in (name.get("family"), name.get("given")) if x)
            if text:
                lines.append((tag, text))
    lines.append(("TI", one_line(item.get("title"))))
    for key, tag in (("container-title", "T2"), ("publisher", "PB"), ("publisher-place", "CY"),
                     ("volume", "VL"), ("issue", "IS"), ("number", "M1"), ("genre", "M3"), ("language", "LA")):
        if item.get(key):
            lines.append((tag, one_line(item[key])))
    issued = date(item.get("issued"))
    if issued:
        lines += [("PY", issued.split("/")[0]), ("DA", issued)]
    page = str(item.get("page") or "")
    if page:
        start, _, end = page.replace("–", "-").partition("-")
        lines.append(("SP", start.strip()))
        if end.strip():
            lines.append(("EP", end.strip()))
    if item.get("DOI"):
        lines.append(("DO", str(item["DOI"])))
    url = item.get("URL") or (f"https://doi.org/{item['DOI']}" if item.get("DOI") else "")
    if url:
        lines.append(("UR", str(url)))
    if date(item.get("accessed")):
        lines.append(("Y2", date(item.get("accessed"))))
    lines.append(("ER", ""))
    return "\r\n".join(f"{tag}  - {value}" for tag, value in lines) + "\r\n"


def page_preview(rendered: str, description: str = "", limit: int = 320) -> tuple[str, list[str]]:
    """(Anriss, Gliederung) aus dem gerenderten HTML einer Seite für Vorschauen im Dashboard.

    Anriss ist der erste eigene Absatz außerhalb von Zitat- und Hinweiskästen, der nicht nur die
    Beschreibung wiederholt; Gliederung sind die H2-Überschriften.
    """
    def text(fragment: str) -> str:
        return one_line(html.unescape(re.sub(r"<[^>]+>", "", fragment)))

    outline = [text(m) for m in re.findall(r"<h2[^>]*>(.*?)</h2>", rendered, re.S)][:8]
    body = re.sub(r"<blockquote.*?</blockquote>|<div class=\"footnote\">.*?</div>", "", rendered, flags=re.S)
    excerpt = ""
    for para in re.findall(r"<p>(.*?)</p>", body, re.S):
        candidate = text(para)
        if candidate and candidate != one_line(description):
            excerpt = candidate
            break
    if len(excerpt) > limit:
        excerpt = excerpt[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " …"
    return excerpt, outline


def embedded_fonts() -> str:
    """@font-face-Regeln mit eingebetteten Schriften aus templates/fonts/ (SIL OFL 1.1, siehe README dort).

    Davor steht ein CSS-Kommentar mit Copyright-Vermerk und Lizenzhinweis (Name, Link) im Klartext; in den
    base64-Daten wären sie für Lesende nicht zu sehen. Eine Schrift ohne ihren Lizenztext wird nicht eingebettet."""
    import base64
    fonts = SKILL_DIR / "templates" / "fonts"
    faces = [("IBM Plex Serif", 400, "ibmplexserif-regular.woff2", "OFL-IBM-Plex.txt"),
             ("IBM Plex Sans", 400, "ibmplexsans-regular.woff2", "OFL-IBM-Plex.txt"),
             ("IBM Plex Sans", 600, "ibmplexsans-semibold.woff2", "OFL-IBM-Plex.txt"),
             ("JetBrains Mono", 400, "jetbrainsmono-regular.woff2", "OFL-JetBrains-Mono.txt")]
    rules, notices = [], {}
    for family, weight, name, license_file in faces:
        font, license_text = fonts / name, fonts / license_file
        # Erste Zeile des Lizenztexts = Copyright-Vermerk des Rechteinhabers; ohne ihn keine Einbettung
        lines = license_text.read_text(encoding="utf-8").splitlines() if license_text.is_file() else []
        notice = lines[0].strip().replace("*/", "* /") if lines else ""
        if not (font.is_file() and notice):
            continue
        if family not in notices.setdefault(notice, []):
            notices[notice].append(family)
        data = base64.b64encode(font.read_bytes()).decode("ascii")
        rules.append(f"@font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; "
                     f"font-display: swap; src: url(data:font/woff2;base64,{data}) format('woff2'); }}")
    if not rules:
        return ""
    lines = [f"   {', '.join(families)}: {notice}" for notice, families in notices.items()]
    comment = ("/* Eingebettete Schriften, unverändert:\n" + "\n".join(lines)
               + "\n   Lizenz: SIL Open Font License 1.1, https://openfontlicense.org */")
    return "\n".join([comment, *rules])


def write_gitattributes(wiki: Wiki) -> list[str]:
    """Trägt in <root>/.gitattributes merge=union für alles ein, was parallele Zweige beide ändern: den Ereignisstrom
    und die Projektionen, die rebuild danach neu erzeugt. Vorhandene Zeilen bleiben. Gibt die neuen Zeilen zurück."""
    bundle = wiki.rel(wiki.bundle)
    paths = [wiki.rel(wiki.events_path), f"{bundle}/**/index.md", f"{bundle}/log.md"]
    paths += [v["path"] for v in wiki.cfg["export"].values() if isinstance(v, dict) and v.get("path")]
    paths = [p.removeprefix("./") for p in paths]
    rules = [(f'"{p}"' if " " in p else p) + " merge=union" for p in paths]
    path = wiki.root / ".gitattributes"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = [r for r in rules if r not in old.splitlines()]
    if new:
        note = "# okfw: parallele Zweige hängen hier beide an; nach dem Merge `okfw.py rebuild`, nie von Hand lösen"
        new_text = "\n".join(([] if note in old else [note]) + new) + "\n"
        replace_file(path, old + ("" if not old or old.endswith("\n") else "\n") + new_text)
    return new


def protected_paths(wiki: Wiki) -> list[str]:
    """Was nur der Kern schreibt. Der Guard (hooks/okf_hook.py, ohne PyYAML) schützt dieselben Pfade;
    tests/test_doku_code.py hält beide Listen gleich."""
    paths = [wiki.rel(wiki.events_path), f"{wiki.rel(wiki.bundle)}/**/index.md", f"{wiki.rel(wiki.bundle)}/log.md"]
    if wiki.raw:
        paths.append(f"{wiki.rel(wiki.raw)}/**")
    paths += [v["path"] for v in wiki.cfg["export"].values() if isinstance(v, dict) and v.get("path")]
    paths.append(wiki.cfg["dashboard"]["path"])
    if wiki.cfg["visibility"].get("field"):
        paths.append(wiki.rel(wiki.dashboard_path(public=True)))
    return paths + list(wiki.cfg["paths"].get("protected", []))


def cmd_status(wiki: Wiki, args) -> int:
    s = wiki.status()
    if args.hook:
        counts = ", ".join(f"{k} {v}" for k, v in s["by_type"].items())
        text = [
            f"OKF-v0.2-Wiki unter {wiki.bundle}: {s['pages']} Seiten ({counts}), davon {s['draft']} draft, "
            f"{s['stale']} veraltet, {s['todo']} offene [!todo].",
            f"Letztes Ereignis: {s['last_event'] or 'keins'}.",
            f"Rohquellen ohne Quellenkarte: {', '.join(s['raw_pending'])}." if s["raw_pending"] else "",
            f"{s['hidden']} Seiten nicht öffentlich ([visibility]); öffentliche Seiten nicht auf sie verweisen lassen."
            if s["hidden"] else "",
            f"{s['release']['stable_outdated']} Seiten stable, seit Änderung ungeprüft (lint: verified_outdated)."
            if s["release"]["stable_outdated"] else "",
            f"{s['release']['pending']} Seiten gegengeprüft und zur Freigabe durch einen Menschen (Dashboard „Zur Freigabe“)."
            if s["release"]["pending"] else "",
            f"Vor inhaltlicher Arbeit {wiki.rel(wiki.bundle)}/index.md lesen und im Wiki suchen.",
            f"Nicht direkt bearbeiten: {', '.join(protected_paths(wiki))}.",
            f"Werkzeug: uv run --no-config --quiet {SKILL_DIR / 'okfw.py'} (new, capture, verify, lint vor Abschluss).",
        ]
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": " ".join(t for t in text if t)}},
                         ensure_ascii=False))
        return 0
    print(f"Bundle: {s['bundle']} ({s['pages']} Seiten)")
    print("Seiten nach Typ: " + ", ".join(f"{k} {v}" for k, v in s["by_type"].items()))
    print(f"draft: {s['draft']} · veraltet: {s['stale']} · offene [!todo]: {s['todo']}")
    print("Vertrauen: " + ", ".join(f"{k} {v}" for k, v in sorted(s["trust"].items()))
          + (f" · {s['trust_outdated']} verfallen" if s["trust_outdated"] else ""))
    rel = s["release"]
    if rel["valid"] or rel["outdated"] or rel["pending"]:
        print(f"Freigaben: {rel['valid']} gültig · {rel['outdated']} verfallen"
              + (f" (davon {rel['stable_outdated']} stable, seit Änderung ungeprüft)" if rel["stable_outdated"] else "")
              + f" · {rel['pending']} zur Freigabe")
    if rel["unhashed"]:
        print(f"Gegenprüfung ohne Prüfwert: {rel['unhashed']} Seiten (zählen nicht für eine Freigabe; "
              "lint verified_unhashed)")
    if wiki.raw:
        print("Rohquellen ohne Karte: " + (", ".join(s["raw_pending"]) or "keine"))
    if s["scope"]:
        field = wiki.cfg["scope"]["field"]
        print(f"{wiki.cfg['scope'].get('label') or field}: " + " · ".join(
            f"{wiki.cfg['labels'].get(field, {}).get(v, v)} {n}" for v, n in sorted(s["scope"].items())))
    if wiki.cfg["visibility"].get("field"):
        print(f"{wiki.cfg['visibility'].get('label') or 'Sichtbarkeit'}: {s['hidden']} Seiten fehlen in der "
              "öffentlichen Ausgabe (dashboard --oeffentlich)")
    if set(s["fundweg"]) - {"fehlt"}:
        total = sum(s["fundweg"].values())
        print("Fundweg der Quellen: " + " · ".join(f"{FUNDWEG_ARTEN.get(a, a)} {n}" for a, n in
                                                   sorted(s["fundweg"].items(), key=lambda t: -t[1]))
              + f" (Modellvorschlag {100 * s['fundweg'].get('modellvorschlag', 0) // max(total, 1)} %)")
    print(f"Ereignisse: {s['events']}" + (f", zuletzt {s['last_event']}" if s["last_event"] else "")
          + f" ({wiki.rel(wiki.events_path)})")
    return 0


def cmd_lint(wiki: Wiki, args) -> int:
    findings = wiki.lint()
    counts = Counter(f.severity for f in findings)
    if args.json:
        print(json.dumps({"pages": len(wiki.pages()), "counts": dict(counts),
                          "findings": [f.as_dict() for f in findings]}, ensure_ascii=False, indent=1))
    elif args.summary:
        for (sev, code), n in sorted(Counter((f.severity, f.code) for f in findings).items(),
                                     key=lambda t: (SEVERITY_ORDER[t[0][0]], -t[1])):
            print(f"{sev:5}  {code:24}  {n}")
    else:
        for f in findings:
            print(f"{f.severity:5}  {f.code:24}  {f.path}  {f.message}")
    if not args.json:
        print(f"\n{len(wiki.pages())} Seiten · {counts['error']} Fehler · {counts['warn']} Warnungen · "
              f"{counts['info']} Hinweise")
    return 1 if counts["error"] or (args.strict and counts["warn"]) else 0


def cmd_rebuild(wiki: Wiki, args) -> int:
    changed = wiki.rebuild(check=args.check)
    if args.check:
        for path in changed:
            print(f"veraltet: {wiki.rel(path)}")
        return 1 if changed else 0
    if args.hook:
        # Stop-Hook: Lint-Fehler als eine Zeile für die Person; ob der Abschluss blockiert wird, entscheidet okf_hook.py
        errors = [f for f in wiki.lint() if f.severity == "error"]
        if errors:
            codes = ", ".join(f"{code} {n}" for code, n in Counter(f.code for f in errors).most_common(4))
            out = {"systemMessage": f"okfw: Lint meldet {len(errors)} Fehler ({codes}); "
                                    "vor Abschluss `lint` ausführen und beheben."}
            if args.befunde:
                pages: dict[str, list[str]] = {}
                for f in errors:
                    pages.setdefault(f.path, []).append(f"{f.code}: {f.message}")
                out["befunde"] = [f"{path}: {'; '.join(items)}" for path, items in pages.items()]
            print(json.dumps(out, ensure_ascii=False))
        return 0
    if not args.quiet:
        print(f"Projektionen neu erzeugt: {len(changed)} Datei(en) geändert")
    return 0


def cmd_new(wiki: Wiki, args) -> int:
    csl = None
    if args.csl:
        raw = sys.stdin.read() if args.csl == "-" else Path(args.csl).read_text(encoding="utf-8")
        csl = json.loads(raw)
        csl = csl[0] if isinstance(csl, list) else csl
    extra = {}
    for item in args.set:
        key, _, value = item.partition("=")
        try:
            extra[key] = load_yaml(value) if value else ""
        except yaml.YAMLError as exc:
            raise WikiError(f"--set {key}: kein gültiges YAML: {str(exc).splitlines()[0]}") from exc
    funde, kandidat = [], None
    if args.aus_lauf or args.kandidat:
        if not (args.aus_lauf and args.kandidat):
            raise WikiError("--aus-lauf und --kandidat gehören zusammen")
        if args.type != SOURCE_TYPE or args.fundweg:
            raise WikiError("--aus-lauf gibt es nur für Quellen und ersetzt --fundweg (die Funde bringt der Lauf mit)")
        kandidat, funde, notes = wiki.fundwege_aus_lauf(args.aus_lauf, args.kandidat)
        for note in notes:
            print(f"okfw: Hinweis: {note}", file=sys.stderr)
        if not (args.doi or args.pmid or args.arxiv or args.manuell or args.csl):   # Kennung des Kandidaten
            args.doi, args.pmid, args.arxiv = (kandidat.get(k) or None for k in ("doi", "pmid", "arxiv"))
            args.pmid = None if args.doi else args.pmid
            args.arxiv = None if args.doi or args.pmid else args.arxiv
            if not (args.doi or args.pmid or args.arxiv):
                raise WikiError(f"Kandidat {kandidat.get('nr')} hat weder DOI noch PMID oder arXiv-ID: "
                                "Karte mit --manuell oder --csl anlegen, Fundweg mit `fundweg --aus-lauf`")
            kandidat = None   # die Karte nimmt seine Kennung, es gibt nichts abzugleichen
        else:   # vor dem Abruf: Ist es eine andere Arbeit, sollen weder Netz noch Karte etwas davon sehen
            kandidat_abgleich(kandidat, kennungen(args.doi or (csl or {}).get("DOI"), args.pmid or (csl or {}).get("PMID"),
                                                  args.arxiv, args.url), "die angegebene Karte")
    if (args.at or args.by) and not args.fundweg:
        raise WikiError("--at und --by gehören zu --fundweg")
    lookup = args.doi or args.pmid or args.arxiv
    if (lookup or args.manuell or args.volltext or args.evidenz) and args.type != SOURCE_TYPE:
        raise WikiError("--doi, --pmid, --arxiv, --manuell, --volltext und --evidenz gibt es nur für Quellen")
    if sum(map(bool, (args.doi, args.pmid, args.arxiv, args.manuell, args.csl))) > 1:
        raise WikiError("nur eine Metadatenquelle: --doi, --pmid, --arxiv, --manuell oder --csl")
    if args.trotz_rueckzug and not (args.doi or args.pmid):
        raise WikiError("--trotz-rueckzug gilt nur mit --doi, --pmid oder --aus-lauf mit einer DOI oder PMID")
    src = wiki.cfg["sources"]
    if lookup:
        csl, resource, checked, flags = wiki.fetch_source(args.doi or "", args.pmid or "", args.arxiv or "")
        args.resource = args.resource or resource
        extra[src["checked_field"]] = checked
        if flags:
            extra[src["updates_field"]] = flags
        if not args.arxiv:   # Rückzug, Bedenken, Korrektur: vor dem Schreiben, ohne Treffer-Karte
            check = wiki.check_retraction(doi=str(csl.get("DOI") or args.doi or ""),
                                          pmid=str(args.pmid or csl.get("PMID") or ""), flags=flags)
            for warning in check.pop("warnungen"):
                print(f"okfw: Achtung: {warning}", file=sys.stderr)
            hits = [h for h in check["hinweise"] if notice_kind(h["art"]) in ("rueckzug", "bedenken")]
            if hits and not args.trotz_rueckzug:
                raise RetractionFound("Rückzug oder Bedenken gemeldet, keine Karte angelegt: "
                                      + "; ".join(notice_text(h) for h in hits)
                                      + ". Trotzdem anlegen (mit Vermerk): --trotz-rueckzug")
            if not check["hinweise"]:
                del check["hinweise"]
            if hits:
                check["trotz"] = True
            extra[src["retraction_field"]] = check
    elif args.manuell:
        if not (args.title and args.url):
            raise WikiError("--manuell braucht --titel und --url")
        csl = {"type": args.csl_typ or src["manual_csl_type"], "title": args.title,
               "author": [{"literal": args.autor or "?"}],
               "issued": {"date-parts": [[args.jahr or dt.date.today().year]]}, "URL": args.url,
               "accessed": today_parts()}
        args.resource = args.resource or args.url
    if args.type == SOURCE_TYPE and not args.access and (lookup or args.manuell):
        manual_access = "originaldokument" in wiki.cfg["enums"].get("access", [])
        args.access = "originaldokument" if args.manuell and manual_access else "citation"
    if args.evidenz:
        extra["evidenz"] = args.evidenz
    if args.volltext:
        extra["fulltext"] = wiki.fulltext_block(args.volltext, args.von or args.resource)["fulltext"]
    if args.fundweg:
        if args.type != SOURCE_TYPE:
            raise WikiError("--fundweg gibt es nur für Quellen")
        extra[wiki.cfg["fundweg"].get("field") or "fundweg"] = wiki.fundweg_entry(
            args.fundweg, db=args.db, query=args.query, von=args.fundweg_von, at=args.at, by=args.by)
    if funde:
        extra[wiki.cfg["fundweg"].get("field") or "fundweg"] = funde[0] if len(funde) == 1 else funde
    if kandidat:   # jetzt auch mit dem, was der Abruf an Kennungen gebracht hat
        hinweis = kandidat_abgleich(kandidat, kennungen(args.doi or (csl or {}).get("DOI"),
                                                        args.pmid or (csl or {}).get("PMID"), args.arxiv, args.url),
                                    "die angegebene Karte")
        if hinweis:
            print(f"okfw: Hinweis: {hinweis}", file=sys.stderr)
    path = wiki.new_page(args.type, args.slug, title=args.title or "", description=args.description or "",
                         sources=[s for s in (args.sources or "").split(",") if s],
                         tags=[t for t in (args.tags or "").split(",") if t],
                         access=args.access, resource=args.resource, csl=csl, extra=extra)
    print(f"angelegt: {wiki.rel(path)}")
    return 0


def cmd_capture(wiki: Wiki, args) -> int:
    for warning in wiki.capture_warnings(args.commit, args.license, args.license_url):
        print(f"okfw: Achtung: {warning}", file=sys.stderr)
    packet = wiki.capture(args.slug, args.url, args.file, commit=args.commit, license_name=args.license,
                          license_url=args.license_url, note=args.note, source_path=args.source_path)
    print(f"erfasst: {wiki.rel(packet)} ({len(args.file)} Datei(en))")
    return 0


def cmd_hash(wiki: Wiki, args) -> int:
    print(yaml.safe_dump(wiki.fulltext_block(args.path, args.retrieved_from), allow_unicode=True, sort_keys=False,
                         default_flow_style=False), end="")
    return 0


def cmd_verify(wiki: Wiki, args) -> int:
    release = args.status == "stable" and args.by.startswith("human:")
    unchecked = [p for p in args.pages if release and wiki.release_state(wiki.parse(wiki.page_path(p)))["check"] is None]
    paths = wiki.verify_pages(args.pages, args.by, status=args.status, note=args.note or "", force=args.force)
    for path in paths:
        print(f"verifiziert: {wiki.page_id(path)} durch {args.by}" + (f", status {args.status}" if args.status else ""))
    if unchecked:
        print(f"Achtung: freigegeben ohne Gegenprüfung zum jetzigen Inhalt (--force): {', '.join(unchecked)}",
              file=sys.stderr)
    return 0


def cmd_event(wiki: Wiki, args) -> int:
    wiki.append_event(args.kind, args.page, args.detail or "", by=args.by)
    return 0


def cmd_body(wiki: Wiki, args) -> int:
    if args.nachziehen == (args.file is not None):
        raise WikiError("body braucht genau eins: <datei|-> oder --nachziehen")
    if args.neu and not args.nachziehen:
        raise WikiError("--neu gilt nur mit --nachziehen")
    before = wiki.parse(wiki.page_path(args.page))
    if args.nachziehen:
        path = wiki.body_changed(args.page, args.detail or ("Seite angelegt" if args.neu else "Körper geändert"),
                                 kind="create" if args.neu else "update")
        after = wiki.parse(path)
        print(f"nachgezogen: {wiki.rel(path)} (generated durch {wiki.actor})")
        if (before.fm or {}).get("status") == "stable" and (after.fm or {}).get("status") == "draft":
            print("  status stable → draft: die Freigabe galt dem alten Inhalt; neu prüfen und mit verify freigeben")
        return 0
    body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    path = wiki.replace_body(args.page, body, args.detail or "")
    after = wiki.parse(path)
    if after.raw_fm == before.raw_fm and after.body == before.body:
        print(f"unverändert: {wiki.rel(path)} (gleicher Inhalt)")
        return 0
    print(f"aktualisiert: {wiki.rel(path)}")
    if (before.fm or {}).get("status") == "stable" and (after.fm or {}).get("status") == "draft":
        print("  status stable → draft: die Freigabe galt dem alten Inhalt; neu prüfen und mit verify freigeben")
    return 0


def cmd_sources(wiki: Wiki, args) -> int:
    add = [s for s in (args.add or "").split(",") if s]
    remove = [s for s in (args.remove or "").split(",") if s]
    if not add and not remove:
        raise WikiError("--add a,b oder --remove a,b angeben")
    path, added, notes = wiki.set_sources(args.page, add=add, remove=remove)
    for note in notes:
        print(f"okfw: {note}", file=sys.stderr)
    print(f"{wiki.page_id(path)}: Quellen " + ", ".join([f"+{s}" for s in added] + [f"−{s}" for s in remove] or ["unverändert"]))
    entries = {str(e.get("id")): e for e in as_list(wiki.parse(path).fm.get("sources")) if isinstance(e, dict)}
    if added:
        print("Fußnoten für den Text (Fundstelle wie „S. 4“ oder „Z. 120–135“ in den Fußnotentext):")
        for sid in added:
            card = wiki.resolve(path, str(entries[sid]["resource"]))
            print(f"  [^{sid}]: [{wiki.parse(card).title}]({entries[sid]['resource']})")
    return 0


def cmd_fundweg(wiki: Wiki, args) -> int:
    if args.aus_lauf or args.kandidat:
        if not (args.aus_lauf and args.kandidat):
            raise WikiError("--aus-lauf und --kandidat gehören zusammen")
        if args.art or args.db or args.query or args.von or args.at or args.by:
            raise WikiError("--aus-lauf bringt Art, Datenbank, Suchstring, Zeitpunkt und Akteur aus dem Lauf mit")
        kandidat, entries, notes = wiki.fundwege_aus_lauf(args.aus_lauf, args.kandidat)
        karte = wiki.parse(wiki.page_path(args.page))
        hinweis = kandidat_abgleich(kandidat, wiki.source_keys(karte), f"Karte {karte.id}")
        for note in notes + [hinweis] * bool(hinweis):
            print(f"okfw: Hinweis: {note}", file=sys.stderr)
        vorher = wiki.fundweg_of(karte)
        path = wiki.set_fundwege(args.page, entries, append=args.add)
        neu = [e for e in entries if e not in vorher] if args.add else entries
        print(f"{wiki.page_id(path)}: {len(neu)} Fundweg(e) aus Lauf, Kandidat {kandidat.get('nr')}"
              + (" (ergänzt)" if neu and args.add else " (standen schon da, unverändert)" if args.add else ""))
        return 0
    if not args.art:
        raise WikiError("--art oder --aus-lauf mit --kandidat angeben")
    entry = wiki.fundweg_entry(args.art, db=args.db, query=args.query, von=args.von, at=args.at, by=args.by)
    path = wiki.set_fundweg(args.page, entry, append=args.add)
    print(f"{wiki.page_id(path)}: Fundweg {FUNDWEG_ARTEN[args.art]}" + (" (ergänzt)" if args.add else ""))
    return 0


def cmd_abbildung(wiki: Wiki, args) -> int:
    if not SLUG_RE.fullmatch(args.name):
        raise WikiError(f"Ungültiger Name {args.name!r}: nur kebab-case (a-z, 0-9, -)")
    source = Path(args.file).expanduser()
    if not source.is_file():
        raise WikiError(f"Keine Datei: {source}")
    if source.stat().st_size > FIGURE_MAX_BYTES:
        raise WikiError(f"{source} ist größer als {FIGURE_MAX_BYTES // 1024} KB: Abbildung vereinfachen")
    try:
        svg, removed = clean_svg(source.read_bytes())
    except FigureError as exc:
        raise WikiError(f"{source} abgelehnt: {exc}") from exc
    target = wiki.figure_file(args.name)
    if target.is_symlink() or not wiki._in_bundle(target):
        raise WikiError(f"{target} ist ein Symlink oder liegt außerhalb des Bundles: nicht geschrieben")
    existed = target.exists()
    if existed and not args.ersetzen:
        raise WikiError(f"{wiki.rel(target)} existiert schon; mit --ersetzen überschreiben")
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_file(target, svg + "\n")
    wiki.append_event("update" if existed else "create", f"{FIGURES_DIR}/{args.name}",
                      f"Abbildung {wiki.rel(target)} ({target.stat().st_size} Byte)")
    if removed:
        print(f"okfw: Achtung: aus dem SVG entfernt: {', '.join(removed)}", file=sys.stderr)
    print(f"abgelegt: {wiki.rel(target)} ({target.stat().st_size} Byte)\n"
          f"Einbinden: ![Titel](/{FIGURES_DIR}/{args.name}.svg)\n"
          "           *Abb.: Was sie zeigt. Eigene Darstellung nach Müller[^mueller].*")
    return 0


RIGHT_WORDS = {"ja": "erlaubt", "bedingt": "mit Auflage", "nein": "nicht erlaubt", "fragen": "erst fragen"}


def cmd_rechte(wiki: Wiki, args) -> int:
    cards = [p for p in wiki.source_cards() if p.fm is not None]
    if args.page:
        page = wiki.parse(wiki.page_path(args.page))
        lic, rights, stored = wiki.license_of(page), wiki.rights_of(page), wiki.fulltext_locations(page)
        if args.json:
            print(json.dumps({"page": page.id, "license": lic, "stored": stored,
                              "rights": {a: {"value": v, "note": n} for a, (v, n) in rights.items()}},
                             ensure_ascii=False, indent=1))
            return 0
        print(f"{page.title}\nLizenz: {lic['name'] or '–'}" + (f" ({lic['url']})" if lic["url"] else "")
              + f"\nKlasse: {lic['label']}")
        for s in stored:
            print(f"Volltext: {s['path']} ({'von Git versioniert' if s['tracked'] else 'nicht versioniert'})")
        for a, (value, note) in rights.items():
            print(f"  {RIGHTS_ACTIONS[a]:42} {RIGHT_WORDS[value]}" + (f" – {note}" if note else ""))
        return 0
    if args.aktion:
        if args.aktion not in RIGHTS_ACTIONS:
            raise WikiError(f"--aktion: eine von {', '.join(RIGHTS_ACTIONS)}")
        groups: dict[str, list[str]] = {v: [] for v in RIGHT_VALUES}
        for p in cards:
            groups[wiki.rights_of(p)[args.aktion][0]].append(p.id)
        if args.json:
            print(json.dumps(groups, ensure_ascii=False, indent=1))
            return 0
        print(f"{RIGHTS_ACTIONS[args.aktion]} ({len(cards)} Quellen)")
        for value, ids in groups.items():
            if ids:
                print(f"\n{RIGHT_WORDS[value]} ({len(ids)}):\n  " + "\n  ".join(sorted(ids)))
        return 0
    by_class = Counter(wiki.license_of(p)["cls"] for p in cards)
    matrix = wiki.rights_matrix()
    if args.json:
        print(json.dumps({"classes": dict(by_class), "matrix": {c: {a: list(v) for a, v in matrix[c].items()}
                                                               for c in by_class}}, ensure_ascii=False, indent=1))
        return 0
    print(f"Lizenzen und Nutzungsrechte ({len(cards)} Quellen)")
    for cls in LICENSE_CLASSES:
        if not by_class[cls]:
            continue
        print(f"\n{LICENSE_CLASSES[cls]} ({by_class[cls]})")
        for a, (value, note) in matrix[cls].items():
            print(f"  {RIGHTS_ACTIONS[a]:42} {RIGHT_WORDS[value]}" + (f" – {note}" if note else ""))
    open_ = sorted(p.id for p in cards if wiki.license_of(p)["cls"] == "unbekannt")
    if open_:
        print(f"\nLizenz ungeklärt ({len(open_)}): " + ", ".join(open_))
    return 0


def cmd_lizenz(wiki: Wiki, args) -> int:
    skipped = 0
    if args.missing:
        unknown = [p for p in wiki.source_cards() if p.fm is not None and wiki.license_of(p)["cls"] == "unbekannt"]
        targets = [p for p in unknown if args.force or not wiki.license_named(p)]
        skipped = len(unknown) - len(targets)
    elif args.page:
        targets = [wiki.parse(wiki.page_path(args.page))]
    else:
        raise WikiError("Seite angeben oder --missing")
    if not args.fetch:
        if args.missing or not (args.name or args.cls):
            raise WikiError("--name (mit --url, --class) setzen oder --fetch nutzen")
        if args.cls and args.cls not in LICENSE_CLASSES:
            raise WikiError(f"--class: eine von {', '.join(LICENSE_CLASSES)}")
        page = targets[0]
        lic = dict((page.fm or {}).get("license") or {}) if isinstance((page.fm or {}).get("license"), dict) else {}
        lic.update({k: v for k, v in (("name", args.name), ("url", args.url), ("class", args.cls)) if v})
        lic.update({"checked": dt.date.today().isoformat(), "via": args.via or "manuell"})
        if args.dry_run:
            print(f"{page.id}: {lic}")
        else:
            wiki.set_license(page.id, lic)
            print(f"{page.id}: {lic.get('name') or 'ohne Namen'} → {LICENSE_CLASSES[classify_license(lic, wiki.cfg['licenses'].get('patterns'))[0]]}")
        return 0
    found = 0
    for i, page in enumerate(targets):
        if not args.missing and not args.force and wiki.license_named(page):
            print(f"{page.id}: hat schon eine Lizenz ({wiki.license_of(page)['name']}); --force zum Überschreiben")
            continue
        ids = wiki.identifiers(page)
        if not any(ids):
            print(f"{page.id}: weder DOI noch PMID oder PMCID – Lizenz am Original prüfen und mit --name setzen")
            continue
        if i:
            time.sleep(0.3)
        lic = wiki.lookup_license(*ids)
        if not lic:
            print(f"{page.id}: keine Lizenzangabe bei Crossref, DataCite, Europe PMC oder OpenAlex")
            continue
        found += 1
        cls = LICENSE_CLASSES[classify_license(lic, wiki.cfg["licenses"].get("patterns"))[0]]
        if args.dry_run:
            print(f"{page.id}: {lic['name']} ({lic['via']}) → {cls}")
        else:
            wiki.set_license(page.id, lic, f"Lizenz über {lic['via']}: {lic['name']}")
            print(f"{page.id}: {lic['name']} ({lic['via']}) → {cls}")
    if args.missing:
        print(f"\n{found} von {len(targets)} Lizenzen gefunden" + (" (nichts geschrieben)" if args.dry_run else ""))
        if skipped:
            print(f"{skipped} benannte Lizenz(en) ohne Klasse übersprungen (`lizenz <seite> --class …` oder `--force`)")
    return 0


def cmd_dashboard(wiki: Wiki, args) -> int:
    path = wiki.write_dashboard(args.out, public=args.public)
    if args.public:
        hidden = wiki.hidden_ids()
        print(f"Öffentliches Dashboard: {path} ({len(wiki.pages()) - len(hidden)} Seiten, {len(hidden)} weggelassen)")
        for p, targets in wiki.visibility_leaks():
            print(f"  Achtung {p.id} verweist auf verborgene {', '.join(targets)}: Link wird zu Text, Beleg entfällt "
                  "(lint: visibility_leak)", file=sys.stderr)
    else:
        print(f"Dashboard: {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


def cmd_serve(wiki: Wiki, args) -> int:
    if str(SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SKILL_DIR))
    import okfw_serve
    return okfw_serve.run(wiki, sys.modules[__name__], port=args.port, oeffnen=not args.no_open)


def cmd_init(args) -> int:
    if args.config:
        raise WikiError("init legt die wiki.toml im Projektordner an: --root DIR statt --config angeben")
    root = Path(args.root or os.getcwd()).resolve()
    target = root / "wiki.toml"
    if target.exists():
        raise WikiError(f"{target} existiert schon")
    profile = SKILL_DIR / "profiles" / f"{args.profile}.toml"
    if not profile.is_file():
        raise WikiError(f"Profil fehlt: {profile}")
    shutil.copyfile(profile, target)
    wiki = Wiki.load(config=target)
    for spec in wiki.types.values():
        (wiki.bundle / spec["folder"]).mkdir(parents=True, exist_ok=True)
    wiki.append_event("init", ".", f"Bundle angelegt (Profil {args.profile}, okfw {VERSION})")
    wiki.rebuild()
    write_gitattributes(wiki)
    print(f"angelegt: {wiki.rel(target)}, Bundle {wiki.rel(wiki.bundle)}/ – Titel und Typen in wiki.toml anpassen")
    return 0


def cmd_gitattributes(wiki: Wiki, args) -> int:
    new = write_gitattributes(wiki)
    print(f".gitattributes: {len(new)} Zeile(n) ergänzt" if new else ".gitattributes: schon vollständig")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Pfad zur wiki.toml")
    parser.add_argument("--root", help="Projektordner mit der wiki.toml (Standard: aufwärts ab dem aktuellen Ordner)")
    parser.add_argument("--version", action="version", version=f"okfw {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="Kurzstatistik")
    p.add_argument("--hook", action="store_true", help="als SessionStart-Hook-JSON ausgeben")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("lint", help="OKF-Konformität und Profilregeln prüfen")
    p.add_argument("--strict", "--streng", action="store_true", help="Warnungen ebenfalls als Fehler werten")
    p.add_argument("--json", action="store_true")
    p.add_argument("--summary", action="store_true", help="nur Anzahl je Befund-Code")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("rebuild", aliases=["index"], help="index.md, log.md und Exporte neu erzeugen")
    p.add_argument("--check", action="store_true", help="nichts schreiben, veraltete Projektionen melden")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--hook", action="store_true", help="Stop-Hook: still neu erzeugen, Lint-Fehler als systemMessage-JSON")
    p.add_argument("--befunde", action="store_true", help="mit --hook: Lint-Fehler zusätzlich je Seite im JSON (für okf_hook.py)")
    p.set_defaults(func=cmd_rebuild)

    def new_options(p):
        p.add_argument("--title", "--titel")
        p.add_argument("--description", "--beschreibung")
        p.add_argument("--sources", "--quellen", help="Komma-Liste von Quellen-Slugs → sources[] für Fußnoten [^slug]")
        p.add_argument("--tags")
        p.add_argument("--access")
        p.add_argument("--resource")
        p.add_argument("--csl", help="CSL-JSON-Datei oder - für stdin")
        p.add_argument("--set", action="append", default=[], metavar="FELD=WERT", help="weiteres Feld (YAML-Wert)")
        p.add_argument("--doi", help="Quelle: Metadaten aus Crossref")
        p.add_argument("--pmid", help="Quelle: Metadaten aus PubMed (Crossref lückenhaft oder keine DOI)")
        p.add_argument("--arxiv", help="Quelle: Metadaten aus arXiv")
        p.add_argument("--manuell", action="store_true", help="Quelle ohne DOI: braucht --titel und --url")
        p.add_argument("--autor", help="--manuell: Autor oder Institution (als literal)")
        p.add_argument("--jahr", type=int, help="--manuell: Jahr")
        p.add_argument("--url", help="--manuell: Fundort")
        p.add_argument("--csl-typ", help="--manuell: CSL-Typ (Standard: [sources] manual_csl_type)")
        p.add_argument("--volltext", help="Quelle: lokaler Volltext (Pfad relativ zum Projekt) → fulltext-Block")
        p.add_argument("--evidenz", help="Quelle: Evidenzart laut Profil ([enums] evidenz)")
        p.add_argument("--fundweg", choices=list(FUNDWEG_ARTEN), help="wie die Quelle gefunden wurde")
        p.add_argument("--db", help="Fundweg: Datenbank oder Werkzeug, z. B. pubmed, crossref, tooluniverse:PubMed_search")
        p.add_argument("--query", help="Fundweg: exakter Suchstring")
        p.add_argument("--fundweg-von", help="Fundweg zitatverfolgung: Slug der Quelle, deren Literatur verfolgt wurde")
        p.add_argument("--von", help="mit --volltext: Abruf-URL des Volltexts")
        p.add_argument("--at", help="mit --fundweg: Zeitpunkt der Suche (ISO 8601 mit Offset), Standard: jetzt")
        p.add_argument("--by", help="mit --fundweg: Akteur der Suche, Standard: [wiki] actor")
        p.add_argument("--aus-lauf", help="Quelle: Ordner einer Recherche mit kandidaten.json; Kennung und alle Funde "
                                          "des Kandidaten (--kandidat) kommen von dort")
        p.add_argument("--kandidat", help="mit --aus-lauf: Nr, DOI oder PMID des Kandidaten")
        p.add_argument("--trotz-rueckzug", action="store_true",
                       help="mit --doi/--pmid: auch bei Rückzug oder Bedenken anlegen, mit Vermerk")
        p.set_defaults(func=cmd_new)

    p = sub.add_parser("new", help="Seite aus Vorlage anlegen")
    p.add_argument("type")
    p.add_argument("slug")
    new_options(p)

    p = sub.add_parser("new-source", help="Quellenkarte anlegen (= new source)")
    p.add_argument("slug")
    new_options(p)
    p.set_defaults(type=SOURCE_TYPE)

    p = sub.add_parser("fundweg", help="Fundweg einer Quelle setzen oder ergänzen")
    p.add_argument("page")
    p.add_argument("--art", choices=list(FUNDWEG_ARTEN))
    p.add_argument("--db")
    p.add_argument("--query")
    p.add_argument("--von")
    p.add_argument("--at", help="Zeitpunkt (ISO 8601 mit Offset), Standard: jetzt")
    p.add_argument("--by", help="Akteur, Standard: [wiki] actor")
    p.add_argument("--add", action="store_true", help="weiteren Fundweg anhängen statt ersetzen")
    p.add_argument("--aus-lauf", help="Ordner einer Recherche mit kandidaten.json: alle Funde des Kandidaten (--kandidat) "
                                      "mit ihrem echten Zeitpunkt, db, query und by")
    p.add_argument("--kandidat", help="mit --aus-lauf: Nr, DOI oder PMID des Kandidaten")
    p.set_defaults(func=cmd_fundweg)

    p = sub.add_parser("abbildung", help="SVG-Schaubild prüfen, bereinigen und unter <bundle>/abbildungen/ ablegen")
    p.add_argument("name", help="Dateiname ohne .svg, kebab-case")
    p.add_argument("--file", required=True, help="SVG-Datei, höchstens 200 KB")
    p.add_argument("--ersetzen", action="store_true", help="eine vorhandene Abbildung gleichen Namens überschreiben")
    p.set_defaults(func=cmd_abbildung)

    p = sub.add_parser("sources", aliases=["quellen"], help="sources[] einer Seite ergänzen oder kürzen")
    p.add_argument("page")
    p.add_argument("--add", help="Komma-Liste von Quellen-Slugs, je auch ID=SLUG (eigene Fußnote auf dieselbe Karte)")
    p.add_argument("--remove", help="Komma-Liste von IDs; nur, wenn der Text sie nicht mehr zitiert")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("capture", help="Rohquelle unveränderlich als Paket ablegen")
    p.add_argument("slug")
    p.add_argument("--url", required=True)
    p.add_argument("--file", action="append", default=[], required=True, metavar="PFAD[=REPO-PFAD]",
                   help="Datei, je Datei auch mit ihrem Pfad im Quell-Repository; mehrfach angeben")
    p.add_argument("--source-path", help="Pfad im Quell-Repository für alle Dateien ohne eigenen REPO-PFAD")
    p.add_argument("--commit")
    p.add_argument("--license")
    p.add_argument("--license-url")
    p.add_argument("--note")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("hash", aliases=["sha"], help="fulltext-Block (path, sha256, retrieved_at) ausgeben")
    p.add_argument("path")
    p.add_argument("--from", dest="retrieved_from")
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("verify", help="Prüfvermerk eintragen (OKF §5.2), auch für mehrere Seiten")
    p.add_argument("pages", nargs="+", metavar="page", help="Konzept-IDs, z. B. concepts/local-tool")
    p.add_argument("--by", required=True)
    p.add_argument("--status", choices=STATUS)
    p.add_argument("--note", help="Notiz für alle Seiten, z. B. „Charge 3“")
    p.add_argument("--force", action="store_true", help="freigeben, auch ohne Gegenprüfung zum jetzigen Inhalt")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("event", help="Ereignis anhängen")
    p.add_argument("kind")
    p.add_argument("page")
    p.add_argument("detail", nargs="?")
    p.add_argument("--by")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("body", help="Körper einer Seite ersetzen, Frontmatter bleibt byte-genau")
    p.add_argument("page")
    p.add_argument("file", nargs="?", help="Markdown-Datei, - für stdin")
    p.add_argument("--nachziehen", action="store_true",
                   help="Körper ist schon geändert (Edit/Write): nur generated nachziehen, stable wie bei body")
    p.add_argument("--neu", action="store_true", help="mit --nachziehen: Seite ist neu (Write), Ereignis create statt update")
    p.add_argument("--detail")
    p.set_defaults(func=cmd_body)

    p = sub.add_parser("rechte", aliases=["rights"], help="Lizenzen und Nutzungsrechte der Quellen anzeigen")
    p.add_argument("page", nargs="?", help="eine Quelle, z. B. sources/x")
    p.add_argument("--aktion", help=f"Quellen nach einem Recht gruppieren: {', '.join(RIGHTS_ACTIONS)}")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rechte)

    p = sub.add_parser("lizenz", aliases=["license"], help="Lizenz einer Quelle setzen oder über APIs suchen")
    p.add_argument("page", nargs="?")
    p.add_argument("--name")
    p.add_argument("--url")
    p.add_argument("--class", dest="cls", help=f"Klasse festlegen: {', '.join(LICENSE_CLASSES)}")
    p.add_argument("--via", help="Herkunft der Angabe: originaldokument, repo-lizenz, github-api … (Standard: manuell)")
    p.add_argument("--fetch", action="store_true", help="über Crossref, DataCite, Europe PMC und OpenAlex suchen (DOI oder PMID)")
    p.add_argument("--missing", action="store_true", help="alle Quellen ohne Lizenzname oder mit unknown")
    p.add_argument("--force", action="store_true",
                   help="vorhandene Lizenz mit Namen überschreiben (mit --missing auch nicht eingeordnete)")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p.set_defaults(func=cmd_lizenz)

    p = sub.add_parser("dashboard", help="HTML-Dashboard mit Netz, Struktur und Befunden schreiben")
    p.add_argument("--out", help="Zieldatei (Standard: [dashboard] path, build/wiki-dashboard.html)")
    p.add_argument("--oeffentlich", "--public", dest="public", action="store_true",
                   help="ohne Seiten aus [visibility] hidden, nach build/wiki-dashboard-oeffentlich.html")
    p.add_argument("--open", action="store_true", help="im Browser öffnen")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("serve", help="Dashboard mit Bearbeiten im Browser, nur auf diesem Rechner")
    p.add_argument("--port", type=int, default=0, help="Port (Standard: ein freier)")
    p.add_argument("--no-open", action="store_true", help="den Browser nicht öffnen, nur die Adresse ausgeben")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gitattributes", help="merge=union für Ereignisstrom und Projektionen in .gitattributes eintragen")
    p.set_defaults(func=cmd_gitattributes)

    p = sub.add_parser("init", help="wiki.toml aus einem Profil anlegen")
    p.add_argument("--profile", default="default")
    p.set_defaults(func=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    hook = (args.cmd == "status" and args.hook) or (args.cmd in ("rebuild", "index") and args.hook)
    quiet = args.cmd in ("rebuild", "index") and args.quiet and not args.hook
    try:
        if args.cmd == "init":
            return cmd_init(args)
        wiki = Wiki.load(config=args.config, root=args.root)
        return args.func(wiki, args)
    except WikiError as exc:
        if (quiet or hook) and isinstance(exc, ConfigMissing):
            return 0   # Hooks in Ordnern ohne Wiki bleiben still
        if hook:
            # Bedienfehler (requires_core, kaputte wiki.toml …) als eine Zeile statt still; Exit 0 blockiert nichts
            out = {"systemMessage": f"okfw: {exc}"}
            if args.cmd == "status":
                out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext":
                                             f"okfw meldet beim Sitzungsstart: {exc}. Vor der Arbeit am Wiki beheben."}
            print(json.dumps(out, ensure_ascii=False))
            return 0
        print(f"okfw: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.stdout, sys.stderr = _Druckbar(sys.stdout), _Druckbar(sys.stderr)
    sys.exit(main())
