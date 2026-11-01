#!/usr/bin/env -S uv run --no-config --quiet --script
# /// script
# requires-python = ">=3.11"
# ///
"""Offizielle Literatur-APIs mit Fundprotokoll: Zugangsweg 2 der Literaturrecherche.

  api.py suche <dienst> "<suchstring>" --lauf DIR [--max 20] [--by AKTEUR]   # --max bis 2000, seitenweise
  api.py eintrag DIR --weg modellvorschlag|container|websuche --db DB --query Q --treffer JSON [--by A]
  api.py zusammenfuehren DIR [--json]
  api.py aufloesen DIR <nr> --doi D | --pmid P [--begruendung "…"]   # ohne Begründung nur Vergleich
  api.py fundweg DIR <nr|doi|pmid> --seite sources/<slug> [--root PROJEKT]
  api.py retraktion [--pmid P] [--doi D]
  api.py abstract --doi D | --pmid P  --out DATEI.txt
  api.py volltext <pmcid> | --xml JATS.xml  --out DATEI.txt
  api.py anker ENTWURF VOLLTEXT
  api.py pruefen DIR
  api.py archivieren DIR
  api.py dienste

Dienste: pubmed, europepmc, crossref, openalex, semanticscholar, arxiv, cellar (SPARQL).
Nur die Hosts in HOSTS (Port 443), auch bei Weiterleitungen; alles andere lehnt das Skript ab.
Schlüsselköpfe gehen bei einer Weiterleitung nie an einen anderen Host. Antworten über MAX_ANTWORT
(64 MB, auch entpackt) sind ein Fehler. Ersetzt curl für diese Dienste: Hostliste und Protokoll
liegen an einer Stelle. Jeder Suchaufruf hängt eine Zeile an DIR/api.jsonl an,
auch wenn er scheitert (bis dahin geholte Treffer bleiben im Eintrag). Der Suchstring wird nie
verändert: Filter gehören in die Syntax des Dienstes.
Schlüssel nur aus der Umgebung, nie im Protokoll: NCBI_API_KEY (Abstand 0,11 s statt 0,34 s),
OPENALEX_API_KEY, S2_API_KEY (auch SEMANTIC_SCHOLAR_API_KEY). Ablage mit `archivieren`: im ignorierten
Projektordner protokoll/recherche, sonst unter OKFW_RECHERCHE_PROTOKOLL (Voreinstellung ~/.claude/recherche-protokoll).
Metadaten für Quellenkarten holt der Wiki-Kern (okfw.py new source --doi/--pmid/--arxiv).
"""
import argparse
import gzip
import html
import http.client
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

UA = "okf-recherche/1.0 (api.py)"
TOOL = "okf-recherche"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
KERN_PFAD = Path(__file__).resolve().parents[2] / "okfw" / "okfw.py"
KERN = (f'uv run --no-config --quiet "{KERN_PFAD}"' if not re.search(r'["$`\\]', str(KERN_PFAD))
        else f"uv run --no-config --quiet {shlex.quote(str(KERN_PFAD))}")

HOSTS = {  # Host → kleinster Abstand zwischen zwei Anfragen in Sekunden (E-Utilities mit Schlüssel: abstand())
    "eutils.ncbi.nlm.nih.gov": 0.34,
    "www.ebi.ac.uk": 0.1,
    "api.crossref.org": 0.1,
    "api.openalex.org": 0.1,
    "api.semanticscholar.org": 1.1,
    "export.arxiv.org": 3.0,
    "publications.europa.eu": 0.2,
}
PORTS: dict[str, set[int]] = {}  # Host → zusätzlich erlaubte Ports; leer: nur der Standardport 443
MAX_ANTWORT = 64 * 1024 * 1024  # Bytes, roh und entpackt
KOPF_NUR_SELBER_HOST = ("key", "token", "authorization", "cookie")  # Teile von Kopfnamen, die nie zu einem anderen Host gehen
WEGE = ("tooluniverse", "api", "websuche", "container", "modellvorschlag")
FUNDWEG_ARTEN = ("suche", "modellvorschlag", "material", "person", "zitatverfolgung")  # wie FUNDWEG_ARTEN in okfw.py
PFLICHT = ("weg", "db", "query", "at", "by", "treffer")
KENNUNGEN = ("doi", "pmid", "pmcid", "arxiv", "celex")
_zuletzt: dict[str, float] = {}
MAX_TREFFER = 2000  # Obergrenze für --max
ANZEIGE = 50  # so viele Treffer zeigt `suche` auf dem Bildschirm; das Protokoll hat alle
SEITE = {"pubmed": 1000, "esummary": 200, "europepmc": 1000, "crossref": 1000, "openalex": 100,
         "semanticscholar": 100, "arxiv": 100}  # Treffer je Anfrage
S2_MAX = 1000  # Semantic Scholar liefert bei der Relevanzsuche höchstens die ersten 1000
HTTP_WIEDERHOLUNGEN = 2  # bei 429 und 5xx, mit Retry-After oder wachsender Pause
WIEDERHOLBAR = (429, 500, 502, 503, 504)
ABBRUCH = (http.client.HTTPException, ConnectionError, TimeoutError, urllib.error.URLError)
ABBRUCH_WIEDERHOLUNGEN = 2  # abgebrochene Verbindung, IncompleteRead (OpenAlex bricht große Antworten oft ab)
STANDARD_BY = "claude-code/recherche"  # Akteur im Protokoll, wenn weder --by noch RECHERCHE_BY gesetzt ist
GEHEIM = ("NCBI_API_KEY", "OPENALEX_API_KEY", "S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY")
GEHEIME_PARAMETER = ("api_key", "apikey", "key", "access_token", "token", "sig", "signature",
                     "secret", "client_secret", "password", "passwd", "pwd", "auth", "credential",
                     "credentials")  # URL-Parameter mit Schlüsseln
_GEHEIME_PARAMETER_RE = re.compile(r"(?i)([?&](?:" + "|".join(GEHEIME_PARAMETER) + r")=)[^&\s\"']*")


class Abgelehnt(Exception):
    """Host steht nicht auf der Liste der offiziellen APIs."""


class ZuGross(ValueError):
    """Antwort überschreitet MAX_ANTWORT."""


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _port(teile) -> int | None:
    try:
        return teile.port or 443
    except ValueError:
        return None


def pruefe_host(url: str) -> str:
    teile = urllib.parse.urlsplit(url)
    host = teile.hostname or ""
    if teile.scheme != "https" or host not in HOSTS:
        raise Abgelehnt(f"{host or url}: kein freigegebener API-Host (erlaubt: {', '.join(sorted(HOSTS))})")
    port = _port(teile)
    if port != 443 and port not in PORTS.get(host, ()):
        raise Abgelehnt(f"{host}:{teile.netloc.rsplit(':', 1)[-1]}: Port nicht freigegeben (erlaubt: 443)")
    return host


