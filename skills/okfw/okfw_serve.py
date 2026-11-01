"""okfw serve: dasselbe Dashboard, aber Karten sind bearbeitbar. Nur lokal, nur mit Token, nur über die Funktionen des Kerns.

Der Server liest und schreibt im Bundle. Außerhalb liest er die wiki.toml, die Designdatei im Projektordner und Vorlagen
(neben der wiki.toml oder im Skill) und schreibt nur die Designdatei und die Nutzerdatei von rollen.py.
Seiten-IDs kommen immer aus dem geladenen Wiki, nie aus einem Pfad der Anfrage. Die wiki.toml schreibt der Server nie;
Einstellungen landen in der Nutzerdatei von rollen.py. Keys gibt es in keiner Richtung: nur ihr Zustand.

Das Modul wird vom Kern geladen (`okfw.py serve`) und bekommt dessen Modul übergeben, damit WikiError und Wiki
dieselben Klassen bleiben.
"""

import contextlib
import hmac
import http.cookies
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from html import escape
from pathlib import Path

HIER = Path(__file__).resolve().parent
if str(HIER) not in sys.path:
    sys.path.insert(0, str(HIER))
import rollen  # noqa: E402

MAX_ANFRAGE = 2 * 1024 * 1024
MAX_SVG = 200 * 1024
MAX_CSS = 256 * 1024
MAX_SEITE_FUER_MODELL = 200_000
GESCHUETZT = ("status", "verified", "generated")
COOKIE = "okfw_sitzung"   # Vorsilbe; dahinter eine Kennung je Serverstart
MARKE = '<script type="application/json" id="okf-data">'
VORSCHLAG_ZEIT = 1900   # etwas mehr als das Zeitlimit von rollen.py
AUFTRAG_VORSCHLAG = ("Gib den vollständigen überarbeiteten Körper der Seite zurück, nur Markdown, ohne Frontmatter. "
                     "Fußnoten und Links bleiben erhalten; erfinde keine Quellen.")
HARNESS_KANN = {
    "sitzung": "Die laufende Claude-Code-Sitzung als Unteragent. Der Server startet keinen Prozess und kann sie nicht bedienen.",
    "claude": "claude -p ohne Werkzeuge. Mit Anthropic über die Anmeldung, mit anderen Anbietern über einen Anthropic-kompatiblen Endpunkt.",
    "opencode": "opencode run in einem eigenen Server, nur der gewählte Anbieter, ohne Werkzeuge. OpenAI- und Anthropic-kompatible Endpunkte.",
    "direkt": "Ein HTTP-Aufruf ohne Harness. Schnell und schlank, OpenAI- oder Anthropic-kompatibel.",
    "container": "Nur der Webabruf: web-recherche im Container. Das Modell ist wählbar, der Anbieter fest.",
}
VARIABLEN = {
    "page": "Seitenhintergrund", "surface": "Karten und Eingabefelder", "ink": "Text", "ink-2": "Text, gedämpft",
    "muted": "Nebentext", "grid": "Rasterlinien", "border": "Rahmen der Karten", "accent": "Hauptfarbe (Knöpfe, Auswahl)",
    "accent-hover": "Hauptfarbe unter dem Zeiger", "on-accent": "Schrift auf der Hauptfarbe", "c1": "Seitenart 1",
    "c2": "Seitenart 2", "c3": "Seitenart 3", "c4": "Seitenart 4", "c-other": "weitere Seitenarten",
    "warning": "Warnmarke", "edge": "Kanten im Graphen", "edge-hi": "Kanten, hervorgehoben", "wash": "leichte Tönung",
    "shadow": "Schatten", "code-bg": "Hintergrund von Code im Text", "pre-bg": "Hintergrund von Codeblöcken",
    "pre-ink": "Schrift in Codeblöcken", "font-heading": "Schrift der Überschriften", "font-body": "Schrift im Text",
    "font-mono": "Schrift für Code",
}


class Fehler(Exception):
    """Klartext-Fehler für die Oberfläche; extra kommt mit ins JSON."""

    def __init__(self, text, code=400, extra=None):
        super().__init__(text)
        self.code = code
        self.extra = extra or {}


def design_problem(css: str, core=None) -> str:
    """Wie Lint design_unsafe: nichts, was Fremdes lädt oder das Style-Element verlässt. Der Kern prüft, wenn er kann."""
    if len(css.encode("utf-8")) > MAX_CSS:
        return f"design.css ist größer als {MAX_CSS // 1024} KB"
    if core is not None and hasattr(core, "css_problem"):
        return core.css_problem(css) or ""
    if "</style" in css.lower():
        return "„</style“ ist nicht erlaubt"
    if re.search(r"@import", css, re.I):
        return "@import lädt Fremdes nach und ist nicht erlaubt"
    if re.search(r"image-set\(|expression\(|javascript:|-moz-binding|behavior\s*:", css, re.I):
        return "image-set(), expression(), javascript: und behavior sind nicht erlaubt"
    for m in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", css, re.I | re.S):
        ziel = m.group(2).strip().lower()
        if not (ziel.startswith("#") or ziel.startswith("data:")):
            return f"url({m.group(2).strip()}) zeigt weder auf # noch auf data:"
    return ""


