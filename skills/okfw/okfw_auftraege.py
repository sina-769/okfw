"""Aufträge und Vorschläge für okfw serve: Ein Modell läuft im Hintergrund, sein Ergebnis wartet als Vorschlag auf die Person.

Ein Auftragstyp hat drei Stufen: `pruefen` unter der Sperre (antwortet sofort), `laufen` im Hintergrund ohne Sperre,
`auswerten` unter der Sperre. Die Typen stehen fest in TYPEN und werden nie aus einem Wiki geladen, denn ein Wiki kann fremd sein.
Was ein Modell liefert, sind Daten: Geschrieben wird nur über eine Änderungsart aus ARTEN, und nur, wenn die Person sie annimmt.

Je Auftrag liegt eine JSON-Datei unter <bundle>/.auftraege/ (Punkt: aus Seiten, Indexen und Lint; eigene .gitignore: aus dem
Commit). Beim Laden gelten diese Dateien als ungeprüft, denn auch sie können aus einem fremden Wiki stammen.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

import okfw_quelle as q
import rollen

VERSION = 1
VORSCHLAG_ZEIT = 1900   # etwas mehr als das Zeitlimit von rollen.py
MAX_SEITE_FUER_MODELL = 200_000
MAX_DATEI = 8 * 1024 * 1024
AUFHEBEN_S = 30 * 24 * 3600   # Erledigtes; danach kennt nur noch das Frontmatter die Modellherkunft
AUFTRAG_VORSCHLAG = ("Gib den vollständigen überarbeiteten Körper der Seite zurück, nur Markdown, ohne Frontmatter. "
                     "Fußnoten und Links bleiben erhalten; erfinde keine Quellen.")
AUFTRAG_SCHAUBILD = ("Zeichne ein Schaubild als eigenständiges SVG. Gib nur das SVG zurück, ohne Erklärung. Mit viewBox und dunkler Schrift auf hellem Grund; "
                     "ohne Skripte, Bilder, fremde Schriften und Verweise nach außen. Beschriftungen kurz und als <text>. "
                     "Erfinde keine Zahlen, Namen oder Quellen: Was die Beschreibung nicht nennt, bleibt weg.")
AKTIV = ("wartet", "laeuft")
ZUSTAENDE = (*AKTIV, "fertig", "gescheitert", "abgebrochen")
STATUS = ("offen", "angenommen", "im_textfeld", "verworfen")
ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
ZEITLIMIT_TEXT = "Der Aufruf des Modells hat das Zeitlimit überschritten. Einzelheiten stehen im Terminal."
EINGANG = "eingang"             # Unterordner: abgelegte Dateien, bis über ihren Vorschlag entschieden ist
EINGANG_AUFHEBEN_S = 24 * 3600  # was nie zu einem Auftrag wurde
DATEIARTEN = {"application/pdf": (".pdf", q.MAX_PDF), "text/markdown": (".md", q.MAX_MARKDOWN), "text/plain": (".md", q.MAX_MARKDOWN)}
RECHTE_FRAGEN = ("ki_auswertung", "volltext_ablegen")
EPMC_SUCHE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
API_PY = Path(__file__).resolve().parent.parent / "recherche" / "scripts" / "api.py"


class Fehler(Exception):
    """Klartext-Fehler für die Oberfläche; extra kommt mit ins JSON."""

    def __init__(self, text, code=400, extra=None):
        super().__init__(text)
        self.code = code
        self.extra = extra or {}


class Abgebrochen(Exception):
    """Die Person hat den Auftrag abgebrochen, oder der Server endet."""


def _beenden(proc: subprocess.Popen) -> None:
    """Den Prozess samt Kindern: rollen.py startet opencode oder claude in derselben Prozessgruppe."""
    def senden(zeichen):
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, zeichen)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError):
            pass

    senden(signal.SIGTERM)
    try:
        proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        senden(getattr(signal, "SIGKILL", signal.SIGTERM))
        proc.communicate()


def rollen_aufruf(argv: list[str], arbeitsordner: str, abbruch: threading.Event | None = None) -> tuple[int, str, str]:
    """Der einzige Weg, auf dem eine Seite zu einem Modell geht; die Tests ersetzen ihn."""
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=arbeitsordner, start_new_session=True)
    frist = time.monotonic() + VORSCHLAG_ZEIT
    while True:
        try:
            aus, fehler = proc.communicate(timeout=0.2)
            return proc.returncode, aus, fehler
        except subprocess.TimeoutExpired:
            if abbruch is not None and abbruch.is_set():
                _beenden(proc)
                raise Abgebrochen() from None
            if time.monotonic() > frist:
                _beenden(proc)
                raise subprocess.TimeoutExpired(argv, VORSCHLAG_ZEIT) from None


abruf_aufruf = rollen_aufruf   # Literatur-APIs, kein Modell; die Tests ersetzen beide getrennt


class Lauf:
    """Was `laufen` von der Maschine bekommt."""

    def __init__(self, melde, abbruch: threading.Event):
        self.melde = melde
        self.abbruch = abbruch


# ---------------------------------------------------------------- Auftragstypen

def modell_fragen(eingabe: dict, rolle: str, auftragstext: str, anhaenge: dict[str, str], lauf: Lauf) -> dict:
    """Ein Aufruf von rollen.py in einem privaten Ordner, der danach wieder weg ist. Hinaus geht nur, was hier übergeben wird."""
    ordner = tempfile.mkdtemp(prefix="okfw-vorschlag-")
    os.chmod(ordner, 0o700)
    try:
        auftrag = Path(ordner) / "auftrag.md"
        auftrag.write_text(auftragstext, encoding="utf-8")
        argv = [sys.executable, "-I", str(Path(__file__).resolve().parent / "rollen.py"), "--wiki", eingabe["config"], "auftrag",
                "--rolle", rolle, "--datei", str(auftrag)]
        for name, text in anhaenge.items():
            (Path(ordner) / name).write_text(text, encoding="utf-8")
            argv += ["--anhang", str(Path(ordner) / name)]
        code, aus, fehler = rollen_aufruf([*argv, "--json"], ordner, lauf.abbruch)
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
    if code != 0:
        raise Fehler((fehler or aus).strip().removeprefix("rollen: ") or f"rollen.py endete mit Exit {code}", 502)
    try:
        antwort = json.loads(aus)
        return {**antwort, "text": str(antwort["text"])}
    except (ValueError, KeyError, TypeError):
        raise Fehler("Unerwartete Antwort von rollen.py (kein JSON mit text)", 502) from None


class Bearbeiten:
    """Bearbeiten per Prompt: eine neue Fassung einer Seite. Gesendet wird nur diese Seite, nie ein Volltext."""

    name = "bearbeiten"
    rolle = "bearbeiten"

    def pruefen(self, sitzung, wiki, daten: dict) -> dict:
        core = sitzung.core
        anweisung = daten.get("anweisung")
        if not isinstance(anweisung, str) or not anweisung.strip():
            raise Fehler("anweisung fehlt: Was soll sich an der Seite ändern?")
        if len(anweisung) > 4000:
            raise Fehler("Die Anweisung ist länger als 4000 Zeichen", 422)
        p = sitzung.seite(wiki, daten.get("id"))
        # Standard ist der gespeicherte Text; das Dashboard schickt den Stand im Textfeld mit, damit der Vorschlag darauf aufbaut
        body = daten.get("body") if isinstance(daten.get("body"), str) else p.body
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
        return {"ziel": p.id, "titel": p.title, "anweisung": anweisung.strip(), "body": body, "warnungen": warnungen,
                "quellen": quellen, "basis": core.sha256_of(p.path), "config": str(sitzung.config_path),
                "gesendet": {"seite": p.id, "zeichen": len(body), "anhaenge": 1, "hinweis": "Gesendet wurde nur diese Seite"}}

    def laufen(self, eingabe: dict, lauf: Lauf) -> dict:
        text = f"{AUFTRAG_VORSCHLAG}\n\nAnweisung der Person:\n{eingabe['anweisung']}\n"
        return modell_fragen(eingabe, self.rolle, text, {"seite.md": eingabe["body"]}, lauf)

    def auswerten(self, sitzung, wiki, eingabe: dict, antwort: dict) -> dict:
        core = sitzung.core
        text = antwort["text"].strip()
        warnungen = list(eingabe["warnungen"])
        m = re.fullmatch(r"```(?:markdown|md)?[ \t]*\n(.*?)\n```", text, re.S)
        if m:
            text = m.group(1).strip()
        if text.startswith("---\n"):
            rest = core.split_frontmatter(text + ("" if text.endswith("\n") else "\n"))
            if rest[0] is not None:
                text = rest[1].strip()
                warnungen.append("Das Modell hat Frontmatter mitgeschickt; es wurde weggelassen.")
        warnungen += self.fussnoten_pruefung(core, eingabe["body"], text, eingabe["quellen"])
        return {"warnungen": warnungen,
                "aenderungen": [{"art": "koerper", "ziel": eingabe["ziel"], "basis": eingabe["basis"],
                                 "daten": {"alt": eingabe["body"], "neu": text}}]}

    @staticmethod
    def fussnoten_pruefung(core, alt: str, neu: str, quellen: set[str]) -> list[str]:
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


class Quelle:
    """Quellen hineinkippen: Aus einer abgelegten Datei oder einer DOI werden Quellenkarte und Konzeptkarten als Vorschlag.
    Rechte und Datenklasse klärt `pruefen` am Server; ohne erlaubte KI-Auswertung bleibt `karte_ohne_modell`."""

    name = "quelle"
    rolle = "lesen"

    def pruefen(self, sitzung, wiki, daten: dict) -> dict:
        auftraege = sitzung.auftraege
        plan = auftraege.planen(wiki, daten)
        ki, ablegen = plan["rechte"]["ki_auswertung"], plan["rechte"]["volltext_ablegen"]
        if not ki["erlaubt"]:
            grund = f" ({ki['hinweis']})" if ki["hinweis"] else ""
            raise Fehler(f"Rechte: {ki['aktion']}: {ki['wort']}{grund}. Ohne Beleg geht nur die Karte ohne Modell.", 422)
        if plan["eingang"] and ablegen["wert"] == "nein":
            raise Fehler(f"Rechte: {ablegen['aktion']}: nicht erlaubt ({ablegen['hinweis']}). Es geht nur die Karte ohne Modell.", 422)
        if not plan["eingang"] and plan["meta"].get("art") == "link":
            raise Fehler("Zu einem Link holt okfw keinen Text: Webseiten gehen nur im Container und mit Freigabe. "
                         "Lade die Datei herunter und ziehe sie hierher, oder lege die Karte ohne Modell an.", 422)
        konzepte = [{"id": p.id, "titel": p.title} for p in wiki.pages() if p.type == "concept" and p.fm is not None][:300]
        warnungen = []
        if ki["wert"] != "ja":
            warnungen.append(f"Rechte: {ki['aktion']} {ki['wort']}{': ' + ki['hinweis'] if ki['hinweis'] else ''}. Dein Beleg steht auf der Karte: {ki['beleg']}")
        if not plan["ablegen"]:
            warnungen.append("Das Original wird nicht abgelegt. Die Karte lässt sich später nicht mehr am Original prüfen.")
        if plan["eingang"]:
            os.utime(auftraege.eingang_datei(plan["eingang"]))   # es wird gebraucht: nicht als liegen geblieben aufräumen
        was = f"der ausgelesene Text von {plan['name']}" if plan["eingang"] else "der frei zugängliche Text zur Kennung"
        return {**plan, "anweisung": "Quelle auswerten", "warnungen": warnungen,
                "config": str(sitzung.config_path), "konzepte": konzepte, "kern": sitzung.core, "ordner": str(auftraege.eingang_ordner(anlegen=True)),
                "gesendet": {"hinweis": f"Gesendet wurden {was} und die Titel von {len(konzepte)} Konzeptkarten", "konzepte": len(konzepte)}}

    def text_holen(self, eingabe: dict, lauf: Lauf) -> tuple[str, str]:
        """Zu einer Kennung über offizielle Literatur-APIs: freier Volltext, sonst nur das Abstract."""
        csl = eingabe["meta"].get("csl") or {}
        doi, pmid = str(csl.get("DOI") or ""), str(csl.get("PMID") or "")
        frage = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid} AND SRC:MED" if pmid else ""
        if not frage:
            raise Fehler("Zu dieser Kennung gibt es hier keinen Text. Lade die Datei herunter und ziehe sie hierher.", 422)
        if not API_PY.is_file():
            raise Fehler("Der Abruf über die Literatur-APIs ist nicht installiert (Skill recherche). Ziehe die Datei hierher.", 422)
        try:
            treffer = eingabe["kern"].http_json(EPMC_SUCHE + urllib.parse.urlencode(
                {"query": frage, "resultType": "lite", "format": "json", "pageSize": 1})).get("resultList", {}).get("result") or []
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise Fehler(f"Europe PMC ist nicht erreichbar ({type(exc).__name__})", 502) from None
        pmcid = str(treffer[0].get("pmcid") or "") if treffer and treffer[0].get("isOpenAccess") == "Y" else ""
        if not re.fullmatch(r"PMC\d+", pmcid):   # Die Antwort ist fremd und würde sonst zur Option von api.py, etwa --xml=<lokale Datei>
            pmcid = ""
        ordner = tempfile.mkdtemp(prefix="okfw-text-")
        try:
            ziel = Path(ordner) / "text.txt"
            befehl = [sys.executable, "-I", str(API_PY)]
            modus, aufruf = ("volltext", [*befehl, "volltext", pmcid, "--out", str(ziel)]) if pmcid else (
                "abstract", [*befehl, "abstract", *(["--doi", doi] if doi else ["--pmid", pmid]), "--out", str(ziel)])
            code = abruf_aufruf(aufruf, ordner, lauf.abbruch)[0]
            if code != 0 or not ziel.is_file():
                raise Fehler("Zu dieser Kennung gibt es weder freien Volltext noch ein Abstract. Lade die Datei herunter und ziehe sie hierher.", 422)
            return q.markdown_lesen(ziel.read_bytes()), modus
        finally:
            shutil.rmtree(ordner, ignore_errors=True)

    def laufen(self, eingabe: dict, lauf: Lauf) -> dict:
        lauf.melde("Der Text wird ausgelesen.")
        modus, kennung, art = "volltext", eingabe["eingang"], eingabe["art"]
        if kennung:
            try:
                text, auskunft = q.original_lesen(Path(eingabe["ordner"]) / f"{kennung}{'.pdf' if art == 'pdf' else '.md'}", lauf.abbruch)
            except q.Unlesbar as exc:
                if lauf.abbruch.is_set():
                    raise Abgebrochen() from None
                raise Fehler(str(exc), 422) from None
            zeichen = auskunft["zeichen"]
        else:
            lauf.melde("Der Text wird über die Literatur-APIs geholt.")
            text, modus = self.text_holen(eingabe, lauf)
            zeichen = len(text)
            roh = text.encode("utf-8")
            kennung = hashlib.sha256(roh).hexdigest()
            art = "markdown"
            ablage = Path(eingabe["ordner"]) / f"{kennung}.md"
            ablage.write_bytes(roh)
            os.chmod(ablage, 0o600)
        if not zeichen:
            raise Fehler("In dieser Datei steht kein lesbarer Text, vielleicht ist sie ein Scan. Es geht nur die Karte ohne Modell.", 422)
        if len(text) > q.MAX_TEXT_FUER_MODELL:
            raise Fehler(f"Der Text ist für einen Modellaufruf zu lang ({len(text)} Zeichen, höchstens {q.MAX_TEXT_FUER_MODELL}). Es geht nur die Karte ohne Modell.", 422)
        lauf.melde("Das Modell liest die Quelle.")
        konzepte = "\n".join(f"{k['id']}: {k['titel']}" for k in eingabe["konzepte"]) or "(noch keine)"
        antwort = modell_fragen(eingabe, self.rolle, q.AUFTRAG_QUELLE, {"volltext.txt": text, "konzepte.txt": konzepte}, lauf)
        return {**antwort, "original": text, "art": art, "modus": modus, "eingang": kennung}

    def auswerten(self, sitzung, wiki, eingabe: dict, antwort: dict) -> dict:
        core = sitzung.core
        try:
            roh = q.json_aus(antwort["text"])
        except q.Unlesbar as exc:
            raise Fehler(str(exc), 502) from None
        warnungen = list(eingabe["warnungen"])
        fremd = sorted(set(map(str, roh)) - q.ANTWORT_FELDER)
        if fremd:
            warnungen.append("Das Modell hat Felder geschickt, die es nicht gibt; sie wurden nicht beachtet: " + ", ".join(k[:40] for k in fremd[:10]))
        for hinweis in (roh.get("hinweise") if isinstance(roh.get("hinweise"), list) else [])[:10]:
            if q.zeile(hinweis):
                warnungen.append("Das Modell meldet aus dem Text: " + q.zeile(hinweis, 300))
        original = q.Original(antwort["original"], antwort["art"])
        gestrichen = []

        def pruefen(wert):
            gut, weg = q.aussagen_pruefen(wert, original)
            gestrichen.extend(weg)
            return gut

        kernaussagen, zahlen, einschraenkungen = pruefen(roh.get("kernaussagen")), pruefen(roh.get("zahlen")), pruefen(roh.get("einschraenkungen"))
        bekannt = {k["id"]: k["titel"] for k in eingabe["konzepte"]}
        verwandt = [k for k in dict.fromkeys(x for x in (roh.get("verwandt") if isinstance(roh.get("verwandt"), list) else []) if isinstance(x, str)) if k in bekannt]
        slug, quell_titel = eingabe["slug"], eingabe["titel"]
        ordner_quellen = wiki.types[core.SOURCE_TYPE]["folder"]
        aenderungen = [{"art": "quelle", "ziel": eingabe["ziel"], "basis": antwort["eingang"],
                        "daten": {**{k: eingabe[k] for k in ("slug", "titel", "name", "lizenz", "rechte_karte", "ablegen", "meta")},
                                  "eingang": antwort["eingang"], "art": antwort["art"], "modus": antwort["modus"],
                                  "beschreibung": eingabe["beschreibung"] or q.zeile(roh.get("beschreibung"), 300),
                                  "kurzreferat": q.zeile(roh.get("kurzreferat")), "design": q.zeile(roh.get("design"), 200),
                                  "kernaussagen": kernaussagen, "zahlen": zahlen, "einschraenkungen": einschraenkungen, "verwandt": verwandt}}]
        typ_konzept = wiki.types.get("concept")
        vergeben = set(bekannt)
        for eintrag in (roh.get("konzepte") if isinstance(roh.get("konzepte"), list) else [])[:q.MAX_KONZEPTE]:
            titel = q.zeile(eintrag.get("titel"), 200) if isinstance(eintrag, dict) else ""
            kurz = q.slug_aus(titel)
            if not titel or not kurz or not typ_konzept:
                continue
            kid = f"{typ_konzept['folder']}/{kurz}"
            if kid in vergeben or os.path.lexists(wiki.bundle / f"{kid}.md"):
                if kid in bekannt and kid not in verwandt:
                    verwandt.append(kid)
                warnungen.append(f"Eine Karte „{titel}“ gibt es schon oder sie steht doppelt im Vorschlag; es entsteht keine zweite.")
                continue
            aussagen = pruefen(eintrag.get("aussagen"))
            if not aussagen:
                warnungen.append(f"Konzept „{titel}“ weggelassen: Keine seiner Aussagen hat einen Anker im Original.")
                continue
            vergeben.add(kid)
            aenderungen.append({"art": "seite", "ziel": kid, "basis": "", "braucht": ["a1"],
                                "daten": {"typ": "concept", "slug": kurz, "titel": titel, "beschreibung": q.zeile(eintrag.get("beschreibung"), 300) or titel,
                                          "erklaerung": q.zeile(eintrag.get("erklaerung"), 1600), "aussagen": aussagen, "verwandt": verwandt[:5],
                                          "eingang": antwort["eingang"], "art": antwort["art"],
                                          "quelle": {"slug": slug, "titel": quell_titel, "ziel": eingabe["ziel"], "resource": f"/{ordner_quellen}/{slug}.md"}}})
        if gestrichen:
            warnungen.append(f"{len(gestrichen)} Aussagen gestrichen, weil ihr Anker nicht wörtlich im Original steht: " + " · ".join(gestrichen[:6]))
        if not kernaussagen:
            warnungen.append("Keine Kernaussage hat einen Anker im Original. Die Karte enthält nur das Kurzreferat; bitte selbst prüfen.")
        return {"warnungen": warnungen, "aenderungen": aenderungen}


def svg_aus(text: str) -> str | None:
    """Vom ersten <svg bis zum letzten </svg>. Läuft unter der Sperre: darum ohne Regex, der bei einer langen Antwort ohne Abschluss quadratisch würde."""
    anfang, ende = text.find("<svg"), text.rfind("</svg")
    schluss = text.find(">", ende) if ende > anfang >= 0 else -1
    return text[anfang:schluss + 1] if schluss >= 0 else None


class Schaubild:
    """Ein SVG aus einer Beschreibung. Gesendet wird nur die Beschreibung; ein vorhandenes Bild ersetzt dieser Weg nie."""

    name = "schaubild"
    rolle = "bearbeiten"

    def pruefen(self, sitzung, wiki, daten: dict) -> dict:
        core = sitzung.core
        beschreibung, name = daten.get("beschreibung"), daten.get("name")
        if not isinstance(beschreibung, str) or not beschreibung.strip():
            raise Fehler("beschreibung fehlt: Was soll das Schaubild zeigen?")
        if len(beschreibung) > 4000:
            raise Fehler("Die Beschreibung ist länger als 4000 Zeichen", 422)
        if not isinstance(name, str) or not core.SLUG_RE.fullmatch(name):
            raise Fehler("Name: nur kleine Buchstaben, Ziffern und Bindestriche (kebab-case)", 422)
        titel, unterschrift = daten.get("titel", ""), daten.get("unterschrift", "")
        if not isinstance(titel, str) or not isinstance(unterschrift, str):
            raise Fehler("titel und unterschrift sind Text")
        if os.path.lexists(wiki.figure_file(name)):
            raise Fehler(f"Es gibt schon eine Abbildung {name}. Wähle einen anderen Namen.", 409)
        return {"ziel": f"{core.FIGURES_DIR}/{name}", "titel": titel.strip() or name, "anweisung": beschreibung.strip(), "name": name,
                "unterschrift": unterschrift.strip(), "warnungen": [], "config": str(sitzung.config_path),
                "gesendet": {"zeichen": len(beschreibung.strip()), "hinweis": "Gesendet wurde nur deine Beschreibung"}}

    def laufen(self, eingabe: dict, lauf: Lauf) -> dict:
        return modell_fragen(eingabe, self.rolle, f"{AUFTRAG_SCHAUBILD}\n\nBeschreibung der Person:\n{eingabe['anweisung']}\n", {}, lauf)

    def auswerten(self, sitzung, wiki, eingabe: dict, antwort: dict) -> dict:
        core = sitzung.core
        svg = svg_aus(antwort["text"])
        if svg is None:
            raise Fehler("Das Modell hat kein SVG geliefert. Formuliere die Beschreibung anders und versuche es noch einmal.")
        if len(svg.encode("utf-8")) > core.FIGURE_MAX_BYTES:
            raise Fehler(f"Das SVG des Modells ist größer als {core.FIGURE_MAX_BYTES // 1024} KB. Beschreibe ein einfacheres Schaubild.")
        try:
            _, entfernt = core.clean_svg(svg.encode("utf-8"))
        except core.FigureError as exc:
            raise Fehler(f"Das SVG des Modells wurde abgelehnt: {exc}") from exc
        verlust = core.svg_verlust(entfernt)
        warnungen = ["Prüfe jede Beschriftung und jede Zahl: Das Modell kennt deine Quellen nicht. Das Bild ist eine Illustration, kein Beleg."]
        if verlust["sichtbar"]:
            warnungen.append(f"Die Bereinigung hat Sichtbares entfernt: {', '.join(verlust['sichtbar'])}.")
        return {"warnungen": warnungen,
                "aenderungen": [{"art": "abbildung", "ziel": eingabe["ziel"], "basis": "",
                                 "daten": {"name": eingabe["name"], "svg": svg, "titel": eingabe["titel"], "unterschrift": eingabe["unterschrift"]}}]}


TYPEN = {t.name: t for t in (Bearbeiten(), Quelle(), Schaubild())}


# ---------------------------------------------------------------- Änderungsarten

def _ist_text(wert) -> bool:
    return isinstance(wert, str)


def _textliste(wert) -> bool:
    return isinstance(wert, list) and all(_ist_text(x) for x in wert)


def vermerk_aus(auftrag: dict) -> dict:
    """Wer den Text vorgeschlagen hat: bleibt in generated.modelle."""
    r = auftrag["rolle"]
    return {"harness": r["harness"], **({"anbieter": r["anbieter"]} if r["anbieter"] else {}), "modell": r["modell"], "rolle": TYPEN[auftrag["typ"]].rolle, "art": "vorschlag"}


class Koerper:
    @staticmethod
    def gueltig(daten) -> bool:
        return isinstance(daten, dict) and _ist_text(daten.get("alt")) and _ist_text(daten.get("neu"))

    @staticmethod
    def annehmen(sitzung, auftrag: dict, a: dict) -> dict:
        """Über denselben Weg wie „Speichern“: Hash, Symlink, Akteur, Modellherkunft, Ereignis und Neubau bleiben an einer Stelle."""
        _, antwort = sitzung.seite_speichern({"id": a["ziel"], "hash": a["basis"], "body": a["daten"]["neu"], "vorschlag": True})
        return antwort

    @staticmethod
    def zum_lesen(sitzung, a: dict) -> dict:
        return a


class Abbildung:
    @staticmethod
    def gueltig(daten) -> bool:
        return isinstance(daten, dict) and all(_ist_text(daten.get(k)) for k in ("name", "svg", "titel", "unterschrift"))

    @staticmethod
    def annehmen(sitzung, auftrag: dict, a: dict) -> dict:
        """Über denselben Weg wie ein hochgeladenes SVG. Bereinigt der Kern beim Ablegen; vorhanden heißt 409, nie ersetzen."""
        d = a["daten"]
        modell = auftrag["rolle"]["modell"] or sitzung.core.MODELL_UNBEKANNT
        _, antwort = sitzung.abbildung({"name": d["name"], "svg": d["svg"]}, rolle=TYPEN[auftrag["typ"]].rolle, modell=modell)
        kennzeichnung = f"Mit KI erzeugt ({modell})"
        zeigt = d["unterschrift"].rstrip(".") or d["titel"]
        return {**antwort, "titel": d["titel"], "unterschrift": zeigt, "kennzeichnung": kennzeichnung,
                "einbindung": f"![{d['titel']}]({antwort['pfad']})\n*Abb.: {zeigt}. {kennzeichnung}.*"}

    @staticmethod
    def zum_lesen(sitzung, a: dict) -> dict:
        """Die Vorschau kommt jedes Mal frisch aus der Bereinigung: Die Datei im Ordner gilt als ungeprüft, die Seite setzt das SVG als HTML ein."""
        core = sitzung.core
        daten = {k: v for k, v in a["daten"].items() if k != "svg"}
        try:
            daten["vorschau"], _ = core.clean_svg(a["daten"]["svg"].encode("utf-8"), prefix="vorschau-")
        except (core.FigureError, UnicodeError) as exc:
            daten["abgelehnt"] = str(exc)
        return {**a, "daten": daten}


class Quellenkarte:
    @staticmethod
    def gueltig(daten) -> bool:
        return isinstance(daten, dict)   # Die ganze Form prüft _vorschlag_gueltig, sie braucht auch Ziel und Basis

    @staticmethod
    def annehmen(sitzung, auftrag: dict, a: dict) -> dict:
        return sitzung.auftraege.quelle_schreiben(a["ziel"], a["daten"], a["basis"], vermerk_aus(auftrag))

    @staticmethod
    def zum_lesen(sitzung, a: dict) -> dict:
        return a


class Konzeptkarte:
    @staticmethod
    def gueltig(daten) -> bool:
        return isinstance(daten, dict)   # wie Quellenkarte

    @staticmethod
    def annehmen(sitzung, auftrag: dict, a: dict) -> dict:
        """Wie /api/neu als Entwurf; die Aussagen wurden im Vorschlag geprüft und werden es hier noch einmal, denn die Datei kann fremd sein."""
        core, d, auftraege = sitzung.core, a["daten"], sitzung.auftraege
        with sitzung.lock:
            wiki = sitzung.wiki()
            wiki.actor = sitzung.akteur()
            if os.path.lexists(wiki.bundle / f"{a['ziel']}.md"):
                raise Fehler(f"Die Karte {a['ziel']} gibt es inzwischen. Es wurde nichts überschrieben.", 409)
            quelle = d["quelle"]
            titel = q.zeile(quelle["titel"], 300)
            if not os.path.lexists(wiki.bundle / f"{quelle['ziel']}.md"):
                raise Fehler(f"Die Quellenkarte {quelle['ziel']} fehlt; ohne sie hätte diese Karte keinen Beleg.", 409)
            aussagen, weg = q.aussagen_pruefen(d["aussagen"], auftraege.original_von(d))
            if not aussagen:
                raise Fehler("Keine Aussage dieser Karte hat einen Anker im Original. Nichts angelegt.", 409)
            seiten = {p.id: p for p in wiki.pages()}
            links = [(seiten[k].title, f"/{k}.md") for k in d["verwandt"] if k in seiten]
            body, quellen = q.konzept_koerper({**d, "titel": q.zeile(d["titel"], 200), "aussagen": aussagen}, {"slug": q.slug_aus(quelle["slug"]), "titel": titel, "resource": f"/{quelle['ziel']}.md"}, links)
            try:
                pfad = wiki.new_page(d["typ"], d["slug"], title=q.zeile(d["titel"], 200), description=q.zeile(d["beschreibung"], 300), extra={"sources": quellen})
                pid = wiki.page_id(pfad)
                vermerk = vermerk_aus(auftrag)
                wiki.replace_body(pid, body, f"Aus dem Vorschlag der Rolle lesen übernommen ({vermerk['modell']}); {len(aussagen)} Anker am Original geprüft", modell=vermerk)
                wiki = sitzung.wiki()
                wiki.actor = sitzung.akteur()
                karte = sitzung.seite(wiki, quelle["ziel"])
                # Die Verbindung setzt der Kern: Sie macht aus dem unveränderten Vorschlag keinen geänderten Text; hat die Person geändert, bleibt das so stehen
                unberuehrt = any(m.get("gleich") for m in wiki.modelle_sicht(karte))
                wiki.replace_body(karte.id, q.verbindung_ergaenzen(karte.body, d["titel"], f"/{pid}.md"), f"Verbindung zu {pid}", modell=vermerk if unberuehrt else None)
            except core.WikiError as exc:
                raise Fehler(str(exc), 422) from None
            return auftraege.fertig_melden(pid, [f"{len(weg)} Aussagen gestrichen, ihr Anker fehlt im Original."] if weg else [])

    @staticmethod
    def zum_lesen(sitzung, a: dict) -> dict:
        return a


ARTEN = {"koerper": Koerper, "abbildung": Abbildung, "quelle": Quellenkarte, "seite": Konzeptkarte}


# ---------------------------------------------------------------- Maschine


def _gueltig(d) -> bool:
    """Eine Datei aus dem Ordner ist nur dann ein Auftrag, wenn sie genau diese Form hat."""
    if not isinstance(d, dict) or d.get("version") != VERSION or not _ist_text(d.get("id")) or not ID_RE.fullmatch(d["id"]):
        return False
    if d.get("typ") not in TYPEN or d.get("zustand") not in ZUSTAENDE or not isinstance(d.get("zeit"), (int, float)):
        return False
    if not isinstance(d.get("rolle"), dict) or not all(_ist_text(d["rolle"].get(k)) for k in ("harness", "anbieter", "modell")):
        return False
    if not _ist_text(d.get("ziel")) or not isinstance(d.get("warnungen"), list) or not all(_ist_text(w) for w in d["warnungen"]):
        return False
    aenderungen = d.get("aenderungen")
    if not isinstance(aenderungen, list):
        return False
    for a in aenderungen:
        if not isinstance(a, dict) or not _ist_text(a.get("id")) or a.get("art") not in ARTEN or a.get("status") not in STATUS:
            return False
        if not _ist_text(a.get("ziel")) or not _ist_text(a.get("basis")) or not isinstance(a.get("braucht"), list):
            return False
        if not ARTEN[a["art"]].gueltig(a.get("daten")):
            return False
        if a["art"] in ("quelle", "seite") and not _vorschlag_gueltig(a):
            return False
    return True


def _aussagen_gueltig(liste) -> bool:
    return isinstance(liste, list) and all(isinstance(x, dict) and all(_ist_text(x.get(k)) for k in ("aussage", "fundstelle", "anker")) for x in liste)


def _dateiname_gueltig(name) -> bool:
    """Wie ihn `planen` baut, ohne Pfadtrenner: Beim Annehmen wird er zum Ziel einer Kopie."""
    return _ist_text(name) and re.fullmatch(r"[\w. -]+", name) is not None and not name.startswith(".")


def _vorschlag_gueltig(a: dict) -> bool:
    d = a.get("daten")
    if not isinstance(d, dict) or not _ist_text(d.get("eingang")) or not q.KENNUNG_RE.fullmatch(d["eingang"]) or d.get("art") not in ("pdf", "markdown"):
        return False
    if not _ist_text(d.get("slug")) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", d["slug"]) or not _ist_text(d.get("titel")):
        return False
    if re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", a["ziel"]) is None:
        return False
    if a["art"] == "quelle":
        return (a["basis"] == d["eingang"] and isinstance(d.get("meta"), dict) and isinstance(d.get("lizenz"), dict) and isinstance(d.get("rechte_karte"), dict) and _dateiname_gueltig(d.get("name"))
                and all(_aussagen_gueltig(d.get(k)) for k in ("kernaussagen", "zahlen", "einschraenkungen")) and _textliste(d.get("verwandt")))
    quelle = d.get("quelle")
    return (d.get("typ") == "concept" and _aussagen_gueltig(d.get("aussagen")) and _textliste(d.get("verwandt")) and isinstance(quelle, dict)
            and all(_ist_text(quelle.get(k)) for k in ("slug", "titel", "ziel")) and re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", quelle["ziel"]) is not None)


def _erledigt(d: dict) -> bool:
    if d["zustand"] in AKTIV:
        return False
    if d.get("gesehen"):
        return True
    return d["zustand"] == "fertig" and all(a["status"] != "offen" for a in d["aenderungen"])


class Auftraege:
    """Alle Aufträge dieses Servers. Zustand und Dateien wechseln unter der Sperre der Sitzung."""

    def __init__(self, sitzung):
        self.sitzung = sitzung
        self.auftraege: dict[str, dict] = {}
        self.abbrueche: dict[str, threading.Event] = {}
        self.rollensperren: dict[str, threading.Lock] = {}
        self.laeufe: list[threading.Thread] = []
        self.ordner_pfad: Path | None = None
        self.geladen = False
        self.kennungen: dict[str, dict] = {}   # was eine DOI ergab: Karten nehmen das von hier, nicht aus der Anfrage

    # -------------------------------------------------------------- Ablage

    def ordner(self, anlegen: bool = False) -> Path:
        if self.ordner_pfad is None:
            self.ordner_pfad = self.sitzung.wiki().bundle / self.sitzung.core.AUFTRAEGE
        pfad = self.ordner_pfad
        if pfad.is_symlink() or (os.path.lexists(pfad) and not pfad.is_dir()):
            raise Fehler(f"{pfad.name} ist kein Ordner im Bundle (Symlink?): Aufträge sind so nicht möglich", 422)
        if anlegen and not pfad.exists():
            pfad.mkdir(mode=0o700)
            (pfad / ".gitignore").write_text("*\n", encoding="utf-8")
        return pfad

    def schreiben(self, d: dict) -> None:
        ordner = self.ordner(anlegen=True)
        griff, name = tempfile.mkstemp(dir=ordner, prefix=".neu-", suffix=".tmp")   # 0600
        try:
            with os.fdopen(griff, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(name, ordner / f"{d['id']}.json")
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def laden(self) -> None:
        """Einmal je Server: Was beim letzten Ende lief, kann nicht mehr fertig werden; alt und erledigt fällt weg."""
        if self.geladen:
            return
        self.geladen = True
        ordner = self.ordner()
        if not ordner.is_dir():
            return
        for datei in sorted(ordner.glob("*.json")):
            try:
                if datei.is_symlink() or datei.stat().st_size > MAX_DATEI:
                    raise ValueError("Symlink oder zu groß")
                d = json.loads(datei.read_text(encoding="utf-8"))
                if not _gueltig(d) or d["id"] != datei.stem:
                    raise ValueError("unerwartete Form")
            except (OSError, ValueError, RecursionError) as exc:
                print(f"okfw serve: {datei.name} in {ordner.name} übergangen: {exc}", file=sys.stderr)
                continue
            if _erledigt(d) and time.time() - d["zeit"] > AUFHEBEN_S:
                datei.unlink(missing_ok=True)
                continue
            if d["zustand"] in AKTIV:
                d.update(zustand="abgebrochen", fehler="Der Server wurde neu gestartet, während der Auftrag lief.")
                self.schreiben(d)
            self.auftraege[d["id"]] = d
        self.eingang_pflegen()

    # -------------------------------------------------------------- Eingang: abgelegte Dateien

    def eingang_ordner(self, anlegen: bool = False) -> Path:
        pfad = self.ordner(anlegen=anlegen) / EINGANG
        if pfad.is_symlink() or (os.path.lexists(pfad) and not pfad.is_dir()):
            raise Fehler(f"{EINGANG} im Ordner der Aufträge ist kein Ordner (Symlink?): Quellen ablegen ist so nicht möglich", 422)
        if anlegen and not pfad.exists():
            pfad.mkdir(mode=0o700)
        return pfad

    def eingang_datei(self, kennung) -> Path:
        """Die abgelegte Datei; ihr Name ist ihre Prüfsumme, nie ein Pfad aus der Anfrage."""
        if not isinstance(kennung, str) or not q.KENNUNG_RE.fullmatch(kennung):
            raise Fehler("eingang fehlt oder ist keine Prüfsumme")
        for endung in (".pdf", ".md"):
            pfad = self.eingang_ordner() / f"{kennung}{endung}"
            if pfad.is_file() and not pfad.is_symlink():
                if q.sha256_der_datei(pfad) != kennung:
                    raise Fehler("Die abgelegte Datei hat sich geändert. Bitte neu ablegen.", 409)
                return pfad
        raise Fehler("Die abgelegte Datei gibt es nicht mehr. Bitte neu ablegen.", 409)

    def original_von(self, d: dict) -> q.Original:
        """Der Text am Original, frisch gelesen: Was ein Vorschlag über seine Anker sagt, gilt nicht als Beleg."""
        try:
            text, _ = q.original_lesen(self.eingang_datei(d["eingang"]))
        except q.Unlesbar as exc:
            raise Fehler(str(exc), 422) from None
        return q.Original(text, d["art"])

    def eingang_pflegen(self, auch: tuple = ()) -> None:
        """Was kein offener Vorschlag braucht: die genannten Dateien sofort, liegen Gebliebenes nach einem Tag."""
        ordner = self.eingang_ordner()
        if not ordner.is_dir():
            return
        gebraucht = {a["daten"]["eingang"] for d in self.auftraege.values() if not _erledigt(d) for a in d["aenderungen"] if a["art"] in ("quelle", "seite")}
        for datei in ordner.iterdir():
            if datei.stem in gebraucht or not datei.is_file() or datei.is_symlink():
                continue
            if datei.stem in auch or time.time() - datei.stat().st_mtime > EINGANG_AUFHEBEN_S:
                datei.unlink(missing_ok=True)

    def datei_annehmen(self, strom, laenge: int, art: str, name: str):
        """Eigene Route, eigene Grenze: Die Bytes gehen ohne Sperre in den Eingang, ausgelesen wird abgeschottet."""
        art = art.split(";")[0].strip().lower()
        if art not in DATEIARTEN:
            raise Fehler("Nur PDF oder Markdown", 415)
        endung, grenze = DATEIARTEN[art]
        if laenge > grenze:
            raise Fehler(f"Die Datei ist größer als {grenze // 2**20} MB", 413)
        if not laenge:
            raise Fehler("Die Datei ist leer", 422)
        with self.sitzung.lock:
            self.laden()
            ordner = self.eingang_ordner(anlegen=True)
        griff, zwischen = tempfile.mkstemp(dir=ordner, prefix=".neu-", suffix=".tmp")   # 0600
        h = hashlib.sha256()
        try:
            with os.fdopen(griff, "wb") as f:
                rest = laenge
                while rest:
                    stueck = strom.read(min(rest, 1 << 20))
                    if not stueck:
                        raise Fehler("Die Übertragung brach ab", 400)
                    f.write(stueck)
                    h.update(stueck)
                    rest -= len(stueck)
            if endung == ".pdf" and b"%PDF-" not in Path(zwischen).read_bytes()[:1024]:
                raise Fehler("Das ist kein PDF: Die Kennung %PDF- fehlt am Anfang.", 422)
            kennung = h.hexdigest()
            ziel = ordner / f"{kennung}{endung}"
            os.replace(zwischen, ziel)
        except BaseException:
            Path(zwischen).unlink(missing_ok=True)
            raise
        name = re.sub(r"[^\w. -]", "", Path(urllib.parse.unquote(name)).name).strip(" .")[:120]
        name = f"{Path(name).stem or 'quelle'}{endung if endung == '.pdf' else Path(name).suffix or endung}"
        aus = {"eingang": kennung, "name": name, "bytes": laenge, "art": "pdf" if endung == ".pdf" else "markdown", "titel": Path(name).stem.replace("_", " ").replace("-", " ")}
        try:
            text, auskunft = q.original_lesen(ziel)
            aus |= auskunft
            aus |= {"doi": q.doi_finden(text), "lizenz_im_text": q.lizenz_finden(text, self.sitzung.core.license_name_from), "zu_lang": len(text) > q.MAX_TEXT_FUER_MODELL}
            if aus["art"] == "markdown" and (m := re.search(r"^# +(.+)$", text, re.M)):
                aus["titel"] = q.zeile(m.group(1), 200)
        except q.Unlesbar as exc:   # die Karte ohne Modell geht auch ohne Text
            aus |= {"fehler": str(exc), "zeichen": 0}
        return 200, aus

    # -------------------------------------------------------------- Rechte und Metadaten klären

    def kennung_lesen(self, text) -> tuple[dict, str, str]:
        """(Kennungen, Link, Schlüssel). Ein Link auf doi.org, PubMed oder arXiv zählt als Kennung."""
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise Fehler("Bitte eine DOI oder einen Link angeben")
        text = text.strip()
        pmid = text if re.fullmatch(r"\d{1,9}", text) else ""
        doi = text if re.fullmatch(r"(?:doi:\s*)?10\.\d{4,9}/\S+", text, re.I) else ""
        arxiv = re.sub(r"^arxiv:", "", text, flags=re.I) if re.fullmatch(r"arxiv:\S+", text, re.I) else ""
        link = ""
        if not (pmid or doi or arxiv):
            teile = urllib.parse.urlsplit(text)
            if teile.scheme not in ("http", "https") or not teile.hostname or teile.username or teile.password:
                raise Fehler("Das ist weder eine DOI noch ein Link (http oder https ohne Zugangsdaten)", 422)
            link = text
        ids = self.sitzung.core.kennungen(doi.removeprefix("doi:").strip() if doi else "", pmid, arxiv, link)
        return ids, link, json.dumps(ids, sort_keys=True) if ids else link

    def kennung_aufloesen(self, text) -> dict:
        """DOI, PMID oder arXiv-ID über die offiziellen APIs des Kerns. Ein anderer Link bleibt ein Verweis: Webseiten holt der Server nie."""
        ids, link, schluessel = self.kennung_lesen(text)
        core = self.sitzung.core
        if schluessel in self.kennungen:
            return self.kennungen[schluessel]
        with self.sitzung.lock:
            wiki = self.sitzung.wiki()
        if not ids:
            meta = {"art": "link", "resource": link, "lizenz": None, "extra": {}, "titel": "", "rueckzug": [], "ids": {}, "csl": None}
        else:
            csl, resource, geprueft, flags = wiki.fetch_source(ids.get("doi", ""), ids.get("pmid", ""), ids.get("arxiv", ""))
            quellen = wiki.cfg["sources"]
            extra, treffer = {quellen["checked_field"]: geprueft, **({quellen["updates_field"]: flags} if flags else {})}, []
            if not ids.get("arxiv"):
                pruefung = wiki.check_retraction(doi=str(csl.get("DOI") or ids.get("doi", "")), pmid=str(csl.get("PMID") or ids.get("pmid", "")), flags=flags)
                pruefung.pop("warnungen", None)
                treffer = [core.notice_text(h) for h in pruefung["hinweise"] if core.notice_kind(h["art"]) in ("rueckzug", "bedenken")]
                if not pruefung["hinweise"]:
                    del pruefung["hinweise"]
                extra[quellen["retraction_field"]] = {**pruefung, **({"trotz": True} if treffer else {})}
            doi, pmid = str(csl.get("DOI") or ""), str(csl.get("PMID") or "")
            meta = {"art": "kennung", "resource": resource, "csl": csl, "extra": extra, "titel": core.one_line(csl.get("title")), "rueckzug": treffer,
                    "lizenz": wiki.lookup_license(doi=doi, pmid=pmid), "ids": core.kennungen(csl.get("DOI"), csl.get("PMID"), ids.get("arxiv", ""))}
        if len(self.kennungen) >= 100:
            self.kennungen.pop(next(iter(self.kennungen)))
        self.kennungen[schluessel] = meta
        return meta

    def rechte_von(self, wiki, lizenz, belege) -> dict:
        """Dieselbe Matrix wie `rechte` und /lesen. „mit Auflage“ und „erst fragen“ gelten erst mit dem Beleg der Person, der auf die Karte kommt;
        Ablegen braucht ihn nur bei „erst fragen“, die Auflage steht bei der Klasse."""
        core = self.sitzung.core
        klasse = core.classify_license(lizenz, wiki.cfg["licenses"].get("patterns"))[0]
        belege = belege if isinstance(belege, dict) else {}
        aus = {"klasse": core.LICENSE_CLASSES[klasse]}
        for aktion in RECHTE_FRAGEN:
            wert, hinweis = wiki.rights_matrix()[klasse][aktion]
            beleg = q.zeile(belege.get(aktion), 500)
            frei = wert == "ja" or (wert != "nein" and bool(beleg)) or (aktion == "volltext_ablegen" and wert == "bedingt")
            aus[aktion] = {"wert": wert, "hinweis": hinweis, "wort": core.RIGHT_WORDS.get(wert, wert), "erlaubt": frei, "beleg": beleg, "aktion": core.RIGHTS_ACTIONS[aktion]}
        return aus

    @staticmethod
    def lizenz_lesen(roh, standard=None) -> dict:
        """Nur Name und Adresse aus der Anfrage; was eine API meldete, bleibt mit Herkunft, solange die Person nichts ändert."""
        if not isinstance(roh, dict):
            return standard or {"name": "unknown", "url": None}
        name, url = q.zeile(roh.get("name"), 200) or "unknown", q.zeile(roh.get("url"), 500) or None
        if url and not re.match(r"https?://", url):
            raise Fehler("Die Adresse der Lizenz beginnt nicht mit http oder https", 422)
        if standard and standard.get("name") == name and standard.get("url") == url:
            return standard
        return {"name": name, "url": url}

    def dublette(self, wiki, meta: dict, eingang) -> str:
        core = self.sitzung.core
        ids = meta.get("ids") or {}
        for p in wiki.source_cards():
            schluessel = wiki.source_keys(p)
            if any(schluessel.get(k) == v for k, v in ids.items()):
                return p.id
            if eingang and str((p.fm or {}).get("resource") or "") == f"urn:sha256:{eingang}":
                return p.id
            paket = (p.fm or {}).get("raw_packet")
            manifest = wiki._im_projekt(f"{paket}/manifest.json") if paket and eingang else None
            if manifest is not None and manifest.is_file():
                daten = core._manifest_lesen(manifest) or {}
                if any(isinstance(f, dict) and f.get("sha256") == eingang for f in core.as_list(daten.get("files"))):
                    return p.id
        return ""

    def planen(self, wiki, daten: dict) -> dict:
        """Prüft die Antworten aus dem Dialog am Server. Daraus entstehen Vorschlag und Karte ohne Modell gleichermaßen."""
        core = self.sitzung.core
        if core.SOURCE_TYPE not in wiki.types:
            raise Fehler(f"Dieses Wiki hat keinen Typ {core.SOURCE_TYPE}", 422)
        eingang, kennung = daten.get("eingang") or "", daten.get("kennung") or ""
        if not eingang and not kennung:
            raise Fehler("Es fehlt eine Datei oder eine DOI")
        art = ""
        if eingang:
            art = "pdf" if self.eingang_datei(eingang).suffix == ".pdf" else "markdown"
        meta = {"art": "datei", "resource": f"urn:sha256:{eingang}", "lizenz": None, "extra": {}, "csl": None, "rueckzug": [], "ids": {}}
        if kennung:
            schluessel = self.kennung_lesen(kennung)[2]
            if schluessel not in self.kennungen:
                raise Fehler("Diese Kennung ist noch nicht geklärt. Bitte den Dialog neu öffnen.", 409)
            meta = self.kennungen[schluessel]
        titel, slug = q.zeile(daten.get("titel"), 300) or meta.get("titel", ""), daten.get("slug")
        if not titel:
            raise Fehler("Titel fehlt", 422)
        if not isinstance(slug, str) or not core.SLUG_RE.fullmatch(slug):
            raise Fehler("Kennung der Datei: nur kleine Buchstaben, Ziffern und Bindestriche", 422)
        ordner = wiki.types[core.SOURCE_TYPE]["folder"]
        ziel = f"{ordner}/{slug}"
        if os.path.lexists(wiki.bundle / f"{ziel}.md"):
            raise Fehler(f"Die Karte {ziel} gibt es schon. Bitte eine andere Kennung wählen.", 409)
        doppelt = self.dublette(wiki, meta, eingang)
        if doppelt:
            raise Fehler(f"Diese Quelle gibt es schon: {doppelt}. Es wurde nichts angelegt.", 409, {"dublette": doppelt})
        if meta["rueckzug"] and daten.get("trotz_rueckzug") is not True:
            raise Fehler("Rückzug oder Bedenken gemeldet: " + "; ".join(meta["rueckzug"]) + ". Ohne ausdrückliche Bestätigung entsteht keine Karte.", 409, {"rueckzug": meta["rueckzug"]})
        lizenz = self.lizenz_lesen(daten.get("lizenz"), meta.get("lizenz"))
        rechte = self.rechte_von(wiki, lizenz, daten.get("belege"))
        name = re.sub(r"[^\w. -]", "", str(daten.get("name") or "")).strip(" .")[:120] or ("quelle.pdf" if art == "pdf" else "quelle.md")
        ablegen = rechte["volltext_ablegen"]["erlaubt"] and wiki.raw is not None
        rechte_karte = {k: f"ja: {rechte[k]['beleg']}" for k in RECHTE_FRAGEN if rechte[k]["beleg"] and rechte[k]["wert"] != "ja"}
        return {"slug": slug, "titel": titel, "ziel": ziel, "name": name, "eingang": eingang, "art": art, "meta": meta, "lizenz": lizenz, "rechte": rechte,
                "rechte_karte": rechte_karte, "ablegen": ablegen, "beschreibung": q.zeile(daten.get("beschreibung"), 300)}

    def klaeren(self, daten: dict):
        """Für den Dialog: Was ist es, was darf damit geschehen, welches Modell sähe es. Geht ins Netz, darum ohne Sperre, und schreibt nichts."""
        s = self.sitzung
        eingang, kennung = daten.get("eingang") or "", daten.get("kennung") or ""
        meta = {"art": "datei", "lizenz": None, "titel": "", "rueckzug": [], "ids": {}, "csl": None, "resource": ""}
        if kennung:
            try:
                meta = self.kennung_aufloesen(kennung)
            except s.core.WikiError as exc:
                raise Fehler(str(exc), 502) from None
        with s.lock:
            self.laden()
            wiki = s.wiki()
            lizenz = self.lizenz_lesen(daten.get("lizenz"), meta.get("lizenz"))
            rechte = self.rechte_von(wiki, lizenz, daten.get("belege"))
            cfg = s.cfg()
            ziel = rollen.loese(cfg, Quelle.rolle)
            grund = ziel["gesperrt"] or ""
            if ziel["harness"] == "sitzung":
                grund = f"Die Rolle „{rollen.ROLLEN[Quelle.rolle]}“ läuft in der Sitzung; der Server kann sie nicht bedienen. Stelle sie in den Einstellungen auf claude, opencode oder direkt."
            if ziel["harness"] == "container":
                grund = "Diese Rolle läuft nur im Container und kann keine Quelle auswerten."
            aus = {"lizenz": lizenz, "rechte": rechte, "titel": meta.get("titel", ""), "art": meta.get("art"), "resource": meta.get("resource"),
                   "rueckzug": meta.get("rueckzug", []), "dublette": self.dublette(wiki, meta, eingang), "lizenz_via": (meta.get("lizenz") or {}).get("via", ""),
                   "datenklasse": cfg["daten"],
                   "rolle": {k: str(ziel.get(k) or "") for k in ("harness", "anbieter", "modell")}, "rolle_titel": rollen.ROLLEN[Quelle.rolle], "rolle_grund": grund,
                   "lizenzen": list(s.core.LICENSE_CLASSES.values())}
        return 200, aus

    def karte_ohne_modell(self, daten: dict):
        """Nur Metadaten und, wo erlaubt, das Original: Es geht nichts an ein Modell, und im Text bleiben die offenen Stellen der Vorlage."""
        s = self.sitzung
        with s.lock:
            self.laden()
            wiki = s.wiki()
            plan = self.planen(wiki, daten)
            d = {k: plan[k] for k in ("slug", "titel", "name", "lizenz", "rechte_karte", "ablegen", "meta", "eingang", "art", "beschreibung")}
            antwort = self.quelle_schreiben(plan["ziel"], {**d, "modus": "ohne"}, plan["eingang"], None)
            if plan["eingang"]:
                self.eingang_pflegen(auch=(plan["eingang"],))
            return 200, antwort

    def quelle_schreiben(self, ziel: str, d: dict, basis: str, vermerk: dict | None) -> dict:
        """`capture`, dann Quellenkarte; mit einem Vermerk auch ihr Text. Status bleibt Entwurf, freigeben ist ein eigener Schritt."""
        s, core = self.sitzung, self.sitzung.core
        with s.lock:
            wiki = s.wiki()
            wiki.actor = s.akteur()
            slug = d["slug"]
            if os.path.lexists(wiki.bundle / f"{ziel}.md"):
                raise Fehler(f"Die Karte {ziel} gibt es inzwischen. Es wurde nichts überschrieben.", 409)
            datei = self.eingang_datei(basis) if basis else None
            hinweise, gestrichen = [], 0
            texte = {k: d.get(k, []) for k in ("kernaussagen", "zahlen", "einschraenkungen")}
            if vermerk is not None:
                original = self.original_von(d)
                for k in texte:
                    texte[k], weg = q.aussagen_pruefen(texte[k], original)
                    gestrichen += len(weg)
                if gestrichen:
                    hinweise.append(f"{gestrichen} Aussagen gestrichen: Ihr Anker steht nicht im Original.")
            meta, extra = d["meta"], dict(d["meta"].get("extra") or {})
            extra["license"] = d["lizenz"]
            if d["rechte_karte"]:
                extra["rights"] = d["rechte_karte"]
            extra[wiki.cfg["fundweg"].get("field") or "fundweg"] = wiki.fundweg_entry("material" if meta.get("art") == "datei" else "person", by=wiki.actor)
            paket = None
            modus = d.get("modus")
            if d["ablegen"] and datei is not None and modus != "abstract":
                ordner = tempfile.mkdtemp(prefix="okfw-paket-")
                try:
                    kopie = Path(ordner) / d["name"]
                    shutil.copyfile(datei, kopie)
                    lic = d["lizenz"]
                    try:
                        paket = wiki.capture(slug, str(meta.get("resource") or ""), [(kopie, None)], license_name=lic.get("name"), license_url=lic.get("url"), note=f"Im Dashboard abgelegt: {d['name']}")
                    except core.WikiError as exc:
                        hinweise.append(f"Original nicht abgelegt: {exc}")
                finally:
                    shutil.rmtree(ordner, ignore_errors=True)
            if paket is not None:
                extra["raw_packet"] = wiki.rel(paket)
            zugang = "abstract" if modus == "abstract" else "fulltext" if paket is not None or vermerk is not None else "citation"
            try:
                pfad = wiki.new_page(core.SOURCE_TYPE, slug, title=q.zeile(d["titel"], 300), description=q.zeile(d.get("beschreibung"), 300) or q.zeile(d["titel"], 300),
                                     access=zugang, resource=meta.get("resource"), csl=meta.get("csl"), extra=extra)
                pid = wiki.page_id(pfad)
                wortlaut = (f"Original als Rohquelle abgelegt: `{wiki.rel(paket)}`." if paket is not None
                            else "Nur das Abstract gelesen; das Original liegt nicht im Wiki." if modus == "abstract" else "Das Original liegt nicht im Wiki.")
                if vermerk is not None:
                    frisch = s.wiki()
                    frisch.actor = wiki.actor
                    p = s.seite(frisch, pid)
                    seiten = {x.id: x for x in frisch.pages()}
                    verwandt = [f"- [{q.als_text(seiten[k].title)}](/{k}.md)" for k in d.get("verwandt", []) if k in seiten]
                    body = q.quelle_koerper(p.body, {**d, **texte}, wiki.cfg["lint"]["claims_section"], wortlaut)
                    if verwandt:
                        body = q.abschnitt_setzen(body, "Verbindungen", "\n".join(verwandt))
                    anzahl = sum(len(v) for v in texte.values())
                    frisch.replace_body(pid, body, f"Aus dem Vorschlag der Rolle lesen übernommen ({vermerk['modell']}); {anzahl} Anker am Original geprüft", modell=vermerk)
            except core.WikiError as exc:
                raise Fehler(str(exc), 422) from None
            return self.fertig_melden(pid, hinweise)

    def fertig_melden(self, pid: str, hinweise: list[str]) -> dict:
        s = self.sitzung
        frisch = s.wiki()
        hinweise = hinweise + s.neu_bauen(frisch)
        return {"ok": True, "id": pid, **s.befunde(frisch, s.seite(frisch, pid)), "hinweise": hinweise}

    # -------------------------------------------------------------- Ablauf

    def starten(self, daten: dict):
        s = self.sitzung
        name = daten.get("typ")
        if not isinstance(name, str) or name not in TYPEN:
            raise Fehler("Diesen Auftragstyp gibt es nicht", 422)
        typ = TYPEN[name]
        try:                                  # sonst scheitert erst das Schreiben im Hintergrund, nach dem Modellaufruf
            json.dumps(daten, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise Fehler("Die Eingabe enthält Zeichen, die sich nicht als UTF-8 speichern lassen", 422) from None
        with s.lock:
            self.laden()
            wiki = s.wiki()
            ziel = rollen.loese(s.cfg(), typ.rolle)
            titel = rollen.ROLLEN[typ.rolle]
            if ziel["harness"] == "sitzung":
                raise Fehler(f"Die Rolle „{titel}“ läuft in der Sitzung; der Server kann sie nicht bedienen. "
                             "Stelle sie in den Einstellungen auf claude, opencode oder direkt.", 422)
            if ziel["gesperrt"]:
                raise Fehler(f"Rolle „{titel}“: {ziel['gesperrt']}", 422)
            eingabe = typ.pruefen(s, wiki, daten)
            kennung = f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
            d = {"version": VERSION, "id": kennung, "typ": name, "zustand": "wartet", "zeit": time.time(), "fortschritt": "Wartet auf einen freien Platz.",
                 "fehler": "", "ziel": eingabe["ziel"], "titel": eingabe["titel"], "anweisung": eingabe["anweisung"],
                 "rolle": {k: str(ziel.get(k) or "") for k in ("harness", "anbieter", "modell")},
                 "gesendet": eingabe["gesendet"], "warnungen": eingabe["warnungen"], "aenderungen": [], "dauer_s": None}
            self.schreiben(d)
            self.auftraege[kennung] = d
            self.abbrueche[kennung] = threading.Event()
            sperre = self.rollensperren.setdefault(typ.rolle, threading.Lock())
        faden = threading.Thread(target=self.lauf, args=(d, typ, eingabe, sperre), daemon=True, name=f"okfw-auftrag-{kennung}")
        self.laeufe = [t for t in self.laeufe if t.is_alive()] + [faden]
        faden.start()
        return 202, self.kurz(d)

    def setze(self, d: dict, **felder) -> None:
        with self.sitzung.lock:
            d.update(felder)
            try:
                self.schreiben(d)
            except (OSError, ValueError, Fehler) as exc:   # der Auftrag bleibt im Speicher sichtbar
                print(f"okfw serve: Auftrag {d['id']} nicht geschrieben: {exc}", file=sys.stderr)

    def lauf(self, d: dict, typ, eingabe: dict, sperre: threading.Lock) -> None:
        s = self.sitzung
        abbruch = self.abbrueche[d["id"]]
        start = time.time()
        try:
            while not sperre.acquire(timeout=0.1):   # je Rolle ein Auftrag zugleich: Anbieter begrenzen ihre Plätze
                if abbruch.is_set():
                    raise Abgebrochen()
            try:
                if abbruch.is_set():
                    raise Abgebrochen()
                self.setze(d, zustand="laeuft", fortschritt="Das Modell arbeitet.")
                antwort = typ.laufen(eingabe, Lauf(lambda text: self.setze(d, fortschritt=str(text)), abbruch))
            finally:
                sperre.release()
            if abbruch.is_set():
                raise Abgebrochen()
            with s.lock:
                ergebnis = typ.auswerten(s, s.wiki(), eingabe, antwort)
                erlaubt = [a for a in ergebnis["aenderungen"] if a.get("art") in ARTEN]
                weg = [str(a.get("art")) for a in ergebnis["aenderungen"] if a.get("art") not in ARTEN]
                if weg:
                    ergebnis["warnungen"].append("Weggelassen, weil es diese Art von Änderung nicht gibt: " + ", ".join(weg))
                aenderungen = [{"id": f"a{i}", "status": "offen", "braucht": [], **a} for i, a in enumerate(erlaubt, 1)]
                # Was das Modell tatsächlich war: rollen.py nennt es; sonst bleibt, was beim Start galt
                rolle = {k: str(antwort.get(k) or d["rolle"][k]) for k in d["rolle"]}
                self.setze(d, zustand="fertig", fortschritt="", aenderungen=aenderungen, warnungen=ergebnis["warnungen"], rolle=rolle,
                           dauer_s=antwort.get("dauer_s") or round(time.time() - start, 1))
        except Abgebrochen:
            self.setze(d, zustand="abgebrochen", fortschritt="", fehler="Abgebrochen. Nichts wurde übernommen.")
        except subprocess.TimeoutExpired as exc:   # str() enthält die Befehlszeile; zum Browser geht nur der Satz
            print(f"okfw serve: {type(exc).__name__}: {exc}", file=sys.stderr)
            self.setze(d, zustand="gescheitert", fortschritt="", fehler=ZEITLIMIT_TEXT)
        except (Fehler, s.core.WikiError, rollen.Fehler) as exc:
            self.setze(d, zustand="gescheitert", fortschritt="", fehler=str(exc))
        except Exception as exc:  # noqa: BLE001
            print(f"okfw serve: {type(exc).__name__}: {exc}", file=sys.stderr)   # Einzelheiten nur ans Terminal
            self.setze(d, zustand="gescheitert", fortschritt="", fehler=f"Interner Fehler: {type(exc).__name__}; Einzelheiten stehen im Terminal")
        finally:
            self.abbrueche.pop(d["id"], None)

    def beenden(self) -> None:
        """Der Server endet: Laufende Modellprozesse hängen in eigenen Prozessgruppen und enden nicht von selbst mit."""
        for abbruch in list(self.abbrueche.values()):
            abbruch.set()
        for faden in self.laeufe:
            faden.join(5)

    # -------------------------------------------------------------- Lesen

    @staticmethod
    def kurz(d: dict) -> dict:
        """Für die Liste, ohne die Texte der Änderungen."""
        return {**{k: d.get(k) for k in ("id", "typ", "zustand", "fortschritt", "fehler", "ziel", "titel", "rolle", "zeit", "dauer_s")},
                "offen": sum(a["status"] == "offen" for a in d["aenderungen"])}

    def finden(self, kennung) -> dict:
        self.laden()
        if not isinstance(kennung, str) or kennung not in self.auftraege:
            raise Fehler("Diesen Auftrag gibt es nicht", 404)
        return self.auftraege[kennung]

    def lesen(self, kennung):
        with self.sitzung.lock:
            d = self.finden(kennung)
            return 200, {**d, "aenderungen": [ARTEN[a["art"]].zum_lesen(self.sitzung, a) for a in d["aenderungen"]]}

    def liste(self):
        with self.sitzung.lock:
            self.laden()
            offen = [d for d in self.auftraege.values() if not _erledigt(d)]
            return 200, {"auftraege": [self.kurz(d) for d in sorted(offen, key=lambda d: d["zeit"], reverse=True)]}

    def herkunft(self, seite: str) -> dict | None:
        """Der jüngste Textvorschlag zu einer Seite, auch verworfen: Wird sein Text von Hand hineinkopiert, bleibt es sein Text."""
        with self.sitzung.lock:
            self.laden()
            for d in sorted(self.auftraege.values(), key=lambda d: d["zeit"], reverse=True):
                for a in d["aenderungen"]:
                    if a["art"] == "koerper" and a["ziel"] == seite:
                        return {"sha": self.sitzung.core.body_sha256(a["daten"]["neu"]), **d["rolle"]}
        return None

    # -------------------------------------------------------------- Handeln

    def abbrechen(self, daten: dict):
        with self.sitzung.lock:
            d = self.finden(daten.get("auftrag"))
            abbruch = self.abbrueche.get(d["id"])
            if d["zustand"] not in AKTIV or abbruch is None:
                raise Fehler("Der Auftrag läuft nicht mehr", 409, {"auftrag": self.kurz(d)})
            abbruch.set()
            return 200, {"ok": True}

    def aenderung(self, d: dict, kennung) -> dict:
        if not isinstance(kennung, str):
            raise Fehler("aenderung fehlt")
        for a in d["aenderungen"]:
            if a["id"] == kennung:
                return a
        raise Fehler("Diese Änderung gibt es nicht", 404)

    def annehmen(self, daten: dict):
        """Mit ziel=textfeld schreibt nichts: Die Person überarbeitet den Text und speichert selbst."""
        with self.sitzung.lock:
            d = self.finden(daten.get("auftrag"))
            a = self.aenderung(d, daten.get("aenderung"))
            if a["status"] != "offen":
                raise Fehler("Über diese Änderung ist schon entschieden", 409)
            if daten.get("ziel") == "textfeld":
                if a["art"] != "koerper":
                    raise Fehler("Nur ein Text lässt sich ins Textfeld übernehmen", 422)
                a["status"] = "im_textfeld"
                self.schreiben(d)
                return 200, {"ok": True, "neu": a["daten"]["neu"]}
            angenommen = []
            for teil in self.reihenfolge(d, a):
                antwort = ARTEN[teil["art"]].annehmen(self.sitzung, d, teil)
                teil["status"] = "angenommen"
                self.schreiben(d)
                angenommen.append(teil["id"])
            self.eingang_nach_entscheidung(d)
            return 200, {**antwort, "angenommen": angenommen}

    def reihenfolge(self, d: dict, a: dict) -> list[dict]:
        """Erst was die Änderung braucht und noch offen ist, dann sie selbst. Was verworfen ist, fehlt ihr: dann nichts."""
        folge: list[dict] = []
        unterwegs: set[str] = set()

        def besuche(teil):
            if teil["id"] in unterwegs:
                raise Fehler("Die Änderungen dieses Vorschlags hängen im Kreis aneinander", 422)
            if teil["status"] == "angenommen" or teil in folge:
                return
            if teil["status"] != "offen":
                raise Fehler(f"Diese Änderung braucht {teil['id']}, und die ist schon verworfen", 409)
            unterwegs.add(teil["id"])
            for kennung in teil["braucht"]:
                besuche(self.aenderung(d, kennung))
            unterwegs.discard(teil["id"])
            folge.append(teil)

        besuche(a)
        return folge

    def verwerfen(self, daten: dict):
        """Ohne aenderung der ganze Auftrag, auch ein gescheiterter: Er verschwindet aus der Liste."""
        with self.sitzung.lock:
            d = self.finden(daten.get("auftrag"))
            if d["zustand"] in AKTIV:
                raise Fehler("Der Auftrag läuft noch; erst abbrechen", 409)
            betroffen = d["aenderungen"] if daten.get("aenderung") is None else [self.aenderung(d, daten["aenderung"])]
            for a in betroffen:
                if a["status"] == "offen":
                    a["status"] = "verworfen"
            if daten.get("aenderung") is None:
                d["gesehen"] = True
            self.schreiben(d)
            self.eingang_nach_entscheidung(d)
            return 200, {"ok": True}

    def eingang_nach_entscheidung(self, d: dict) -> None:
        if _erledigt(d):
            self.eingang_pflegen(auch=tuple(a["daten"]["eingang"] for a in d["aenderungen"] if a["art"] in ("quelle", "seite")))