class _NurErlaubte(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        pruefe_host(newurl)
        neu = super().redirect_request(req, fp, code, msg, headers, newurl)
        alt, ziel = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if neu is not None and (alt.hostname, _port(alt)) != (ziel.hostname, _port(ziel)):
            for kopf in (neu.headers, neu.unredirected_hdrs):
                for name in [n for n in kopf if any(t in n.lower() for t in KOPF_NUR_SELBER_HOST)]:
                    del kopf[name]
        return neu


_OPENER = urllib.request.build_opener(_NurErlaubte)


def abstand(host: str) -> float:
    if host == "eutils.ncbi.nlm.nih.gov" and os.environ.get("NCBI_API_KEY"):
        return 0.11
    return HOSTS[host]


def pause_nach(e: urllib.error.HTTPError, wiederholung: int) -> float:
    nach = (e.headers.get("Retry-After", "") if e.headers else "").strip()
    return min(int(nach), 30) if nach.isdigit() else 3.0 * wiederholung


def hole(url: str, accept: str | None = None, headers: dict | None = None, timeout: int = 30) -> bytes:
    host = pruefe_host(url)
    warte = abstand(host) - (time.monotonic() - _zuletzt.get(host, 0.0))
    if warte > 0:
        time.sleep(warte)
    kopf = {"User-Agent": UA, "Accept-Encoding": "gzip", **({"Accept": accept} if accept else {}), **(headers or {})}
    http = abbruch = 0
    while True:
        _zuletzt[host] = time.monotonic()
        try:
            with _OPENER.open(urllib.request.Request(url, headers=kopf), timeout=timeout) as r:
                roh = r.read(MAX_ANTWORT + 1)
                if len(roh) > MAX_ANTWORT:
                    raise ZuGross(f"{host}: Antwort größer als {MAX_ANTWORT // 2**20} MB")
                if (getattr(r, "headers", None) or {}).get("Content-Encoding") != "gzip":
                    return roh
                with gzip.GzipFile(fileobj=io.BytesIO(roh)) as z:
                    voll = z.read(MAX_ANTWORT + 1)
                if len(voll) > MAX_ANTWORT:
                    raise ZuGross(f"{host}: entpackte Antwort größer als {MAX_ANTWORT // 2**20} MB")
                return voll
        except urllib.error.HTTPError as e:
            if e.code in WIEDERHOLBAR and http < HTTP_WIEDERHOLUNGEN:
                http += 1
                time.sleep(pause_nach(e, http))
                continue
            raise
        except ABBRUCH:  # abgebrochene Verbindung, IncompleteRead, Zeitüberschreitung
            if abbruch < ABBRUCH_WIEDERHOLUNGEN:
                abbruch += 1
                time.sleep(1.0 * abbruch)
                continue
            raise


def hole_json(url: str, accept: str = "application/json", **kw):
    return json.loads(hole(url, accept=accept, **kw))


def ohne_schluessel(url: str) -> str:
    teile = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(teile.query) if k.lower() not in GEHEIME_PARAMETER]
    return urllib.parse.urlunsplit(teile._replace(query=urllib.parse.urlencode(q)))


def eutils(befehl: str, **p) -> str:
    p["tool"] = TOOL
    if os.environ.get("NCBI_API_KEY"):
        p["api_key"] = os.environ["NCBI_API_KEY"]
    return EUTILS + befehl + ".fcgi?" + urllib.parse.urlencode(p)


def schwaerzen(x):
    """Schlüssel aus der Umgebung und `api_key=…` in allen Zeichenketten unkenntlich machen."""
    geheim = [f for k in GEHEIM if len(v := os.environ.get(k, "")) >= 4
              for f in {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v)}]

    def eins(t: str) -> str:
        for v in geheim:
            t = t.replace(v, "***")
        return _GEHEIME_PARAMETER_RE.sub(r"\1***", t)
    if isinstance(x, str):
        return eins(x)
    if isinstance(x, list):
        return [schwaerzen(i) for i in x]
    if isinstance(x, dict):
        return {k: schwaerzen(v) for k, v in x.items()}
    return x


# ---------------------------------------------------------------- Kennungen

def norm_doi(doi) -> str:
    doi = str(doi or "").strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I)
    return doi.lower()


def norm_pmcid(pmcid) -> str:
    s = str(pmcid or "").strip().upper()
    s = re.sub(r"^.*/PMC/ARTICLES/", "", s).rstrip("/")
    return s if s.startswith("PMC") or not s else f"PMC{s}" if s.isdigit() else s


def norm_pmid(pmid) -> str:
    s = str(pmid or "").strip().rstrip("/")
    return s.rsplit("/", 1)[-1] if s else ""