def rollen_aufruf(argv: list[str], arbeitsordner: str) -> tuple[int, str, str]:
    """Der einzige Weg, auf dem eine Seite zu einem Modell geht; die Tests ersetzen ihn."""
    lauf = subprocess.run(argv, capture_output=True, text=True, timeout=VORSCHLAG_ZEIT, cwd=arbeitsordner)
    return lauf.returncode, lauf.stdout, lauf.stderr


class Sitzung:
    """Zustand des laufenden Servers; alle Zugriffe auf das Wiki laufen nacheinander unter lock."""

    def __init__(self, core, config_path: Path, token: str):
        self.core = core
        self.config_path = Path(config_path)
        self.token = token
        self.session = secrets.token_urlsafe(24)
        # Cookies kennen keine Ports: Ohne eigenen Pfad bekäme sie jeder Prozess auf derselben Adresse
        self.praefix = secrets.token_urlsafe(12)
        self.cookie = f"{COOKIE}_{secrets.token_hex(4)}"
        self.lock = threading.RLock()
        self.vorschlaege: dict[str, dict] = {}
        self.assets = {"css": (HIER / "templates" / "serve.css").read_text(encoding="utf-8"),
                       "js": (HIER / "templates" / "serve.js").read_text(encoding="utf-8")}

    # ---------------------------------------------------------------- Grundlagen

    def wiki(self):
        return self.core.Wiki.load(config=self.config_path)

    def seite(self, wiki, pid):
        if not isinstance(pid, str) or not pid:
            raise Fehler("id fehlt", 400)
        for p in wiki.pages():
            if p.id == pid:
                return p
        raise Fehler("Seite nicht gefunden", 404)

    def akteur(self) -> str:
        kennung = rollen.person()
        if not kennung:
            raise Fehler("Es ist keine Kennung gesetzt. Trage sie unter Einstellungen → Person ein; "
                         "sie erscheint als human:<kennung> bei Änderungen und Freigaben.", 422)
        akteur = f"human:{kennung}"
        if not self.core.ACTOR_RE.match(akteur):
            raise Fehler("Die Kennung ist keine gültige Akteurskennung", 422)
        return akteur

    def stand(self, p) -> dict:
        core = self.core
        fm = None
        if p.fm is not None:
            fm = json.loads(json.dumps(p.fm, ensure_ascii=False, default=str))
        return {"id": p.id, "titel": p.title, "typ": p.type, "hash": core.sha256_of(p.path),
                "body": p.body.strip("\n"), "frontmatter": p.raw_fm, "felder": fm, "fehler": p.error,
                "status": (p.fm or {}).get("status"), "geschuetzt": list(GESCHUETZT), "pruefwertfeld": core.VERIFIED_HASH}

    def befunde(self, wiki, p=None) -> dict:
        findings = wiki.lint()
        zaehler: dict[str, int] = {}
        for f in findings:
            zaehler[f.severity] = zaehler.get(f.severity, 0) + 1
        seite = [f.as_dict() for f in findings if p is not None and f.path == wiki.rel(p.path)]
        return {"befunde": seite, "befunde_gesamt": zaehler}

    def neu_bauen(self, wiki) -> list[str]:
        try:
            wiki.rebuild()
        except (self.core.WikiError, OSError) as exc:
            return [f"Projektionen (index.md, log.md) nicht neu erzeugt: {exc}"]
        return []

    # ---------------------------------------------------------------- Dashboard

    def dashboard(self, nonce: str) -> str:
        with self.lock:
            html = self.wiki().render_dashboard(csp=False)
        if html.count(MARKE) != 1:
            raise Fehler("Die Daten-Marke der Vorlage steht nicht genau einmal in der Seite; steht sie im Design oder in einer Karte?", 500)
        start = "window.OKFW_SERVE = " + json.dumps({"version": self.core.VERSION}) + ";"
        einsatz = f"<style>{self.assets['css']}</style>\n<script>{start}</script>\n<script>{self.assets['js']}</script>\n"
        html = html.replace(MARKE, einsatz + MARKE)
        return re.sub(r"<script(?=[\s>])", f'<script nonce="{nonce}"', html)

    # ---------------------------------------------------------------- Seiten

    def seite_lesen(self, pid):
        with self.lock:
            return 200, self.stand(self.seite(self.wiki(), pid))

    def frontmatter_pruefen(self, p, text: str) -> dict:
        core = self.core
        if p.raw_fm is None or p.fm is None:
            raise Fehler("Das Frontmatter dieser Seite ist nicht lesbar. Bitte von Hand beheben, danach hier weiter.", 422)
        if len(text.encode("utf-8")) > core.MAX_FRONTMATTER_BYTES:
            raise Fehler("Das Frontmatter ist größer als 64 KB", 422)
        if re.search(r"^---[ \t]*$", text, re.M):
            raise Fehler("Eine Zeile „---“ beendet das Frontmatter und darf darin nicht stehen", 422)
        try:
            neu = core.load_yaml(text)
        except core.yaml.YAMLError as exc:
            raise Fehler(f"Kein gültiges YAML: {str(exc).splitlines()[0] if str(exc) else exc}", 422)
        if neu is None:
            neu = {}
        if not isinstance(neu, dict):
            raise Fehler("Das Frontmatter muss eine Zuordnung (Feld: Wert) sein", 422)
        if not all(isinstance(k, str) for k in neu):
            raise Fehler("Feldnamen im Frontmatter müssen Text sein", 422)
        fehlt = object()
        geaendert = [k for k in GESCHUETZT if neu.get(k, fehlt) != p.fm.get(k, fehlt)]
        if geaendert:
            raise Fehler(f"Geschützte Felder lassen sich hier nicht ändern: {', '.join(geaendert)}. status und verified "
                         "setzt nur die Freigabe, generated zieht der Kern nach; der Prüfwert "
                         f"({core.VERIFIED_HASH}) steht in verified.", 422)
        probe = text
        for k in GESCHUETZT:
            probe = core.set_frontmatter_key(probe, k, "okfw-probe")
        try:
            gelesen = core.load_yaml(probe)
        except core.yaml.YAMLError:
            gelesen = None
        if not isinstance(gelesen, dict) or any(gelesen.get(k) != "okfw-probe" for k in GESCHUETZT):
            raise Fehler("status, verified und generated müssen als eigene Zeilen „feld: …“ stehen, sonst kann der Kern "
                         "sie nicht mehr nachziehen", 422)
        return neu

    def seite_speichern(self, data: dict):
        core = self.core
        with self.lock:
            wiki = self.wiki()
            p = self.seite(wiki, data.get("id"))
            if p.path.is_symlink():
                raise Fehler(f"{p.id} ist ein symbolischer Link; der Server folgt ihm nicht. "
                             "Bitte durch eine Datei ersetzen.", 422)
            stand = self.stand(p)
            if data.get("hash") != stand["hash"]:
                raise Fehler("Die Seite hat sich seit dem Laden geändert. Es wurde nichts überschrieben.", 409,
                             {"aktuell": stand})
            body, text = data.get("body"), data.get("frontmatter")
            for name, wert in (("body", body), ("frontmatter", text)):
                if wert is not None and not isinstance(wert, str):
                    raise Fehler(f"{name} muss Text sein")
            if body is None and text is None:
                raise Fehler("Nichts zu speichern: body oder frontmatter fehlt")
            akteur = self.akteur()
            fm_geaendert, neu_fm = False, None
            if text is not None:
                text = text.replace("\r\n", "\n")
                if text and not text.endswith("\n"):
                    text += "\n"
                fm_geaendert = text != (p.raw_fm or "")
                if fm_geaendert:
                    neu_fm = self.frontmatter_pruefen(p, text)
            body_geaendert = body is not None and core.body_sha256(body) != core.body_sha256(p.body)
            if not body_geaendert and not fm_geaendert:
                return 200, {"ok": True, "unveraendert": True, "stand": stand, **self.befunde(wiki, p), "hinweise": []}
            alt_status = (p.fm or {}).get("status")
            wiki.actor = akteur
            if fm_geaendert:
                original = p.path.read_text(encoding="utf-8")
                m = core.FRONTMATTER_RE.match(original)
                if not m:
                    raise Fehler("Die Seite hat keinen Frontmatter-Block", 422)
                p.path.write_text(original[:m.start(1)] + text + original[m.end(1):], encoding="utf-8")
                wiki._pages = None
                if wiki.parse(p.path).fm != neu_fm:
                    p.path.write_text(original, encoding="utf-8")
                    raise Fehler("Das Frontmatter ließ sich nicht sicher schreiben. Nichts geändert.", 500)
            felder = ", ".join(k for k in (neu_fm or {}) if (p.fm or {}).get(k, object()) != neu_fm[k]) if fm_geaendert else ""
            detail = "Im Dashboard bearbeitet"
            if body_geaendert:
                vorschlag = self.vorschlaege.get(p.id) if data.get("vorschlag") else None
                if vorschlag:
                    gleich = core.body_sha256(body) == vorschlag["sha"]
                    detail += (f"; Textvorschlag der Rolle bearbeiten ({vorschlag['harness']}, {vorschlag['anbieter']}, "
                               f"{vorschlag['modell']}) " + ("unverändert übernommen" if gleich else "überarbeitet übernommen"))
                if felder:
                    detail += f"; Eigenschaften: {felder}"
                wiki._pages = None
                wiki.replace_body(p.id, body, detail)
            else:
                wiki._pages = None
                parsed = wiki.parse(p.path)
                wiki._write_frontmatter(p.path, {"generated": wiki.generated_now(parsed.fm)})
                wiki.append_event("update", p.id, f"{detail}; Eigenschaften: {felder or 'nur Schreibweise oder Kommentare'}")
            frisch = self.wiki()
            hinweise = self.neu_bauen(frisch)
            q = self.seite(frisch, p.id)
            if alt_status == "stable" and (q.fm or {}).get("status") == "draft":
                hinweise.append("status stable → draft: Die Freigabe galt dem alten Inhalt. Neu gegenprüfen lassen und "
                                "wieder freigeben.")
            return 200, {"ok": True, "stand": self.stand(q), **self.befunde(frisch, q), "hinweise": hinweise}

    def neue_seite(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            typ, slug, titel, beschr = (data.get(k) for k in ("typ", "slug", "titel", "beschreibung"))
            for name, wert in (("typ", typ), ("slug", slug), ("titel", titel)):
                if not isinstance(wert, str) or not wert.strip():
                    raise Fehler(f"{name} fehlt")
            if typ not in wiki.types:
                raise Fehler(f"Typ {typ!r} gibt es in dieser wiki.toml nicht ({', '.join(wiki.types)})", 422)
            wiki.actor = self.akteur()
            try:
                pfad = wiki.new_page(typ, slug.strip(), title=titel.strip(),
                                     description=(beschr or "").strip() if isinstance(beschr, str) else "")
            except self.core.WikiError as exc:
                raise Fehler(str(exc), 422)
            frisch = self.wiki()
            hinweise = self.neu_bauen(frisch)
            q = self.seite(frisch, wiki.page_id(pfad))
            return 200, {"ok": True, "id": q.id, **self.befunde(frisch, q), "hinweise": hinweise}

    def freigeben(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            ids = data.get("ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) or len(ids) > 500:
                raise Fehler("ids: eine Liste von Seiten-IDs")
            bekannt = {p.id for p in wiki.pages()}
            fremd = [i for i in ids if i not in bekannt]
            if fremd:
                raise Fehler(f"Seite nicht gefunden: {', '.join(fremd)}", 404)
            akteur = self.akteur()
            try:
                wiki.verify_pages(ids, akteur, status="stable", note="Freigabe im Dashboard")
            except self.core.WikiError as exc:   # --force gibt es nur auf der Befehlszeile, das Dashboard gibt nichts ungeprüft frei
                raise Fehler(re.sub(r",? oder mit --force[^.;]*", "; das Dashboard gibt ohne Gegenprüfung nichts frei", str(exc)), 422)
            hinweise = self.neu_bauen(self.wiki())
            return 200, {"ok": True, "freigegeben": list(dict.fromkeys(ids)), "hinweise": hinweise}

    def abbildung(self, data: dict):
        core = self.core
        name, svg = data.get("name"), data.get("svg")
        if not isinstance(name, str) or not isinstance(svg, str):
            raise Fehler("name und svg fehlen")
        name = name.strip().removesuffix(".svg")
        if not core.SLUG_RE.fullmatch(name):
            raise Fehler("Name: nur kleine Buchstaben, Ziffern und Bindestriche (kebab-case)", 422)
        if len(svg.encode("utf-8")) > MAX_SVG:
            raise Fehler(f"Das SVG ist größer als {MAX_SVG // 1024} KB", 422)
        with self.lock:
            akteur = self.akteur()
            ordner = tempfile.mkdtemp(prefix="okfw-abb-")
            try:
                datei = Path(ordner) / f"{name}.svg"
                datei.write_text(svg, encoding="utf-8")
                # ein eigener Prozess: Kein Umgebungswechsel im laufenden Server, kein SystemExit aus der Kommandozeile
                lauf = subprocess.run([sys.executable, core.__file__, "--config", str(self.config_path), "abbildung", name, "--file", str(datei)],
                                      env={**os.environ, "WIKI_ACTOR": akteur}, capture_output=True, text=True, timeout=120)
            finally:
                shutil.rmtree(ordner, ignore_errors=True)
            if lauf.returncode != 0:
                text = (lauf.stderr or lauf.stdout).strip()
                if "Traceback" in text:
                    raise Fehler(f"Der Kern ist beim Ablegen gescheitert: {text.splitlines()[-1]}", 500)
                raise Fehler(text.removeprefix("okfw: ") or "abbildung scheiterte", 422)
            return 200, {"ok": True, "name": name, "pfad": f"/abbildungen/{name}.svg",
                         "einbindung": f"![Titel](/abbildungen/{name}.svg)", "ausgabe": lauf.stdout.strip()}

    # ---------------------------------------------------------------- Einstellungen

    def cfg(self):
        return rollen.lade(self.config_path)

    def einstellungen(self):
        cfg = self.cfg()
        s = rollen.status(cfg)
        s["eigene"] = sorted(rollen.toml_lesen(rollen.nutzerdatei()).get("anbieter", {}))
        s["harnesse_info"] = {hid: {"formen": h["formen"], "programm": h["programm"], "kann": HARNESS_KANN[hid]}
                              for hid, h in rollen.HARNESSE.items()}
        s["rollen_namen"] = rollen.ROLLEN
        s["datenklassen"] = list(rollen.DATENKLASSEN)
        s["anbieter_formen"] = {aid: {f: a[f] for f in rollen.formen(a)} for aid, a in cfg["anbieter"].items()}
        s["anbieter_schluessel"] = {aid: {k: a.get(k, "") for k in ("schluessel", "schluessel_env")}
                                    for aid, a in cfg["anbieter"].items()}
        s["anbieter_eigen_daten"] = {aid: a.get("daten", list(rollen.DATENKLASSEN[:2])) for aid, a in cfg["anbieter"].items()}
        return s

    def einstellungen_lesen(self):
        with self.lock:
            return 200, self.einstellungen()

    def rolle_setzen(self, data: dict):
        with self.lock:
            werte = [data.get(k) for k in ("rolle", "harness", "anbieter", "modell")]
            if not all(isinstance(w, str) for w in werte):
                raise Fehler("rolle, harness, anbieter und modell als Text angeben")
            rolle, harness, anbieter, modell = werte
            if anbieter not in self.cfg()["anbieter"]:
                raise Fehler(f"Unbekannter Anbieter {anbieter!r}", 422)
            rollen.setze_rolle(rolle, harness, anbieter, modell.strip())
            return 200, self.einstellungen()

    def anbieter_setzen(self, data: dict):
        with self.lock:
            aid = data.get("id")
            if not isinstance(aid, str):
                raise Fehler("id fehlt")
            if data.get("entfernen"):
                rollen.entferne_anbieter(aid)
                return 200, self.einstellungen()
            feld = {}
            for k in ("name", "openai", "anthropic", "schluessel", "schluessel_env", "hinweis"):
                wert = data.get(k, "")
                if not isinstance(wert, str):
                    raise Fehler(f"{k} muss Text sein")
                feld[k] = wert.strip()
            daten = data.get("daten")   # fehlt: Standard oder der bisherige Wert; leer: nichts erlaubt
            if daten is not None and (not isinstance(daten, list) or not all(isinstance(d, str) for d in daten)):
                raise Fehler("daten: eine Liste von Datenklassen")
            feld["daten"] = daten
            if not feld["openai"] and not feld["anthropic"]:
                raise Fehler("Mindestens eine Adresse (OpenAI- oder Anthropic-kompatibel) angeben", 422)
            rollen.setze_anbieter(aid, feld)   # vorbefehl lässt die Oberfläche bewusst aus: das wäre ein Programmaufruf
            return 200, self.einstellungen()

    def person_setzen(self, data: dict):
        with self.lock:
            kennung = data.get("kennung")
            if not isinstance(kennung, str):
                raise Fehler("kennung fehlt")
            rollen.setze_person(kennung.strip())
            return 200, self.einstellungen()

    def modelle(self, anbieter):
        with self.lock:
            cfg = self.cfg()
        if not isinstance(anbieter, str) or anbieter not in cfg["anbieter"]:
            raise Fehler("Unbekannter Anbieter", 404)
        try:
            return 200, {"anbieter": anbieter, "modelle": rollen.modelle(cfg, anbieter)}
        except rollen.Fehler as exc:
            raise Fehler(str(exc), 502 if exc.code == rollen.EXIT_HARNESS else 422)

    # ---------------------------------------------------------------- Design

    def design_pfad(self, wiki) -> tuple[Path, str]:
        rel = str(wiki.cfg["dashboard"].get("design") or "design.css")
        pfad = (wiki.root / rel).resolve()
        if not pfad.is_relative_to(wiki.root) or pfad.suffix != ".css":
            raise Fehler("[dashboard] design muss eine .css-Datei im Projektordner sein", 422)
        if (wiki.root / rel).is_symlink():
            raise Fehler(f"{rel} ist ein symbolischer Link; der Server folgt ihm nicht. Bitte durch eine Datei ersetzen.", 422)
        return pfad, rel

    def variablen(self) -> list[dict]:
        text = (HIER / "templates" / "dashboard.html").read_text(encoding="utf-8")
        hell = re.search(r":root\s*\{(.*?)\}", text, re.S)
        dunkel = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', text, re.S)
        lesen = lambda m: dict(re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", m.group(1))) if m else {}
        h, d = lesen(hell), lesen(dunkel)
        return [{"name": f"--{n}", "hell": v.strip(), "dunkel": d.get(n, v).strip(), "bedeutung": VARIABLEN.get(n, "")}
                for n, v in h.items()]

    def design_lesen(self):
        with self.lock:
            wiki = self.wiki()
            pfad, rel = self.design_pfad(wiki)
            css = pfad.read_text(encoding="utf-8") if pfad.is_file() else ""
            return 200, {"css": css, "datei": rel, "vorhanden": pfad.is_file(), "variablen": self.variablen()}

    def design_speichern(self, data: dict):
        css = data.get("css")
        if not isinstance(css, str):
            raise Fehler("css fehlt")
        problem = design_problem(css, self.core)
        if problem:
            raise Fehler(f"Design abgelehnt: {problem}", 422)
        with self.lock:
            wiki = self.wiki()
            pfad, rel = self.design_pfad(wiki)
            if not pfad.parent.is_dir():   # Ordner aus [dashboard] design fehlt: klare Meldung statt 500
                raise Fehler(f"[dashboard] design nennt {rel}, aber der Ordner {pfad.parent.relative_to(wiki.root)} "
                              "fehlt im Projektordner. Bitte anlegen.", 422)
            griff, zwischen = tempfile.mkstemp(dir=pfad.parent, prefix=f".{pfad.name}.", suffix=".tmp")   # folgt keinem Link
            try:
                with os.fdopen(griff, "w", encoding="utf-8") as f:
                    f.write(css)
                os.chmod(zwischen, pfad.stat().st_mode & 0o777 if pfad.is_file() else 0o644)
                os.replace(zwischen, pfad)
            except BaseException:
                Path(zwischen).unlink(missing_ok=True)
                raise
            return 200, {"ok": True, "datei": rel}

    # ---------------------------------------------------------------- Vorschlag eines Modells

    def fussnoten_pruefung(self, alt: str, neu: str, quellen: set[str]) -> list[str]:
        core = self.core
        refs = lambda text: set(core.FOOTNOTE_REF_RE.findall(core.CODE_RE.sub("", text)))
        links = lambda text: set(core.LINK_RE.findall(core.CODE_RE.sub("", text)))
        a, n = refs(alt), refs(neu)
        warnungen = []
        if a - n:
            warnungen.append("Fußnoten verschwunden: " + ", ".join(f"[^{x}]" for x in sorted(a - n)))
        if n - a:
            warnungen.append("Neue Fußnoten: " + ", ".join(f"[^{x}]" for x in sorted(n - a))
                             + ". Das Modell soll keine Quellen erfinden; prüfe jede.")
        ohne = sorted(x for x in n if x not in quellen)
        if ohne:
            warnungen.append("Ohne sources-Eintrag in den Eigenschaften: " + ", ".join(f"[^{x}]" for x in ohne))
        if links(alt) - links(neu):
            warnungen.append("Links verschwunden: " + ", ".join(sorted(links(alt) - links(neu))))
        return warnungen

    def vorschlag(self, data: dict):
        core = self.core
        anweisung = data.get("anweisung")
        if not isinstance(anweisung, str) or not anweisung.strip():
            raise Fehler("anweisung fehlt: Was soll sich an der Seite ändern?")
        if len(anweisung) > 4000:
            raise Fehler("Die Anweisung ist länger als 4000 Zeichen", 422)
        with self.lock:
            wiki = self.wiki()
            p = self.seite(wiki, data.get("id"))
            cfg = self.cfg()
            ziel = rollen.loese(cfg, "bearbeiten")
            if ziel["harness"] == "sitzung":
                raise Fehler("Die Rolle „Bearbeiten per Prompt“ läuft in der Sitzung; der Server kann sie nicht bedienen. "
                             "Stelle sie in den Einstellungen auf claude, opencode oder direkt.", 422)
            if ziel["gesperrt"]:
                raise Fehler(f"Rolle „Bearbeiten per Prompt“: {ziel['gesperrt']}", 422)
            # Standard ist der gespeicherte Text; das Dashboard schickt den Stand im Textfeld mit, damit der Vorschlag darauf aufbaut
            body = data.get("body") if isinstance(data.get("body"), str) else p.body
            body = body.strip("\n")
            if len(body) > MAX_SEITE_FUER_MODELL:
                raise Fehler("Die Seite ist für einen Modellaufruf zu lang", 422)
            warnungen = []
            if p.id in {q.id for q in wiki.source_cards()}:
                wert, hinweis = wiki.rights_of(p).get("ki_auswertung", ("ja", ""))
                if wert == "nein":
                    raise Fehler(f"Rechte: KI-Auswertung ist für diese Quelle nicht erlaubt ({hinweis})", 422)
                if wert != "ja":
                    warnungen.append(f"Rechte: KI-Auswertung nur {core.RIGHT_WORDS.get(wert, wert)} ({hinweis}). "
                                     "Es geht nur diese Seite hinaus, kein Volltext.")
            quellen = {str(e["id"]) for e in core.as_list((p.fm or {}).get("sources")) if isinstance(e, dict) and e.get("id")}
            config = str(self.config_path)
        ordner = tempfile.mkdtemp(prefix="okfw-vorschlag-")
        os.chmod(ordner, 0o700)
        try:
            auftrag, seite = Path(ordner) / "auftrag.md", Path(ordner) / "seite.md"
            auftrag.write_text(f"{AUFTRAG_VORSCHLAG}\n\nAnweisung der Person:\n{anweisung.strip()}\n", encoding="utf-8")
            seite.write_text(body, encoding="utf-8")
            argv = [sys.executable, "-I", str(HIER / "rollen.py"), "--wiki", config, "auftrag", "--rolle", "bearbeiten",
                    "--datei", str(auftrag), "--anhang", str(seite), "--json"]
            code, aus, fehler = rollen_aufruf(argv, ordner)
        finally:
            shutil.rmtree(ordner, ignore_errors=True)
        if code != 0:
            text = (fehler or aus).strip().removeprefix("rollen: ") or f"rollen.py endete mit Exit {code}"
            raise Fehler(text, 502 if code == rollen.EXIT_HARNESS else 422)
        try:
            antwort = json.loads(aus)
            text = str(antwort["text"])
        except (ValueError, KeyError, TypeError):
            raise Fehler("Unerwartete Antwort von rollen.py (kein JSON mit text)", 502)
        text = text.strip()
        m = re.fullmatch(r"```(?:markdown|md)?[ \t]*\n(.*?)\n```", text, re.S)
        if m:
            text = m.group(1).strip()
        if text.startswith("---\n"):
            rest = core.split_frontmatter(text + ("" if text.endswith("\n") else "\n"))
            if rest[0] is not None:
                text = rest[1].strip()
                warnungen.append("Das Modell hat Frontmatter mitgeschickt; es wurde weggelassen.")
        warnungen += self.fussnoten_pruefung(body, text, quellen)
        rolle = {k: antwort.get(k, "") for k in ("harness", "anbieter", "modell")}
        with self.lock:
            self.vorschlaege[p.id] = {"sha": core.body_sha256(text), **rolle}
        return 200, {"vorschlag": text, "warnungen": warnungen, "rolle": rolle,
                     "gesendet": {"seite": p.id, "zeichen": len(body), "anhaenge": 1}, "dauer_s": antwort.get("dauer_s")}


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, adresse, sitzung: Sitzung):
        self.sitzung = sitzung
        super().__init__(adresse, Griff)

    @property
    def hosts(self):
        port = self.server_address[1]
        return (f"127.0.0.1:{port}", f"localhost:{port}")


class Griff(http.server.BaseHTTPRequestHandler):
    server_version = "okfw"
    timeout = 120   # eine Verbindung, die nichts sendet, hält keinen Thread fest

    def log_message(self, *args):
        pass

    # ---------------------------------------------------------------- Antworten

    def kopf(self, code, art, laenge, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", art)
        self.send_header("Content-Length", str(laenge))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def json_antwort(self, code, daten, extra=None):
        roh = json.dumps(daten, ensure_ascii=False).encode("utf-8", "replace")
        self.kopf(code, "application/json; charset=utf-8", len(roh), extra)
        self.wfile.write(roh)

    def fehler(self, code, text, extra=None):
        self.json_antwort(code, {"fehler": text, **(extra or {})})

    # ---------------------------------------------------------------- Prüfungen

    def herkunft_ok(self) -> bool:
        """Host gegen DNS-Rebinding; ein Origin, falls gesendet, muss zu dieser Seite gehören."""
        hosts = self.server.hosts
        kopf = self.headers.get_all("Host") or []
        if len(kopf) != 1 or kopf[0] not in hosts:
            return False
        herkunft = self.headers.get("Origin")
        return herkunft is None or herkunft in {f"http://{h}" for h in hosts}

    def pfad_im_praefix(self, pfad: str) -> str | None:
        """Pfad hinter dem Präfix dieses Serverstarts, sonst None: Außerhalb gibt es hier nichts."""
        basis = f"/{self.server.sitzung.praefix}/"
        return "/" + pfad[len(basis):] if pfad.startswith(basis) else None

    def angemeldet(self) -> bool:
        try:
            jar = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        eintrag = jar.get(self.server.sitzung.cookie)
        return bool(eintrag) and hmac.compare_digest(eintrag.value.encode(), self.server.sitzung.session.encode())

    # ---------------------------------------------------------------- GET

    def do_GET(self):
        sitzung = self.server.sitzung
        teile = urllib.parse.urlsplit(self.path)
        if not self.herkunft_ok():
            return self.fehler(403, "Host oder Herkunft nicht erlaubt")
        pfad = self.pfad_im_praefix(teile.path)
        if pfad is None:
            return self.fehler(404, "Nicht gefunden")
        abfrage = urllib.parse.parse_qs(teile.query)
        token = (abfrage.get("t") or [""])[0]
        if pfad == "/" and token:
            if not hmac.compare_digest(token.encode(), sitzung.token.encode()):
                return self.fehler(403, "Falscher Token. Öffne die Start-URL aus dem Terminal.")
            basis = f"/{sitzung.praefix}/"
            cookie = f"{sitzung.cookie}={sitzung.session}; HttpOnly; SameSite=Strict; Path={basis}"
            self.kopf(302, "text/plain; charset=utf-8", 0, {"Location": basis, "Set-Cookie": cookie})
            return
        if not self.angemeldet():
            return self.fehler(403, "Nicht angemeldet. Öffne die Start-URL aus dem Terminal (sie endet auf ?t=…).")
        try:
            if pfad == "/":
                nonce = secrets.token_urlsafe(16)
                try:
                    roh = sitzung.dashboard(nonce).encode("utf-8", "replace")
                except Exception as exc:  # noqa: BLE001
                    return self.fehlerseite(exc)
                csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'unsafe-inline'; img-src 'self' data: blob:; "
                       "font-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % nonce)
                self.kopf(200, "text/html; charset=utf-8", len(roh), {"Content-Security-Policy": csp})
                self.wfile.write(roh)
                return
            routen = {"/api/seite": lambda: sitzung.seite_lesen((abfrage.get("id") or [""])[0]),
                      "/api/einstellungen": sitzung.einstellungen_lesen,
                      "/api/modelle": lambda: sitzung.modelle((abfrage.get("anbieter") or [""])[0]),
                      "/api/design": sitzung.design_lesen}
            if pfad not in routen:
                return self.fehler(404, "Nicht gefunden")
            code, daten = routen[pfad]()
            self.json_antwort(code, daten)
        except Exception as exc:  # noqa: BLE001
            self.ausnahme(exc)

    # ---------------------------------------------------------------- POST

    def do_POST(self):
        sitzung = self.server.sitzung
        teile = urllib.parse.urlsplit(self.path)
        if not self.herkunft_ok() or self.headers.get("Origin") is None:
            return self.fehler(403, "Host oder Herkunft nicht erlaubt")
        pfad = self.pfad_im_praefix(teile.path)
        if pfad is None:
            return self.fehler(404, "Nicht gefunden")
        if not self.angemeldet():
            return self.fehler(403, "Nicht angemeldet. Öffne die Start-URL aus dem Terminal.")
        if not self.headers.get("X-OKFW"):
            return self.fehler(403, "Kopf X-OKFW fehlt")
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self.fehler(415, "Content-Type muss application/json sein")
        try:
            laenge = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.fehler(411, "Content-Length fehlt")
        if laenge < 0 or laenge > MAX_ANFRAGE:
            return self.fehler(413, f"Anfrage größer als {MAX_ANFRAGE // (1024 * 1024)} MB")
        try:
            daten = json.loads(self.rfile.read(laenge).decode("utf-8"))
        except (ValueError, RecursionError):
            return self.fehler(400, "Kein gültiges JSON")
        if not isinstance(daten, dict):
            return self.fehler(400, "Das JSON muss ein Objekt sein")
        routen = {"/api/seite": sitzung.seite_speichern, "/api/neu": sitzung.neue_seite,
                  "/api/freigabe": sitzung.freigeben, "/api/abbildung": sitzung.abbildung,
                  "/api/einstellungen/rolle": sitzung.rolle_setzen, "/api/einstellungen/anbieter": sitzung.anbieter_setzen,
                  "/api/einstellungen/person": sitzung.person_setzen, "/api/design": sitzung.design_speichern,
                  "/api/vorschlag": sitzung.vorschlag}
        if pfad not in routen:
            return self.fehler(404, "Nicht gefunden")
        try:
            code, antwort = routen[pfad](daten)
            self.json_antwort(code, antwort)
        except Exception as exc:  # noqa: BLE001
            self.ausnahme(exc)

    def ausnahme(self, exc):
        core = self.server.sitzung.core
        if isinstance(exc, Fehler):
            return self.fehler(exc.code, str(exc), exc.extra)
        if isinstance(exc, (core.WikiError, rollen.Fehler)):
            return self.fehler(422, str(exc))
        if isinstance(exc, RecursionError):
            return self.fehler(422, "Die Eingabe ist zu tief verschachtelt")
        if isinstance(exc, UnicodeError):
            return self.fehler(422, "Die Eingabe enthält Zeichen, die sich nicht speichern lassen")
        print(f"okfw serve: {type(exc).__name__}: {exc}", file=sys.stderr)   # Einzelheiten nur ans Terminal
        if isinstance(exc, subprocess.TimeoutExpired):   # z. B. Modellaufruf; str() enthält die Befehlszeile
            return self.fehler(504, "Der Aufruf (z. B. des Modells) hat das Zeitlimit überschritten. "
                                     "Einzelheiten stehen im Terminal.")
        self.fehler(500, f"Interner Fehler: {type(exc).__name__}; Einzelheiten stehen im Terminal")

    def fehlerseite(self, exc):
        """Die Seite selbst ließ sich nicht bauen: Der Browser zeigt einen Satz statt JSON oder Traceback."""
        print(f"okfw serve: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = exc.code if isinstance(exc, Fehler) else 500
        # WikiError und Fehler sind für die Person geschrieben; alles andere könnte Pfade, Befehlszeilen oder
        # Schlüssel zeigen und nennt nur den Typnamen
        text = (escape(str(exc)) if isinstance(exc, (self.server.sitzung.core.WikiError, Fehler))
                else f"{escape(type(exc).__name__)}: Dashboard-Bau fehlgeschlagen; Einzelheiten stehen im Terminal")
        roh = ("<!doctype html><meta charset=\"utf-8\"><title>okfw serve</title><h1>Das Dashboard ließ sich nicht bauen</h1>"
               f"<p>{text}</p><p>Das Wiki ist unverändert. Prüfe die zuletzt geänderte Karte und das Design, "
               "etwa mit <code>okfw.py lint</code>, und lade die Seite neu.</p>").encode("utf-8", "replace")
        self.kopf(code, "text/html; charset=utf-8", len(roh), {"Content-Security-Policy": "default-src 'none'"})
        self.wfile.write(roh)


def bauen(core, config_path, port: int = 0, token: str | None = None) -> Server:
    """Server an 127.0.0.1 binden, ohne ihn zu starten (die Tests nutzen das)."""
    sitzung = Sitzung(core, Path(config_path), token or secrets.token_urlsafe(24))
    return Server(("127.0.0.1", port), sitzung)


def run(wiki, core, port: int = 0, oeffnen: bool = True) -> int:
    if wiki.config_path is None:
        raise core.WikiError("serve braucht eine wiki.toml")
    try:
        server = bauen(core, wiki.config_path, port)
    except OSError as exc:
        raise core.WikiError(f"Port {port} nicht frei ({exc}); --port ändern oder weglassen") from exc
    adresse = f"http://127.0.0.1:{server.server_address[1]}/{server.sitzung.praefix}/?t={server.sitzung.token}"
    print(f"okfw serve: {wiki.dashboard_title()} bearbeiten unter\n  {adresse}\nBeenden mit Ctrl+C. "
          "Nur dieser Rechner kommt heran; die Dashboard-Datei zum Weitergeben bleibt eine Leseansicht.", flush=True)
    if oeffnen:
        with contextlib.suppress(Exception):
            webbrowser.open(adresse)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
