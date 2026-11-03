"""okfw serve: dasselbe Dashboard, aber Karten sind bearbeitbar. Nur lokal, nur mit Token, nur über die Funktionen des Kerns.
Modellaufrufe laufen als Aufträge im Hintergrund (okfw_auftraege.py) und kommen als Vorschlag zurück.

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
import itertools
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
import okfw_auftraege  # noqa: E402
import rollen  # noqa: E402
from okfw_auftraege import Fehler  # noqa: E402

MAX_ANFRAGE = 2 * 1024 * 1024
MAX_SVG = 200 * 1024
MAX_CSS = 256 * 1024
GESCHUETZT = ("status", "verified", "generated")
COOKIE = "okfw_sitzung"   # Vorsilbe; dahinter eine Kennung je Serverstart
MARKE = '<script type="application/json" id="okf-data">'
# Die Schreibschicht in dieser Folge; `render_dashboard` kennt keine davon, die Datei zum Weitergeben bleibt ohne sie
SERVE_CSS = ("serve.css", "auftraege.css")
SERVE_JS = ("serve.js", "auftraege.js", "auftrag_bearbeiten.js", "auftrag_schaubild.js", "auftrag_bild.js", "auftrag_quelle.js")
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
    "font-mono": "Schrift für Code", "font-lesen": "Schrift beim Lesen einer Karte", "schild": "Kopf als Schild",
    "auf-schild": "Schrift auf dem Schild", "linie": "Linie durch die Stationen", "haltestelle": "Füllung der Haltestellen",
    "okfw-dauer": "Dauer der Übergänge", "okfw-kurve": "Verlauf der Übergänge",
}


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
    if re.search(r"image-set\(|expression\(|javascript:|-moz-binding|(?<!scroll-)behavior\s*:", css, re.I):
        return "image-set(), expression(), javascript: und behavior sind nicht erlaubt"
    for m in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", css, re.I | re.S):
        ziel = m.group(2).strip().lower()
        if not (ziel.startswith("#") or ziel.startswith("data:")):
            return f"url({m.group(2).strip()}) zeigt weder auf # noch auf data:"
    return ""


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
        self.auftraege = okfw_auftraege.Auftraege(self)
        self.assets = {"css": "\n".join((HIER / "templates" / n).read_text(encoding="utf-8") for n in SERVE_CSS),
                       "js": [(HIER / "templates" / n).read_text(encoding="utf-8") for n in SERVE_JS]}

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
            wiki = self.wiki()
            html = wiki.render_dashboard(csp=False)
        if html.count(MARKE) != 1:
            raise Fehler("Die Daten-Marke der Vorlage steht nicht genau einmal in der Seite; steht sie im Design oder in einer Karte?", 500)
        start = "window.OKFW_SERVE = " + json.dumps({"version": self.core.VERSION}).replace("<", "\\u003c") + ";"
        skripte = "".join(f"<script>{js}</script>\n" for js in self.assets["js"])
        einsatz = f"<style>{self.assets['css']}</style>\n<script>{start}</script>\n{skripte}"
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
                         "setzen nur Freigabe und Ablösen, generated zieht der Kern nach; der Prüfwert "
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
                vermerk = None
                vorschlag = self.auftraege.herkunft(p.id)
                gleich = bool(vorschlag) and core.body_sha256(body) == vorschlag["sha"]   # auch von Hand hineinkopiert
                if data.get("vorschlag") or gleich:
                    # Ist die Datei des Vorschlags weg, ist nur noch bekannt, dass der Text von einem Modell kam
                    quelle = vorschlag or {"harness": "dashboard", "anbieter": "", "modell": core.MODELL_UNBEKANNT}
                    vermerk = {"harness": quelle["harness"], **({"anbieter": quelle["anbieter"]} if quelle["anbieter"] else {}),
                               "modell": quelle["modell"] or core.MODELL_UNBEKANNT, "rolle": "bearbeiten",
                               "art": "vorschlag" if gleich else "vorschlag-bearbeitet"}
                    detail += (f"; Textvorschlag der Rolle bearbeiten ({quelle['harness']}, {quelle['anbieter']}, "
                               f"{quelle['modell']}) " + ("unverändert übernommen" if gleich else "überarbeitet übernommen"))
                if felder:
                    detail += f"; Eigenschaften: {felder}"
                wiki._pages = None
                wiki.replace_body(p.id, body, detail, modell=vermerk)
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
            bestaetigt = data.get("ohne_gegenpruefung", [])
            if not isinstance(bestaetigt, list) or not all(isinstance(i, str) for i in bestaetigt):
                raise Fehler("ohne_gegenpruefung: eine Liste von Seiten-IDs")
            bekannt = {p.id for p in wiki.pages()}
            fremd = [i for i in ids if i not in bekannt]
            if fremd:
                raise Fehler(f"Seite nicht gefunden: {', '.join(fremd)}", 404)
            abgeloest = sorted(p.id for p in wiki.pages() if p.id in ids and (p.fm or {}).get("status") == "deprecated")
            if abgeloest:   # stable höbe das Ablösen stillschweigend auf; der Weg zurück führt über verify --status draft
                raise Fehler(f"Abgelöst, darum keine Freigabe: {', '.join(abgeloest)}. Es wurde nichts geändert.", 422)
            akteur = self.akteur()
            ohne = [p.id for p in wiki.pages() if p.id in ids and p.fm is not None and wiki.release_state(p)["check"] is None]
            offen = sorted(set(ohne) - set(bestaetigt))
            if offen:   # force nur auf ausdrückliche Antwort, und die gilt genau den genannten Seiten
                raise Fehler(f"Keine Gegenprüfung zum jetzigen Inhalt: {', '.join(offen)}. Es wurde nichts freigegeben.", 409,
                             {"ohne_gegenpruefung": offen})
            try:
                wiki.verify_pages(ids, akteur, status="stable", note="Freigabe im Dashboard", force=bool(ohne))
            except self.core.WikiError as exc:
                raise Fehler(str(exc), 422)
            hinweise = self.neu_bauen(self.wiki())
            return 200, {"ok": True, "freigegeben": list(dict.fromkeys(ids)), "ohne_gegenpruefung": ohne, "hinweise": hinweise}

    # ---------------------------------------------------------------- Ablösen, Löschen, Papierkorb

    def folgen_lesen(self, pid):
        with self.lock:
            wiki = self.wiki()
            p = self.seite(wiki, pid)
            folgen = wiki.folgen(p.id)
            return 200, {"hash": self.core.sha256_of(p.path), "folgen": folgen, "zeilen": self.core.folgen_zeilen(folgen, 15)}

    def unveraendert(self, wiki, data: dict):
        """Die Karte, wie sie beim Fragen war: Die Antwort der Person galt diesem Stand und diesen Folgen."""
        p = self.seite(wiki, data.get("id"))
        if data.get("hash") != self.core.sha256_of(p.path):
            raise Fehler("Die Seite hat sich seit dem Laden geändert. Es wurde nichts geändert.", 409)
        return p

    def abloesen(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            p = self.unveraendert(wiki, data)
            nachfolger, notiz = data.get("nachfolger"), data.get("notiz")
            for name, wert in (("nachfolger", nachfolger), ("notiz", notiz)):
                if wert is not None and not isinstance(wert, str):
                    raise Fehler(f"{name} muss Text sein")
            akteur = self.akteur()
            wiki.actor = akteur
            fertig = wiki.deprecate_page(p.id, akteur, nachfolger=nachfolger or None, note=notiz or "")
            frisch = self.wiki()
            return 200, {"ok": True, **fertig, "hinweise": self.neu_bauen(frisch)}

    def loeschen(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            p = self.unveraendert(wiki, data)
            bestaetigt, roh = data.get("belege_bestaetigt", []), data.get("rohquelle", False)
            if not isinstance(bestaetigt, list) or not all(isinstance(s, str) for s in bestaetigt):
                raise Fehler("belege_bestaetigt: eine Liste von Seiten-IDs")
            if not isinstance(roh, bool):
                raise Fehler("rohquelle: true oder false")
            akteur = self.akteur()
            folgen = wiki.folgen(p.id)
            offen = sorted({b["seite"] for b in folgen["belege"]} - set(bestaetigt))
            if offen:
                raise Fehler(f"Belege brächen, ohne Bestätigung: {', '.join(offen)}. Es wurde nichts gelöscht.", 409,
                             {"folgen": folgen, "zeilen": self.core.folgen_zeilen(folgen, 15)})
            wiki.actor = akteur
            fertig = wiki.delete_page(p.id, akteur, belege_brechen=bool(folgen["belege"]), mit_rohquelle=roh)
            frisch = self.wiki()
            return 200, {"ok": True, "eintrag": fertig["eintrag"], "seite": fertig["seite"], "hinweise": self.neu_bauen(frisch),
                         "befunde_gesamt": self.befunde(frisch)["befunde_gesamt"]}

    def papierkorb_lesen(self):
        with self.lock:
            return 200, {"eintraege": self.wiki().papierkorb()}

    def wiederherstellen(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            akteur = self.akteur()
            wiki.actor = akteur
            fertig = wiki.restore_page(data.get("eintrag"), akteur)
            frisch = self.wiki()
            return 200, {"ok": True, **fertig, "hinweise": self.neu_bauen(frisch),
                         "eintraege": frisch.papierkorb()}

    def abbildung_auskunft(self, wiki, name: str, vorhanden: bool) -> dict:
        """Für den Dialog: ein freier Name und die Seiten, die eine vorhandene Abbildung schon zeigen."""
        if not vorhanden:
            return {"vorhanden": False}
        stamm = re.sub(r"-\d+$", "", name)
        zahl = next(n for n in itertools.count(2) if not wiki.figure_taken(f"{stamm}-{n}"))
        return {"vorhanden": True, "vorschlag": f"{stamm}-{zahl}",
                "genutzt_auf": [p.title or p.id for p in wiki.pages()
                                if any(ref["name"] == name for ref in wiki.figure_refs(p.body))]}

    # ---------------------------------------------------------------- Pfade

    def pfad_stand(self, wiki, p) -> dict:
        stationen, _, meldungen = wiki.pfad_stationen(p)
        seiten = {q.id: q for q in wiki.pages()}
        for s in stationen:
            for e in s["elemente"]:
                if e.get("id"):
                    e["titel"], e["stand"] = seiten[e["id"]].title, wiki.karte_stand(seiten[e["id"]])
        return {"id": p.id, "titel": p.title, "raster": wiki.pfad_raster(p),
                "hash": self.core.sha256_of(p.path), "stationen": stationen, "befunde": wiki.pfad_befunde(p),
                "meldungen": meldungen,
                "karten": [{"id": q.id, "titel": q.title, "typ": q.type or "?"} for q in wiki.pages()
                           if q.fm is not None and q.type != "pfad"]}

    def pfad_finden(self, wiki, pid):
        try:
            p = wiki.pfad_seite(pid)
        except self.core.WikiError as exc:
            raise Fehler(str(exc), 404)
        if p is None:
            raise Fehler("Der Standardpfad steht in keiner Datei. Erst in eine Datei übernehmen, dann bearbeiten.", 404)
        return p

    def pfad_lesen(self, pid):
        with self.lock:
            wiki = self.wiki()
            return 200, self.pfad_stand(wiki, self.pfad_finden(wiki, pid))

    def pfad_speichern(self, data: dict):
        pid, gesehen, stationen = data.get("id"), data.get("hash"), data.get("stationen")
        if not isinstance(pid, str) or not isinstance(gesehen, str) or not isinstance(stationen, list):
            raise Fehler("id, hash und stationen fehlen")
        with self.lock:
            wiki = self.wiki()
            p = self.pfad_finden(wiki, pid)
            if self.core.sha256_of(p.path) != gesehen:
                raise Fehler("Dieser Pfad hat sich seit dem Laden geändert. Es wurde nichts überschrieben.", 409,
                             {"aktuell": self.pfad_stand(wiki, p)})
            wiki.actor = self.akteur()
            try:
                geaendert = wiki.pfad_speichern(p.id, stationen)
            except self.core.WikiError as exc:
                raise Fehler(str(exc), 422)
            frisch = self.wiki()
            hinweise = self.neu_bauen(frisch) if geaendert else []
            return 200, {"ok": True, "geaendert": geaendert, **self.pfad_stand(frisch, self.pfad_finden(frisch, pid)), "hinweise": hinweise}

    def pfad_neu(self, data: dict):
        titel = data.get("titel")
        if not isinstance(titel, str) or not titel.strip():
            raise Fehler("Titel fehlt", 422)
        with self.lock:
            wiki = self.wiki()
            wiki.actor = self.akteur()
            try:
                pfad = wiki.pfad_neu(titel)
            except self.core.WikiError as exc:
                raise Fehler(str(exc), 422)
            frisch = self.wiki()
            hinweise = self.neu_bauen(frisch)
            return 200, {"ok": True, "id": wiki.page_id(pfad), "hinweise": hinweise}

    def pfad_uebernehmen(self, data: dict):
        with self.lock:
            wiki = self.wiki()
            wiki.actor = self.akteur()
            try:
                bericht = wiki.pfad_uebernehmen()
            except self.core.WikiError as exc:
                raise Fehler(str(exc), 422)
            return 200, {"ok": True, **bericht, "hinweise": self.neu_bauen(self.wiki())}

    def abbildung(self, data: dict, rolle: str | None = None, modell: str | None = None):
        """rolle und modell setzt nur ein angenommener Vorschlag, nie eine Anfrage. Mit pruefen nur die Auskunft (bereinigt, Vorschau, Verluste, Namenskonflikt), ohne zu schreiben. Sonst legt der
        Kern ab; eine vorhandene Abbildung nur mit ersetzen, sonst 409 mit derselben Auskunft."""
        core = self.core
        name, svg, ersetzen, pruefen = data.get("name"), data.get("svg"), data.get("ersetzen", False), data.get("pruefen", False)
        if not isinstance(name, str) or not isinstance(svg, str):
            raise Fehler("name und svg fehlen")
        if not isinstance(ersetzen, bool) or not isinstance(pruefen, bool):
            raise Fehler("ersetzen und pruefen sind ja oder nein")
        name = name.strip().removesuffix(".svg")
        if not core.SLUG_RE.fullmatch(name):
            raise Fehler("Name: nur kleine Buchstaben, Ziffern und Bindestriche (kebab-case)", 422)
        try:
            roh = svg.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise Fehler("Das SVG ist kein gültiges UTF-8", 422) from exc
        if len(roh) > MAX_SVG:
            raise Fehler(f"Das SVG ist größer als {MAX_SVG // 1024} KB: Abbildung vereinfachen", 422)
        try:
            vorschau, entfernt = core.clean_svg(roh, prefix="vorschau-")
        except core.FigureError as exc:
            raise Fehler(f"Das SVG wurde abgelehnt: {exc}", 422) from exc
        with self.lock:
            akteur = self.akteur()
            wiki = self.wiki()
            vorhanden = bool(wiki.figure_taken(name))
            if pruefen:
                return 200, {"ok": True, "name": name, "svg": vorschau, **core.svg_verlust(entfernt),
                             **self.abbildung_auskunft(wiki, name, vorhanden)}
            if vorhanden and not ersetzen:
                raise Fehler(f"Es gibt schon eine Abbildung {name}. Ersetzen oder einen anderen Namen wählen.", 409,
                             self.abbildung_auskunft(wiki, name, vorhanden))
            ordner = tempfile.mkdtemp(prefix="okfw-abb-")
            try:
                datei = Path(ordner) / f"{name}.svg"
                datei.write_text(svg, encoding="utf-8")
                # ein eigener Prozess: Kein Umgebungswechsel im laufenden Server, kein SystemExit aus der Kommandozeile
                lauf = subprocess.run([sys.executable, core.__file__, "--config", str(self.config_path), "abbildung", name, "--file", str(datei),
                                       *(["--ersetzen"] if ersetzen else []), *(["--rolle", rolle, "--modell", modell] if rolle else [])],
                                      env={**os.environ, "WIKI_ACTOR": akteur}, capture_output=True, text=True, timeout=120)
            finally:
                shutil.rmtree(ordner, ignore_errors=True)
            if lauf.returncode != 0:
                text = (lauf.stderr or lauf.stdout).strip()
                if "Traceback" in text:
                    raise Fehler(f"Der Kern ist beim Ablegen gescheitert: {text.splitlines()[-1]}", 500)
                raise Fehler(text.removeprefix("okfw: ") or "abbildung scheiterte", 422)
            return 200, {"ok": True, "name": name, "pfad": f"/abbildungen/{name}.svg",
                         "einbindung": f"![Titel](/abbildungen/{name}.svg)", "ausgabe": lauf.stdout.strip(),
                         "ersetzt": vorhanden, **core.svg_verlust(entfernt)}

    def bild_ablegen(self, name: str, roh: bytes, modell: str, nachweis: dict) -> dict:
        """Nur ein angenommener Bildvorschlag ruft das: Der Kern prüft und bereinigt das WebP, legt es ab und schreibt das
        Ereignis mit Rolle bild und der Anfrage. Vorhanden heißt 409, ersetzt wird auf diesem Weg nie."""
        core = self.core
        if not core.SLUG_RE.fullmatch(name):
            raise Fehler("Name: nur kleine Buchstaben, Ziffern und Bindestriche (kebab-case)", 422)
        with self.lock:
            akteur = self.akteur()
            if self.wiki().figure_taken(name):
                raise Fehler(f"Es gibt schon eine Abbildung {name}. Verwirf diese Variante oder benenne die vorhandene um.", 409)
            ordner = tempfile.mkdtemp(prefix="okfw-bild-")
            try:
                datei, anfrage = Path(ordner) / f"{name}.webp", Path(ordner) / "anfrage.json"
                datei.write_bytes(roh)
                anfrage.write_text(json.dumps(nachweis, ensure_ascii=False), encoding="utf-8")
                lauf = subprocess.run([sys.executable, core.__file__, "--config", str(self.config_path), "abbildung", name, "--file", str(datei),
                                       "--rolle", "bild", "--modell", modell, "--anfrage", str(anfrage)],
                                      env={**os.environ, "WIKI_ACTOR": akteur}, capture_output=True, text=True, timeout=120)
            finally:
                shutil.rmtree(ordner, ignore_errors=True)
            if lauf.returncode != 0:
                text = (lauf.stderr or lauf.stdout).strip()
                if "Traceback" in text:
                    raise Fehler(f"Der Kern ist beim Ablegen gescheitert: {text.splitlines()[-1]}", 500)
                raise Fehler(text.removeprefix("okfw: ") or "abbildung scheiterte", 422)
            return {"ok": True, "name": name, "pfad": f"/abbildungen/{name}.webp", "ausgabe": lauf.stdout.strip()}

    def bild_vorschau(self, daten: dict):
        """Für den Dialog „Bild erzeugen“: was gesendet würde, mit Erklärungen, ohne zu senden und ohne zu schreiben."""
        Bild, bildplan = okfw_auftraege.Bild, okfw_auftraege.bildplan
        with self.lock:
            wiki, cfg = self.wiki(), self.cfg()
            ziel = rollen.loese(cfg, "bild")
            stile = wiki.cfg["bild"]["stile"]
            if not str(daten.get("prompt") or "").strip():
                daten = {**daten, "prompt": "…"}   # Worte übernehmen geht auch vor dem Prompt
            plan = Bild.plan(wiki, daten)
            name = daten.get("name")
            belegt = [n for n in Bild.namen(name, plan["varianten"]) if wiki.figure_taken(n)] if isinstance(name, str) and self.core.SLUG_RE.fullmatch(name) else []
        anbieter = cfg["anbieter"].get(ziel["anbieter"], {})
        return 200, {"einstellungen": plan["einstellungen"], "erkannt": plan["erkannt"], "erklaerung": plan["erklaerung"],
                     "hinweise": plan["hinweise"], "sekunden": plan["sekunden"], "gesendet": {"model": ziel["modell"], **plan["nutzlast"]},
                     "rolle": {"modell": ziel["modell"], "anbieter": anbieter.get("name", ziel["anbieter"]), "gesperrt": ziel["gesperrt"]},
                     "datenklasse": cfg["daten"], "belegt": belegt,
                     "formate": {k: f"{b}x{h}" for k, (b, h) in bildplan.FORMATE.items()},
                     "stile": {"eigene": sorted(stile), "eingebaut": list(bildplan.STILE)}}

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

    def modelle(self, anbieter, bild: bool = False):
        with self.lock:
            cfg = self.cfg()
        if not isinstance(anbieter, str) or anbieter not in cfg["anbieter"]:
            raise Fehler("Unbekannter Anbieter", 404)
        try:
            return 200, {"anbieter": anbieter, "modelle": rollen.modelle(cfg, anbieter, bild=bild)}
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


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, adresse, sitzung: Sitzung):
        self.sitzung = sitzung
        super().__init__(adresse, Griff)

    def server_close(self):
        self.sitzung.auftraege.beenden()
        super().server_close()

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
                      "/api/folgen": lambda: sitzung.folgen_lesen((abfrage.get("id") or [""])[0]),
                      "/api/papierkorb": sitzung.papierkorb_lesen,
                      "/api/einstellungen": sitzung.einstellungen_lesen,
                      "/api/modelle": lambda: sitzung.modelle((abfrage.get("anbieter") or [""])[0], (abfrage.get("bild") or [""])[0] == "1"),
                      "/api/design": sitzung.design_lesen,
                      "/api/auftraege": sitzung.auftraege.liste,
                      "/api/auftrag": lambda: sitzung.auftraege.lesen((abfrage.get("id") or [""])[0]),
                      "/api/pfad": lambda: sitzung.pfad_lesen((abfrage.get("id") or [""])[0])}
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
        if pfad == "/api/quelle/datei":
            return self.quelle_datei()
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
                  "/api/auftrag": sitzung.auftraege.starten, "/api/auftrag/abbrechen": sitzung.auftraege.abbrechen,
                  "/api/vorschlag/annehmen": sitzung.auftraege.annehmen, "/api/vorschlag/verwerfen": sitzung.auftraege.verwerfen,
                  "/api/quelle/klaeren": sitzung.auftraege.klaeren, "/api/quelle/karte": sitzung.auftraege.karte_ohne_modell,
                  "/api/abloesen": sitzung.abloesen, "/api/bild/vorschau": sitzung.bild_vorschau,
                  "/api/loeschen": sitzung.loeschen, "/api/wiederherstellen": sitzung.wiederherstellen,
                  "/api/pfad": sitzung.pfad_speichern, "/api/pfad/neu": sitzung.pfad_neu,
                  "/api/pfad/uebernehmen": sitzung.pfad_uebernehmen}
        if pfad not in routen:
            return self.fehler(404, "Nicht gefunden")
        try:
            code, antwort = routen[pfad](daten)
            self.json_antwort(code, antwort)
        except Exception as exc:  # noqa: BLE001
            self.ausnahme(exc)

    def quelle_datei(self):
        """Dateien statt JSON, mit eigener Grenze. Die Prüfungen von do_POST sind hier schon gelaufen."""
        try:
            laenge = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.fehler(411, "Content-Length fehlt")
        if laenge < 0:
            return self.fehler(400, "Content-Length ist ungültig")
        try:
            code, antwort = self.server.sitzung.auftraege.datei_annehmen(self.rfile, laenge, self.headers.get("Content-Type") or "", self.headers.get("X-OKFW-Name") or "")
            self.json_antwort(code, antwort)
        except Exception as exc:  # noqa: BLE001
            self.close_connection = True   # der Rest der Datei kommt nicht mehr an
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