def norm_titel(titel) -> str:
    s = unicodedata.normalize("NFKD", str(titel or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def treffer(rang: int, **felder) -> dict:
    felder["doi"] = norm_doi(felder.get("doi"))
    felder["pmid"] = norm_pmid(felder.get("pmid"))
    felder["pmcid"] = norm_pmcid(felder.get("pmcid"))
    if felder.get("titel"):
        felder["titel"] = re.sub(r"<[^>]+>", "", " ".join(html.unescape(str(felder["titel"])).split()))
    if felder.get("jahr"):
        m = re.search(r"\d{4}", str(felder["jahr"]))
        felder["jahr"] = int(m.group()) if m else None
    return {"rang": rang, **{k: v for k, v in felder.items() if v not in (None, "", [])}}


# ---------------------------------------------------------------- Dienste

def seite(st: dict, url: str) -> None:
    st["seiten"] += 1
    st["url"] = st["url"] or url


def s_pubmed(q, n, st):
    st["parameter"] = {"sort": "relevance", "retmax": n}
    liste, start = st["liste"], 0
    while len(liste) < n:
        url = eutils("esearch", db="pubmed", term=q, retmax=min(n - len(liste), SEITE["pubmed"]), retstart=start,
                     retmode="json", sort="relevance")
        seite(st, url)
        r = hole_json(url)["esearchresult"]
        st["n_gesamt"] = int(r.get("count", 0))
        ids = r.get("idlist", [])
        if not ids:
            break
        for i in range(0, len(ids), SEITE["esummary"]):
            teil = ids[i:i + SEITE["esummary"]]
            s = hole_json(eutils("esummary", db="pubmed", id=",".join(teil), retmode="json"))["result"]
            for pmid in teil:
                d = s.get(pmid, {})
                a = {x.get("idtype"): x.get("value") for x in d.get("articleids", [])}
                liste.append(treffer(len(liste) + 1, pmid=pmid, doi=a.get("doi"), pmcid=a.get("pmc"),
                                     titel=d.get("title"), jahr=d.get("pubdate"),
                                     quelle=d.get("fulljournalname") or d.get("source")))
        start += len(ids)
        if start >= st["n_gesamt"]:
            break


def s_europepmc(q, n, st):
    st["parameter"] = {"pageSize": min(n, SEITE["europepmc"]), "resultType": "lite"}
    liste, cursor = st["liste"], "*"
    while len(liste) < n:
        url = EPMC + "search?" + urllib.parse.urlencode(
            {"query": q, "format": "json", "pageSize": min(n - len(liste), SEITE["europepmc"]),
             "resultType": "lite", "cursorMark": cursor})
        seite(st, url)
        r = hole_json(url)
        st["n_gesamt"] = r.get("hitCount")
        ergebnis = r.get("resultList", {}).get("result", [])
        for x in ergebnis:
            liste.append(treffer(len(liste) + 1, pmid=x.get("pmid"), pmcid=x.get("pmcid"), doi=x.get("doi"),
                                 titel=x.get("title"), jahr=x.get("pubYear"), quelle=x.get("journalTitle"),
                                 epmc=f"{x.get('source')}:{x.get('id')}", oa=x.get("isOpenAccess") == "Y"))
        weiter = r.get("nextCursorMark")
        if not ergebnis or not weiter or weiter == cursor:
            break
        cursor = weiter


def s_crossref(q, n, st):
    st["parameter"] = {"rows": min(n, SEITE["crossref"])}
    liste, cursor = st["liste"], "*"
    while len(liste) < n:
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
            {"query": q, "rows": min(n - len(liste), SEITE["crossref"]),
             "select": "DOI,title,issued,container-title,type", "cursor": cursor})
        seite(st, url)
        m = hole_json(url)["message"]
        st["n_gesamt"] = m.get("total-results")
        items = m.get("items", [])
        for x in items:
            liste.append(treffer(len(liste) + 1, doi=x.get("DOI"), titel=(x.get("title") or [""])[0],
                                 jahr=((x.get("issued") or {}).get("date-parts") or [[None]])[0][0],
                                 quelle=(x.get("container-title") or [""])[0], typ=x.get("type")))
        cursor = m.get("next-cursor")
        if not items or not cursor:
            break


def s_openalex(q, n, st):
    """Seiten per cursor nur mit OPENALEX_API_KEY (ohne Schlüssel antwortet der Dienst auf Seite 2 mit 401);
    ohne Schlüssel per page=, jede Seite so groß wie die erste."""
    key = os.environ.get("OPENALEX_API_KEY")
    groesse = min(n, SEITE["openalex"])
    st["parameter"] = {"per-page": groesse, "seiten_per": "cursor" if key else "page"}
    liste, cursor = st["liste"], "*"
    while len(liste) < n:
        p = {"search": q, "per-page": groesse if not key else min(n - len(liste), groesse),
             "select": "id,doi,title,publication_year,ids,primary_location"}
        p.update({"cursor": cursor, "api_key": key} if key else {"page": len(liste) // groesse + 1})
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(p)
        seite(st, url)
        r = hole_json(url)
        st["n_gesamt"] = (r.get("meta") or {}).get("count")
        ergebnis = r.get("results", [])
        for x in ergebnis:
            ids = x.get("ids") or {}
            quelle = ((x.get("primary_location") or {}).get("source") or {}).get("display_name")
            liste.append(treffer(len(liste) + 1, doi=x.get("doi"), pmid=ids.get("pmid"), pmcid=ids.get("pmcid"),
                                 titel=x.get("title"), jahr=x.get("publication_year"), quelle=quelle,
                                 openalex=str(x.get("id", "")).rsplit("/", 1)[-1]))
        cursor = (r.get("meta") or {}).get("next_cursor")
        if not ergebnis or (key and not cursor) or (st["n_gesamt"] is not None and len(liste) >= st["n_gesamt"]):
            break


def s_semanticscholar(q, n, st):
    if n > S2_MAX:
        st["parameter"]["hinweis"] = f"Semantic Scholar liefert bei der Suche höchstens {S2_MAX} Treffer"
        n = S2_MAX
    st["parameter"]["limit"] = min(n, SEITE["semanticscholar"])
    key = os.environ.get("S2_API_KEY") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    liste = st["liste"]
    while len(liste) < n:
        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
            {"query": q, "limit": min(n - len(liste), SEITE["semanticscholar"]), "offset": len(liste),
             "fields": "title,year,externalIds,venue"})
        seite(st, url)
        r = hole_json(url, headers={"x-api-key": key} if key else None)
        st["n_gesamt"] = r.get("total")
        daten = r.get("data") or []
        for x in daten:
            e = x.get("externalIds") or {}
            liste.append(treffer(len(liste) + 1, doi=e.get("DOI"), pmid=e.get("PubMed"),
                                 pmcid=e.get("PubMedCentral"), arxiv=e.get("ArXiv"), titel=x.get("title"),
                                 jahr=x.get("year"), quelle=x.get("venue"), s2=x.get("paperId")))
        if not daten or r.get("next") is None:
            break


def s_arxiv(q, n, st):
    st["parameter"] = {"max_results": min(n, SEITE["arxiv"]), "sortBy": "relevance"}
    ns = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/",
          "x": "http://arxiv.org/schemas/atom"}
    liste = st["liste"]
    while len(liste) < n:
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"search_query": q, "max_results": min(n - len(liste), SEITE["arxiv"]), "start": len(liste),
             "sortBy": "relevance"})
        seite(st, url)
        root = ET.fromstring(hole(url, timeout=90))
        total = root.findtext("o:totalResults", "", ns)
        st["n_gesamt"] = int(total) if total.isdigit() else None
        eintraege = root.findall("a:entry", ns)
        for e in eintraege:
            aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1])
            liste.append(treffer(len(liste) + 1, arxiv=aid, doi=e.findtext("x:doi", "", ns),
                                 titel=e.findtext("a:title", "", ns), jahr=e.findtext("a:published", "", ns),
                                 quelle="arXiv"))
        if not eintraege or (st["n_gesamt"] is not None and len(liste) >= st["n_gesamt"]):
            break


def s_cellar(q, n, st):
    url = "https://publications.europa.eu/webapi/rdf/sparql?" + urllib.parse.urlencode(
        {"query": q, "format": "application/sparql-results+json"})
    st["parameter"] = {"max": n, "hinweis": "n_gesamt unbekannt; LIMIT steht im SPARQL-Suchstring"}
    seite(st, url)
    rows = hole_json(url, accept="application/sparql-results+json").get("results", {}).get("bindings", [])

    def wert(row, *namen):
        return next((row[k]["value"] for k in namen if k in row), None)
    st["liste"].extend(treffer(i, celex=wert(r, "celex"), titel=wert(r, "title", "titel"), jahr=wert(r, "date", "datum"),
                               url=wert(r, "work", "uri", "s"), quelle="EUR-Lex/Cellar")
                       for i, r in enumerate(rows[:n], 1))


DIENSTE = {
    "pubmed": (s_pubmed, "PubMed-Syntax, z. B. metformin[tiab] AND 2020:2026[dp]"),
    "europepmc": (s_europepmc, "Europe-PMC-Syntax, z. B. \"drug safety\" AND PUB_YEAR:[2020 TO 2026] AND OPEN_ACCESS:y"),
    "crossref": (s_crossref, "Freitext (bibliografische Suche)"),
    "openalex": (s_openalex, "Freitext; optional OPENALEX_API_KEY (Seiten per cursor)"),
    "semanticscholar": (s_semanticscholar, "Freitext; optional S2_API_KEY (sonst gedrosselt); höchstens 1000 Treffer"),
    "arxiv": (s_arxiv, "arXiv-Syntax, z. B. ti:\"retrieval augmented\" AND cat:cs.CL"),
    "cellar": (s_cellar, "SPARQL auf den Cellar des EU-Amts für Veröffentlichungen; Variablen celex, title, date, work"),
}


# ---------------------------------------------------------------- Protokoll

def kein_symlink(datei: Path) -> None:
    """Der Lauf-Ordner kann aus einem fremden Wiki stammen; ein Symlink lenkte das Schreiben sonst aus ihm hinaus."""
    if datei.is_symlink():
        sys.exit(f"{datei} ist ein symbolischer Link: nicht geschrieben")


def liste_laden(datei: Path, pflicht: str) -> list[dict]:
    """Liste von Zuordnungen aus dem Lauf-Ordner, jede mit dem Feld pflicht; sonst Abbruch mit Text."""
    daten = json.loads(datei.read_text(encoding="utf-8"))
    if not isinstance(daten, list) or not all(isinstance(e, dict) and pflicht in e for e in daten):
        sys.exit(f"{datei}: erwartet eine Liste von Einträgen mit {pflicht}")
    return daten


