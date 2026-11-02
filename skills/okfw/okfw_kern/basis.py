"""Grundlagen ohne Fachlogik: Version, Pfade, Fehlerklassen, gemeinsame Muster, Datei- und Zeithelfer, Page und Finding."""

import calendar
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


VERSION = "0.7.1"
SKILL_DIR = Path(__file__).resolve().parent.parent
RESERVED = {"index.md", "log.md"}
STATUS = ("draft", "stable", "deprecated")
SOURCE_TYPE = "source"
# Prüfwert des Seitenkörpers in jedem verified-Eintrag, den `verify` schreibt (Erweiterung zu OKF §5.2)
VERIFIED_HASH = "body_sha256"
SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
ACTOR_RE = re.compile(r"^(human:[\w.-]+|process:[\w.-]+|[\w.-]+/[\w.:-]+)$")
LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
FOOTNOTE_TEXT_RE = re.compile(r"^\[\^([^\]\s]+)\]:[ \t]*(.*)$", re.M)
FENCE_RE = re.compile(r"```.*?```", re.S)
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
H2_RE = re.compile(r"^## +(.+?)\s*$", re.M)
CONTRADICTION_RE = re.compile(r"⚠️\s*\*\*Widerspruch:?\*\*")
# ausdrücklicher Prüfvermerk: „zu prüfen“ am Blockanfang, in Anführungszeichen oder Klammern, nicht im Fließtext
CHECK_RE = re.compile(r"^\W*zu prüfen\b|[„“”\"‚‘(\[]\s*zu prüfen\b|\bzu prüfen\s*[)\]“”\"‘’]", re.I)
# Schaubilder: <bundle>/abbildungen/<name>.svg, eingebunden als ![Titel](/abbildungen/name.svg) und darunter *Abb. …*
FIGURES_DIR = "abbildungen"
PAPIERKORB = ".papierkorb"   # im Bundle; der Punkt hält ihn aus Seiten, Indexen und Lint
AUFTRAEGE = ".auftraege"     # ebenso: Aufträge und Vorschläge von `serve`, mit eigener .gitignore
FIGURE_SRC_RE = re.compile(r"^/abbildungen/([a-z0-9]+(?:-[a-z0-9]+)*)\.svg$")
IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FIGURE_CAPTION_RE = re.compile(r"^\s*\*Abb\.")
# Datenklassen eines Wikis ([rollen] daten); dieselben prüft rollen.py
DATENKLASSEN = ("oeffentlich", "intern", "personenbezogen")


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


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


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