def anhaengen(datei: Path, eintrag: dict) -> None:
    kein_symlink(datei)
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


def cmd_suche(a) -> int:
    lauf = Path(a.lauf).expanduser()
    n = max(1, min(a.max, MAX_TREFFER))
    st = {"liste": [], "n_gesamt": None, "url": None, "parameter": {}, "seiten": 0}
    eintrag = {"v": 1, "lauf": lauf.name, "weg": "api", "db": f"api:{a.dienst}", "werkzeug": "api.py suche",
               "query": a.query, "parameter": {}, "at": jetzt(), "by": a.by, "n_gesamt": None, "n_geliefert": 0,
               "url": None, "treffer": [], "fehler": None}
    try:
        DIENSTE[a.dienst][0](a.query, n, st)
    except Abgelehnt as e:
        eintrag["fehler"] = f"abgelehnt: {e}"
    except Exception as e:  # jeder Fehlschlag gehört ins Protokoll; bisherige Treffer bleiben
        eintrag["fehler"] = f"{type(e).__name__}: {e}"
        if isinstance(e, urllib.error.HTTPError):  # die Antwort nennt oft den Grund (Kontingent, Schlüssel nötig)
            try:
                eintrag["fehler"] += " | " + " ".join(e.read(400).decode("utf-8", "replace").split())
            except (OSError, ValueError, AttributeError):
                pass
    liste = st["liste"][:n]
    if st["seiten"] > 1:
        st["parameter"]["seiten"] = st["seiten"]
    if a.max > MAX_TREFFER:
        st["parameter"]["max_begrenzt_auf"] = MAX_TREFFER
    eintrag.update({"parameter": st["parameter"], "n_gesamt": st["n_gesamt"], "n_geliefert": len(liste),
                    "url": ohne_schluessel(st["url"]) if st["url"] else None, "treffer": liste})
    eintrag = schwaerzen(eintrag)
    anhaengen(lauf / "api.jsonl", eintrag)
    kurz = {k: eintrag[k] for k in ("db", "query", "at", "n_gesamt", "n_geliefert", "fehler")}
    kurz["treffer"] = [{k: t[k] for k in ("rang", *KENNUNGEN, "titel", "jahr") if k in t}
                       for t in eintrag["treffer"][:ANZEIGE]]
    if len(eintrag["treffer"]) > ANZEIGE:
        kurz["hinweis"] = f"nur die ersten {ANZEIGE} von {len(eintrag['treffer'])} Treffern angezeigt; alle stehen in {lauf / 'api.jsonl'}"
    print(json.dumps(kurz, ensure_ascii=False, indent=1))
    return 2 if eintrag["fehler"] else 0


def cmd_eintrag(a) -> int:
    try:
        liste = json.loads(Path(a.treffer).read_text(encoding="utf-8") if Path(a.treffer).is_file() else a.treffer)
    except (ValueError, OSError) as e:
        sys.exit(f"--treffer: JSON-Liste oder Datei erwartet ({e})")
    eintrag = {"v": 1, "lauf": Path(a.lauf).name, "weg": a.weg, "db": a.db, "werkzeug": a.werkzeug or a.weg,
               "query": a.query, "parameter": {}, "at": a.at or jetzt(), "by": a.by, "n_gesamt": a.n_gesamt,
               "n_geliefert": len(liste), "url": ohne_schluessel(a.url) if a.url else a.url, "fehler": None,
               "treffer": [treffer(t.pop("rang", i), **t) for i, t in enumerate(liste, 1)]}
    anhaengen(Path(a.lauf).expanduser() / f"{a.weg}.jsonl", schwaerzen(eintrag))
    print(f"{a.weg}.jsonl: 1 Eintrag, {len(liste)} Treffer")
    return 0


def lade_aufloesungen(lauf: Path) -> dict[str, dict]:
    datei = lauf / "aufloesungen.json"
    return {a["url"]: a for a in liste_laden(datei, "url")} if datei.is_file() else {}


def cmd_aufloesen(a) -> int:
    """Einen Websuche-Treffer über Weg 2 einer Kennung zuordnen; die Zuordnung prüft Claude am Vergleich."""
    lauf = Path(a.lauf).expanduser()
    kandidaten = liste_laden(lauf / "kandidaten.json", "nr")
    k = next((k for k in kandidaten if str(k["nr"]) == str(a.kandidat)), None)
    if k is None or not k.get("url") or "websuche" not in k.get("wege", ()):
        sys.exit(f"Kandidat {a.kandidat}: kein Websuche-Treffer mit URL")
    if a.doi:
        m = hole_json(f"https://api.crossref.org/works/{urllib.parse.quote(norm_doi(a.doi))}")["message"]
        api = {"via": f"api:crossref works/{norm_doi(a.doi)}", "api_titel": (m.get("title") or [""])[0],
               "api_url": ((m.get("resource") or {}).get("primary") or {}).get("URL") or m.get("URL")}
    elif a.pmid:
        d = hole_json(eutils("esummary", db="pubmed", id=norm_pmid(a.pmid), retmode="json"))["result"][norm_pmid(a.pmid)]
        api = {"via": f"api:pubmed esummary {norm_pmid(a.pmid)}", "api_titel": d.get("title"),
               "api_url": f"https://pubmed.ncbi.nlm.nih.gov/{norm_pmid(a.pmid)}/"}
    else:
        sys.exit("--doi oder --pmid angeben")
    if a.nur_zeigen or not a.begruendung:
        roh = next((t for e in lade(lauf)[0] if e["weg"] == "websuche" for t in e["treffer"] if t.get("url") == k["url"]), {})
        print(f"Websuche: {roh.get('titel')}\n          {k['url']}\nAPI:      {api['api_titel']}\n          {api['api_url']}")
        print("Nichts festgehalten. Passt es, mit --begruendung \"…\" wiederholen.")
        return 0
    roh = next((t for e in lade(lauf)[0] if e["weg"] == "websuche" for t in e["treffer"] if t.get("url") == k["url"]), {})
    eintrag = {"url": k["url"], "doi": norm_doi(a.doi) or None, "pmid": norm_pmid(a.pmid) or None, **api,
               "websuche_titel": roh.get("titel"), "begruendung": a.begruendung, "at": jetzt(), "by": a.by}
    alle = lade_aufloesungen(lauf)
    alle[k["url"]] = eintrag
    kein_symlink(lauf / "aufloesungen.json")
    (lauf / "aufloesungen.json").write_text(json.dumps(list(alle.values()), ensure_ascii=False, indent=1) + "\n",
                                            encoding="utf-8")
    print(f"Websuche: {roh.get('titel')}\n          {k['url']}\nAPI:      {api['api_titel']}\n          {api['api_url']}")
    print("Zuordnung festgehalten (aufloesungen.json); passt sie nicht, Eintrag dort löschen und neu zusammenführen.")
    return 0


def _uebersprungen(fehler: list[str], ort: str, grund: str) -> None:
    fehler.append(f"{ort}: {grund}")
    print(f"Hinweis: {ort} übersprungen ({grund})", file=sys.stderr)


def lade(lauf: Path) -> tuple[list[dict], list[str]]:
    eintraege, fehler = [], []
    for datei in sorted(lauf.glob("*.jsonl")):
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            if not zeile.strip():
                continue
            ort = f"{datei.name}:{nr}"
            try:
                e = json.loads(zeile)
            except ValueError as exc:
                fehler.append(f"{ort}: kein JSON ({exc})")
                continue
            if not isinstance(e, dict):
                _uebersprungen(fehler, ort, "kein JSON-Objekt")
                continue
            fehlt = [k for k in PFLICHT if k not in e]
            if fehlt:
                fehler.append(f"{ort}: Felder fehlen: {', '.join(fehlt)}")
                continue
            if e["weg"] not in WEGE:
                fehler.append(f"{ort}: weg {e['weg']!r} ist keiner von {', '.join(WEGE)}")
            if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?(\.\d+)?(Z|[+-]\d\d:\d\d)$", str(e["at"])):
                fehler.append(f"{ort}: at {e['at']!r} ist kein ISO-8601-Zeitpunkt mit Offset")
            if not re.match(r"^(human:[\w.-]+|process:[\w.-]+|[\w.-]+/[\w.:-]+)$", str(e["by"])):
                fehler.append(f"{ort}: by {e['by']!r} folgt nicht der Akteurskonvention (z. B. claude-code/fund-api)")
            if not isinstance(e["treffer"], list):
                fehler.append(f"{ort}: treffer ist keine Liste")
                continue
            falsch = [k for k in ("weg", "db", "query") if not isinstance(e[k], str)]
            if falsch:
                _uebersprungen(fehler, ort, f"{', '.join(falsch)} ist keine Zeichenkette")
                continue
            if not all(isinstance(t, dict) for t in e["treffer"]):
                _uebersprungen(fehler, ort, "treffer enthält Einträge, die kein Objekt sind")
                continue
            e["_ort"] = ort
            eintraege.append(e)
    return eintraege, fehler


def cmd_pruefen(a) -> int:
    eintraege, fehler = lade(Path(a.lauf).expanduser())
    for f in fehler:
        print(f"✗ {f}")
    print(f"{len(eintraege)} Einträge gültig, {len(fehler)} Fehler")
    return 1 if fehler else 0


def schluessel(t: dict) -> list[str]:
    keys = [f"{k}:{str(t[k]).lower()}" for k in KENNUNGEN if t.get(k)]
    titel = norm_titel(t.get("titel"))
    if len(titel) > 20:
        keys.append(f"titel:{titel}|{t.get('jahr') or ''}")
    if t.get("url") and not keys:
        keys.append(f"url:{t['url']}")
    return keys


def zusammenfuehren(lauf: Path) -> tuple[list[dict], list[str]]:
    eintraege, fehler = lade(lauf)
    eltern: dict[int, int] = {}

    def wurzel(i):
        while eltern.setdefault(i, i) != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i
    alle, nach_schluessel = [], {}
    aufl = lade_aufloesungen(lauf)
    for e in eintraege:
        for t in e["treffer"]:
            if e["weg"] == "websuche" and t.get("url") in aufl:
                z = aufl[t["url"]]
                t = {**t, **{x: z[x] for x in ("doi", "pmid") if z.get(x)}, "aufgeloest_via": z.get("via", "?")}
            i = len(alle)
            alle.append((e, t))
            for k in schluessel(t):
                if k in nach_schluessel:
                    eltern[wurzel(i)] = wurzel(nach_schluessel[k])
                else:
                    nach_schluessel[k] = i
    gruppen: dict[int, list] = {}
    for i, paar in enumerate(alle):
        gruppen.setdefault(wurzel(i), []).append(paar)
    kandidaten = []
    for paare in gruppen.values():
        k = {"titel": None, "jahr": None, **{x: None for x in KENNUNGEN}, "url": None, "funde": []}
        for e, t in sorted(paare, key=lambda p: p[0]["weg"] == "websuche"):  # Metadaten der APIs zuerst
            for feld in ("titel", "jahr", *KENNUNGEN, "url"):
                k[feld] = k[feld] or t.get(feld)
            fund = {"weg": e["weg"], "db": e["db"], "query": e["query"], "at": e["at"], "by": e["by"],
                    "rang": t.get("rang"), "ort": e["_ort"],
                    **({"aufgeloest_via": t["aufgeloest_via"]} if t.get("aufgeloest_via") else {}),
                    **({"at_aus_auftrag": True} if e.get("at_aus_auftrag") else {})}
            if not any(f["db"] == fund["db"] and f["query"] == fund["query"] for f in k["funde"]):
                k["funde"].append(fund)
        k["funde"].sort(key=lambda f: f["at"])
        k["wege"] = sorted({f["weg"] for f in k["funde"]})
        k["aufgeloest"] = any(k[x] for x in KENNUNGEN)
        k["nur_websuche"] = k["wege"] == ["websuche"]
        k["nur_modell"] = k["wege"] == ["modellvorschlag"]
        kandidaten.append({x: v for x, v in k.items() if v not in (None, [])} | {"funde": k["funde"]})
    kandidaten.sort(key=lambda k: (-len({f["db"] for f in k["funde"]}), min(f["rang"] or 999 for f in k["funde"])))
    for nr, k in enumerate(kandidaten, 1):
        k["nr"] = nr
    return kandidaten, fehler


def kennung(k: dict) -> str:
    return next((f"{x}:{k[x]}" for x in KENNUNGEN if k.get(x)), "– (unaufgelöst)")


def cmd_zusammenfuehren(a) -> int:
    lauf = Path(a.lauf).expanduser()
    kandidaten, fehler = zusammenfuehren(lauf)
    kein_symlink(lauf / "kandidaten.json")
    (lauf / "kandidaten.json").write_text(json.dumps(kandidaten, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(kandidaten, ensure_ascii=False, indent=1))
        return 1 if fehler else 0
    for f in fehler:
        print(f"✗ {f}")
    eintraege, _ = lade(lauf)
    print("| Weg | Datenbank/Werkzeug | Suchstring | Zeitpunkt | gesamt | geliefert |\n|---|---|---|---|---|---|")
    for e in eintraege:
        q = e["query"] if len(e["query"]) <= 90 else e["query"][:87] + "…"
        n = "Fehler: " + e["fehler"] if e.get("fehler") else e["n_gesamt"] if e.get("n_gesamt") is not None else "–"
        at = e["at"] + (" (Start laut Auftrag)" if e.get("at_aus_auftrag") else "")
        print(f"| {e['weg']} | {e['db']} | `{q}` | {at} | {n} | {len(e['treffer'])} |")
    print(f"\n| Nr | Titel | Jahr | Kennung | Wege | DBs | Funde (Datenbank: Rang) |\n|---|---|---|---|---|---|---|")
    for k in kandidaten:
        titel = (k.get("titel") or "–")[:80]
        funde = ", ".join(f"{f['db']}: {f['rang']}" for f in k["funde"])
        print(f"| {k['nr']} | {titel} | {k.get('jahr', '–')} | {kennung(k)} | {', '.join(k['wege'])} | "
              f"{len({f['db'] for f in k['funde']})} | {funde} |")
    n_mehr = sum(len(k["wege"]) > 1 for k in kandidaten)
    n_db = sum(len({f["db"] for f in k["funde"]}) > 1 for k in kandidaten)
    n_web = sum(k["nur_websuche"] for k in kandidaten)
    n_web_offen = sum(k["nur_websuche"] and not k["aufgeloest"] for k in kandidaten)
    print(f"\nKandidaten: {len(kandidaten)} (über ≥2 Wege: {n_mehr}; über ≥2 Datenbanken: {n_db}; nur Websuche: "
          f"{n_web}, davon unaufgelöst: {n_web_offen}; Modellvorschläge: {sum(k['nur_modell'] for k in kandidaten)})")
    print(f"Datei: {lauf / 'kandidaten.json'}")
    return 1 if fehler else 0


FUND_PFLICHT = ("db", "query", "at", "by")


def cmd_fundweg(a) -> int:
    lauf = Path(a.lauf).expanduser()
    datei = lauf / "kandidaten.json"
    kandidaten = liste_laden(datei, "nr") if datei.is_file() else zusammenfuehren(lauf)[0]
    wahl = a.kandidat.strip()
    k = next((k for k in kandidaten if str(k["nr"]) == wahl
              or norm_doi(wahl) in (k.get("doi"), ) or norm_pmid(wahl) == k.get("pmid")), None)
    if k is None:
        sys.exit(f"Kandidat {wahl!r} nicht in {datei}")
    if k.get("nur_websuche", True) and not k.get("aufgeloest"):
        sys.exit(f"Kandidat {k['nr']} kommt nur aus der Websuche und ist nicht über Weg 1 oder 2 aufgelöst")
    funde = k.get("funde")
    if not isinstance(funde, list) or not all(isinstance(f, dict) and all(isinstance(f.get(x), str) for x in FUND_PFLICHT)
                                              for f in funde):
        sys.exit(f"Kandidat {k['nr']}: funde braucht je Eintrag {', '.join(FUND_PFLICHT)} als Text")
    kern = KERN + (f" --root {shlex.quote(a.root)}" if a.root else "")
    # wie okfw.py fundwege_aus_lauf: ein weg, der selbst eine Fundweg-Art ist, bleibt sie; alle anderen Wege sind eine Suche
    arten = [f["weg"] if f.get("weg") in FUNDWEG_ARTEN else "suche" for f in k["funde"]]
    for art, f in zip(arten, k["funde"], strict=True):
        if art == "zitatverfolgung" and not f.get("von"):
            sys.exit(f"Kandidat {k['nr']}: Fund zitatverfolgung ohne von, die Quelle dazu fehlt im Lauf; Fundweg von Hand "
                     "setzen: fundweg <karte> --art zitatverfolgung --von <slug>")
    for i, (art, f) in enumerate(zip(arten, k["funde"], strict=True)):
        teile = ["fundweg", a.seite, "--art", art, "--db", f["db"], "--query", f["query"],
                 "--at", f["at"], "--by", f["by"]] + (["--von", str(f["von"])] if f.get("von") else []) \
            + (["--add"] if i else [])
        if f.get("at_aus_auftrag"):
            print("# Zeitpunkt = Startzeit laut Auftrag (Agent ohne Uhr)")
        if f.get("aufgeloest_via"):
            print(f"# Websuche-Treffer, aufgelöst über {' '.join(str(f['aufgeloest_via']).split())} (aufloesungen.json)")
        print(f"{kern} {shlex.join(teile)}")
    return 0


# ---------------------------------------------------------------- Prüfungen und Volltext

def cmd_retraktion(a) -> int:
    if not (a.pmid or a.doi):
        sys.exit("--pmid oder --doi angeben")
    pmid = norm_pmid(a.pmid)
    if not pmid:  # PMID zur DOI über esearch; ohne PubMed-Eintrag bleibt nur Crossref
        r = hole_json(eutils("esearch", db="pubmed", term=f'"{norm_doi(a.doi)}"[doi]', retmode="json"))
        pmid = next(iter(r["esearchresult"].get("idlist", [])), "")
    out = {"pmid": pmid or None, "doi": norm_doi(a.doi) or None, "publikationstypen": [], "verweise": [],
           "crossref": None}
    if pmid:
        url = eutils("efetch", db="pubmed", id=pmid, retmode="xml")
        art = ET.fromstring(hole(url)).find(".//PubmedArticle")
        if art is None:
            sys.exit(f"PMID {pmid} nicht gefunden")
        out["publikationstypen"] = [p.text for p in art.findall(".//PublicationType") if p.text]
        for c in art.findall(".//CommentsCorrections"):
            typ = c.get("RefType", "")
            if re.search(r"Retraction|Concern|Erratum|Republished|Update", typ):
                out["verweise"].append({"typ": typ, "pmid": c.findtext("PMID"), "quelle": c.findtext("RefSource")})
    if a.doi:
        m = hole_json(f"https://api.crossref.org/works/{urllib.parse.quote(norm_doi(a.doi))}")["message"]
        out["crossref"] = [{"typ": u.get("type"), "doi": u.get("DOI"), "label": u.get("label"),
                            "datum": ((u.get("updated") or {}).get("date-parts") or [[None]])[0]}
                           for u in m.get("updated-by", [])]
    pt = set(out["publikationstypen"])
    refs = {v["typ"] for v in out["verweise"]}
    cr = " ".join(str(u["typ"]).lower() for u in out["crossref"] or [])
    out["zurueckgezogen"] = ("Retracted Publication" in pt or "RetractionIn" in refs
                             or bool(re.search(r"retraction|withdrawal|removal", cr)))
    out["bedenken"] = "ExpressionOfConcernIn" in refs or "concern" in cr
    out["korrektur"] = bool(refs & {"ErratumIn", "CorrectedandRepublishedIn"}) or bool(
        re.search(r"correction|erratum|corrigendum", cr))
    out["ist_hinweis"] = bool(pt & {"Retraction of Publication", "Published Erratum", "Expression of Concern"})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 3 if out["zurueckgezogen"] or out["bedenken"] else 0


def _text(e) -> str:
    return " ".join("".join(e.itertext()).split()) if e is not None else ""


def _ohne_bloecke(e) -> str:
    """Text eines Absatzes ohne eingebettete Tabellen und Abbildungen (die stehen als eigene Blöcke)."""
    teile: list[str] = []

    def lauf(x, wurzel=False):
        if not wurzel and x.tag in ("table-wrap", "fig", "table-wrap-group", "fig-group"):
            teile.append(" " + (x.tail or ""))
            return
        teile.append(x.text or "")
        for c in x:
            lauf(c)
        if not wurzel:
            teile.append(x.tail or "")
    lauf(e, True)
    return " ".join("".join(teile).split())


def cmd_abstract(a) -> int:
    """Abstract laut Europe PMC als Text; für Quellen ohne freien Volltext (Modus Abstract in /lesen)."""
    if bool(a.doi) == bool(a.pmid):
        sys.exit("--doi oder --pmid angeben")
    frage = f'DOI:"{norm_doi(a.doi)}"' if a.doi else f"EXT_ID:{norm_pmid(a.pmid)} AND SRC:MED"
    url = EPMC + "search?" + urllib.parse.urlencode({"query": frage, "resultType": "core", "format": "json", "pageSize": 1})
    treffer = (hole_json(url).get("resultList") or {}).get("result") or []
    text = re.sub(r"<[^>]+>", "", (treffer[0].get("abstractText") or "") if treffer else "").strip()
    if not text:
        print("Kein Abstract bei Europe PMC.", file=sys.stderr)
        return 1
    ziel = Path(a.out).expanduser()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(f"# {treffer[0].get('title', '')}\nQuelle: Europe PMC, Abstract, abgerufen {jetzt()}.\n\n"
                    f"## Abstract\n\n[Abs. 1] {text}\n", encoding="utf-8")
    print(f"{ziel}: {len(text)} Zeichen")
    return 0


def cmd_volltext(a) -> int:
    pmcid = norm_pmcid(a.pmcid) if a.pmcid else None
    if a.xml:  # vorhandene JATS-Datei (z. B. aus einem Rohquellen-Paket) nur umwandeln
        url, roh = str(Path(a.xml).resolve()), Path(a.xml).read_bytes()
    elif pmcid:
        url = f"{EPMC}{pmcid}/fullTextXML"
        roh = hole(url, accept="application/xml")
    else:
        sys.exit("PMCID oder --xml DATEI angeben")
    root = ET.fromstring(roh)
    xlink = "{http://www.w3.org/1999/xlink}href"
    lic = root.find(".//license")
    ref = next((x.text.strip() for x in root.iter() if str(x.tag).endswith("license_ref") and x.text), None)
    link = next((x.get(xlink) for x in lic.iter() if x.get(xlink)), None) if lic is not None else None
    lizenz = {"url": (lic.get(xlink) if lic is not None else None) or ref or link,
              "text": _text(lic).removeprefix(ref or "").strip()}
    zeilen = [f"# {_text(root.find('.//article-title'))}",
              f"Quelle: {'Europe PMC fullTextXML ' + pmcid if pmcid else url}, umgewandelt {jetzt()}. "
              f"Lizenz laut XML: {lizenz['url'] or '–'}", ""]
    zaehler = {"abschnitte": 0, "absaetze": 0, "tabellen": 0, "abbildungen": 0}
    stand = {"kopf": None, "nr": {}}

    def kopf(titel):  # Überschrift erst ausgeben, wenn darunter etwas steht; Absatzzähler je Abschnitt
        if stand["kopf"] != titel:
            if titel not in stand["nr"]:
                zaehler["abschnitte"] += 1
            stand["kopf"] = titel
            zeilen.extend([f"## {titel}", ""])

    def absatz(titel, text):
        if text:
            kopf(titel)
            stand["nr"][titel] = stand["nr"].get(titel, 0) + 1
            zeilen.extend([f"[Abs. {stand['nr'][titel]}] {text}", ""])
            zaehler["absaetze"] += 1

    def block(titel, x):  # Tabellen mit Zeilen, Abbildungen mit Legende, an ihrer Stelle im Text
        kopf(titel)
        label = re.sub(r"^(Fig(ure)?|Abb|Table|Tab)\.?\s*", "", _text(x.find("label")) or x.get("id") or "?")
        if x.tag == "fig":
            zaehler["abbildungen"] += 1
            zeilen.extend([f"[Abb. {label}] {_text(x.find('caption'))}", ""])
            return
        zaehler["tabellen"] += 1
        zeilen.append(f"[Tab. {label}] {_text(x.find('caption'))}")
        for tr in x.iter("tr"):
            zeilen.append("  | " + " | ".join(_text(c) for c in tr if c.tag in ("td", "th")) + " |")
        for fn in x.iter("table-wrap-foot"):
            zeilen.append(f"  Fußnote: {_text(fn)}")
        zeilen.append("")

    def inhalt(knoten, titel):
        """Kinder in Lesereihenfolge: Absätze ohne eingebettete Tabellen, Listen, Kästen, Tabellen, Abbildungen."""
        for c in knoten:
            if c.tag == "p":
                absatz(titel, _ohne_bloecke(c))
                for x in c.iter():
                    if x.tag in ("table-wrap", "fig"):
                        block(titel, x)
            elif c.tag == "list":
                for li in c.iter("list-item"):
                    absatz(titel, "• " + _ohne_bloecke(li))
            elif c.tag in ("disp-quote", "boxed-text", "statement", "def-list"):
                absatz(titel, _ohne_bloecke(c))
            elif c.tag in ("table-wrap", "fig", "table-wrap-group", "fig-group"):
                for x in ([c] if c.tag in ("table-wrap", "fig") else list(c)):
                    block(titel, x)
            elif c.tag == "sec":
                inhalt(c, (f"{titel} / " if titel != "Text" else "") + (_text(c.find("title")) or "Abschnitt"))

    for ab in root.findall(".//front//abstract"):
        inhalt(ab, "Abstract" + (f" ({ab.get('abstract-type')})" if ab.get("abstract-type") else ""))
    body = root.find(".//body")
    if body is not None:
        inhalt(body, "Text")
    # Erklärungen (Interessenkonflikte, Förderung, Daten, Danksagung, Fußnoten); das Literaturverzeichnis nicht
    for x in [*root.iter("notes"), *root.findall(".//back/ack"), *root.findall(".//back/fn-group"),
              *root.findall(".//back/sec")]:
        titel = _text(x.find("title")) or {"ack": "Danksagung", "fn-group": "Fußnoten"}.get(x.tag, "Angaben")
        inhalt(x, f"Angaben / {titel}")
        for fn in x.findall("fn"):
            absatz(f"Angaben / {titel}", _ohne_bloecke(fn))
    ziel = Path(a.out).expanduser()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text("\n".join(zeilen), encoding="utf-8")
    if not a.xml:
        ziel.with_suffix(".xml").write_bytes(roh)
    print(json.dumps({"pmcid": pmcid, "url": url, "text": str(ziel), "xml": str(ziel.with_suffix(".xml")),
                      "lizenz": lizenz, **zaehler}, ensure_ascii=False, indent=1))
    return 0 if zaehler["absaetze"] else 2


def _norm_anker(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "")
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"[„“”‟″]", '"', re.sub(r"[‚‘’‛′]", "'", s))
    return " ".join(s.split()).casefold()


def cmd_anker(a) -> int:
    roh = Path(a.entwurf).read_text(encoding="utf-8")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", roh, re.S)
    try:
        entwurf = json.loads(m.group(1) if m else roh)
    except ValueError as e:
        sys.exit(f"Entwurf ist kein JSON und enthält keinen ```json-Block ({e})")
    zeilen = Path(a.volltext).read_text(encoding="utf-8").splitlines()
    text, zeile_von, ort_von = [], [], []
    abschnitt, marke = "–", ""
    for nr, z in enumerate(zeilen, 1):
        if z.startswith("## "):
            abschnitt, marke = z[3:], ""
        m = re.match(r"^\[(Abs\. \d+|Tab\. [^\]]+|Abb\. [^\]]+)\]", z)
        if m:
            marke = m.group(1)
        elif z and not z.startswith("  "):  # Tabellenzeilen ("  |") gehören zur letzten Tabelle
            marke = marke if z.startswith("[") else ""
        n = _norm_anker(z) + " "
        text.append(n)
        zeile_von.extend([nr] * len(n))
        ort_von.extend([f"{abschnitt}, {marke}" if marke else abschnitt] * len(n))
    voll = "".join(text)

    def zelle(s) -> str:
        return str(s or "–").replace("|", "/")
    print("| Nr | Art | Fundstelle laut Entwurf | Anker | Zeile | Fundstelle im Volltext (geprüft) |\n|---|---|---|---|---|---|")
    fehlt = lang = 0
    posten = ([("aussage", x) for x in entwurf.get("kernaussagen") or []]
              + [("zahl", x) for x in entwurf.get("zahlen") or []]
              + [("einschränkung", x) for x in entwurf.get("einschraenkungen") or []])
    for i, (art, x) in enumerate(posten, 1):
        anker = str(x.get("anker") or "")
        pos = voll.find(_norm_anker(anker)) if anker.strip() else -1
        if len(anker.split()) > 25:
            lang += 1
        if pos < 0:
            fehlt += 1
            print(f"| {i} | {art} | {zelle(x.get('fundstelle'))} | ✗ nicht gefunden | – | – |")
        else:
            print(f"| {i} | {art} | {zelle(x.get('fundstelle'))} | ✓ | {zeile_von[pos]} | {zelle(ort_von[pos])[:80]} |")
    print(f"\nAnker: {len(posten) - fehlt}/{len(posten)} wörtlich gefunden"
          + (f"; {lang} Anker länger als 25 Wörter (kürzen)" if lang else ""))
    return 1 if fehlt or not posten else 0


# ---------------------------------------------------------------- Ablage

def protokoll_ziel(name: str) -> Path:
    """<git-Wurzel>/protokoll/recherche/<lauf>, wenn git den Ordner ignoriert; sonst global, dort
    unter OKFW_RECHERCHE_PROTOKOLL, falls gesetzt."""
    try:
        wurzel = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        if wurzel and subprocess.run(["git", "-C", wurzel, "check-ignore", "-q", f"protokoll/recherche/{name}"],
                                     capture_output=True).returncode == 0:
            return Path(wurzel) / "protokoll" / "recherche" / name
    except OSError:
        pass
    basis = os.environ.get("OKFW_RECHERCHE_PROTOKOLL")
    return (Path(basis).expanduser() if basis else Path.home() / ".claude" / "recherche-protokoll") / Path.cwd().name / name


def cmd_archivieren(a) -> int:
    lauf = Path(a.lauf).expanduser()
    ziel = protokoll_ziel(lauf.name)
    dateien = [p for p in lauf.iterdir() if p.suffix in (".jsonl", ".json") and p.is_file() and not p.is_symlink()]
    try:
        ziel.mkdir(parents=True, exist_ok=True)
        for p in dateien:
            shutil.copy2(p, ziel / p.name)
    except OSError as e:
        sys.exit(f"{ziel}: {e}. In der Bash-Sandbox ist der globale Ordner gesperrt; Aufruf außerhalb der Sandbox wiederholen.")
    print(f"{len(dateien)} Dateien (Protokolle, Auftrag, Kandidaten; keine Volltexte) → {ziel}")
    return 0


def cmd_dienste(a) -> int:
    for name, (_, hilfe) in DIENSTE.items():
        print(f"{name:16} api:{name:16} {hilfe}")
    print("\nHosts: " + ", ".join(sorted(HOSTS)))
    return 0


def baue_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="befehl", required=True)
    s = sub.add_parser("suche", help="einen Dienst abfragen und protokollieren")
    s.add_argument("dienst", choices=list(DIENSTE))
    s.add_argument("query", help="exakter Suchstring in der Syntax des Dienstes")
    s.add_argument("--lauf", required=True, help="Lauf-Ordner (Protokoll DIR/api.jsonl)")
    s.add_argument("--max", type=int, default=20, help=f"Treffer insgesamt (seitenweise geholt, höchstens {MAX_TREFFER})")
    s.add_argument("--by", default=os.environ.get("RECHERCHE_BY", STANDARD_BY))
    s.set_defaults(f=cmd_suche)
    e = sub.add_parser("eintrag", help="Protokolleintrag für Websuche, Container oder Modellvorschlag")
    e.add_argument("lauf")
    e.add_argument("--weg", required=True, choices=[w for w in WEGE if w != "api"])
    e.add_argument("--db", required=True)
    e.add_argument("--query", required=True)
    e.add_argument("--treffer", required=True, help="JSON-Liste oder Datei, je Treffer doi/pmid/pmcid/titel/jahr/url")
    e.add_argument("--by", required=True)
    e.add_argument("--at")
    e.add_argument("--werkzeug")
    e.add_argument("--url")
    e.add_argument("--n-gesamt", type=int)
    e.set_defaults(f=cmd_eintrag)
    z = sub.add_parser("zusammenfuehren", help="Protokolle prüfen, deduplizieren, Kandidaten zeigen")
    z.add_argument("lauf")
    z.add_argument("--json", action="store_true")
    z.set_defaults(f=cmd_zusammenfuehren)
    u = sub.add_parser("aufloesen", help="Websuche-Treffer über Crossref oder PubMed einer Kennung zuordnen")
    u.add_argument("lauf")
    u.add_argument("kandidat", help="Nr aus kandidaten.json")
    u.add_argument("--doi")
    u.add_argument("--pmid")
    u.add_argument("--begruendung", help="warum Websuche-Treffer und API-Eintrag dieselbe Arbeit sind (ohne: nur zeigen)")
    u.add_argument("--nur-zeigen", action="store_true", help="Vergleich zeigen, nichts festhalten")
    u.add_argument("--by", default=os.environ.get("RECHERCHE_BY", STANDARD_BY))
    u.set_defaults(f=cmd_aufloesen)
    f = sub.add_parser("fundweg", help="okfw-Befehle für den Fundweg eines Kandidaten")
    f.add_argument("lauf")
    f.add_argument("kandidat", help="Nr aus kandidaten.json, DOI oder PMID")
    f.add_argument("--seite", required=True, help="z. B. sources/<slug>")
    f.add_argument("--root", help="Projektordner für okfw (z. B. eine Kopie im Scratchpad)")
    f.set_defaults(f=cmd_fundweg)
    r = sub.add_parser("retraktion", help="Rückzug, Bedenken, Korrektur laut PubMed und Crossref")
    r.add_argument("--pmid")
    r.add_argument("--doi")
    r.set_defaults(f=cmd_retraktion)
    v = sub.add_parser("abstract", help="Abstract aus Europe PMC als Text")
    v.add_argument("--doi")
    v.add_argument("--pmid")
    v.add_argument("--out", required=True)
    v.set_defaults(f=cmd_abstract)
    v = sub.add_parser("volltext", help="OA-Volltext aus Europe PMC als Text mit Abschnitten und Absatznummern")
    v.add_argument("pmcid", nargs="?")
    v.add_argument("--xml", help="lokale JATS-Datei umwandeln statt abrufen")
    v.add_argument("--out", required=True)
    v.set_defaults(f=cmd_volltext)
    n = sub.add_parser("anker", help="wörtliche Anker eines Lese-Entwurfs im Volltext suchen")
    n.add_argument("entwurf")
    n.add_argument("volltext")
    n.set_defaults(f=cmd_anker)
    c = sub.add_parser("pruefen", help="Protokolldateien eines Laufs gegen das Schema prüfen")
    c.add_argument("lauf")
    c.set_defaults(f=cmd_pruefen)
    w = sub.add_parser("archivieren", help="Protokolle eines Laufs dauerhaft ablegen")
    w.add_argument("lauf")
    w.set_defaults(f=cmd_archivieren)
    d = sub.add_parser("dienste", help="Dienste, Syntax, Hosts")
    d.set_defaults(f=cmd_dienste)
    return p


def main() -> int:
    a = baue_parser().parse_args()
    try:
        return a.f(a)
    except Abgelehnt as exc:
        sys.exit(f"abgelehnt: {exc}")
    except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as exc:
        sys.exit(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
