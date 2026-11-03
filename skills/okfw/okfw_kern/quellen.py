"""Metadaten von außen: Netzzugriff, CSL aus Crossref, DataCite, PubMed und arXiv, Rückzugshinweise, Kennungen, Zitierhelfer."""

import datetime as dt
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .basis import FOOTNOTE_DEF_RE, VERSION, WikiError, as_list, one_line


CROSSREF_TYPES = {"journal-article": "article-journal", "proceedings-article": "paper-conference",
                  "posted-content": "article", "book-chapter": "chapter"}
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


_NICHT_ABK = {"EU", "EG", "EWG", "EC", "EEC", "US", "USA", "UK"}
_PARAGRAF_RE = re.compile(r"§§?\s*\d+[a-z]?(?:\s*(?:,|–|-|bis|und)\s*\d+[a-z]?)*")
_EU_AKT_RE = re.compile(r"(?:Durchführungs|Delegierte |Implementing |Delegated )?(?:Verordnung|Richtlinie|Beschluss|Regulation"
                        r"|Directive|Decision)\s*\((?:EU|EG|EWG|EC|EEC)(?:, Euratom)?\)\s*(?:Nr\.\s*|No\.?\s*)?\d+/\d+")
_FUELLWORT = {"von", "vom", "ohne", "der", "die", "das", "des", "den", "dem", "und", "oder", "zu", "zur", "zum", "für", "mit",
              "über", "im", "in", "auf", "bei", "nach", "vor", "aus", "an", "am", "ein", "eine", "of", "the", "and", "for",
              "to", "on", "a", "an", "with", "from", "by"}


def _abkuerzung(text: str) -> str:
    """'SGB IX', 'ApBetrO', 'BAG UB' – Abkürzung, wie sie in Klammern hinter Gesetzes- und Organisationsnamen steht; sonst leer."""
    text = text.strip()
    if not 2 <= len(text) <= 15 or text in _NICHT_ABK or sum(c.isupper() for c in text) < 2:
        return ""
    return text if re.fullmatch(r"[A-ZÄÖÜ][\wÄÖÜäöüß.\-/]*(?: [A-ZÄÖÜ0-9][\wÄÖÜäöüß.\-/]*)?", text) else ""


def kurzname(name: str) -> str:
    """Organisation im Klammerbeleg nach APA 7 kurz: 'Bundesarbeitsgemeinschaft … (BIH)' und 'ABDA – Bundesvereinigung …'
    werden zu 'BIH' und 'ABDA'; die volle Form steht im Literaturverzeichnis. Ohne Abkürzung bleibt der Name."""
    name = one_line(name)
    m = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", name)
    if m and _abkuerzung(m.group(2)):
        return m.group(2).strip()
    head = re.split(r"\s+[–—-]\s+", name, maxsplit=1)
    return head[0] if len(head) == 2 and _abkuerzung(head[0]) else name


def kurztitel(title: str) -> str:
    """Kurztitel (CSL title-short) für Klammerbelege ohne Autor und für Rechtsquellen, nach APA 7 die ersten Wörter.

    Gesetze mit Abkürzung werden zu 'CanG' oder '§ 33b EStG', EU-Rechtsakte zu 'Verordnung (EU) 2024/1689'; sonst
    endet der Titel am ersten Doppelpunkt, Gedankenstrich, Klammer oder Komma und hat höchstens vier Wörter. Leer, wenn
    der Titel schon kurz ist.
    """
    title = one_line(title)
    vorne = re.split(r":\s", title, maxsplit=1)[0]
    for m in re.finditer(r"\(([^()]+)\)", vorne):
        abk = next((t for t in reversed(re.split(r"\s*[–—;,]\s*|\s+-\s+", m.group(1))) if _abkuerzung(t)), "")
        if abk:
            paragraf = _PARAGRAF_RE.search(title)
            kurz = f"{paragraf.group(0)} {abk}" if paragraf else abk
            return kurz if kurz != title else ""
    eu = _EU_AKT_RE.match(title)
    if eu:
        return eu.group(0) if eu.group(0) != title else ""
    kurz = re.split(r":\s|\s+[–—]\s+|\s+\(|,\s", title, maxsplit=1)[0].strip() or title
    woerter, gezaehlt = [], 0
    for wort in kurz.split():
        gezaehlt += bool(re.search(r"\w", wort))   # „/“ und „&“ zählen nicht als Wort
        if gezaehlt > 4:
            while len(woerter) > 1 and woerter[-1].lower() in _FUELLWORT | {"/", "&", "–"}:
                woerter.pop()
            kurz = " ".join(woerter)
            break
        woerter.append(wort)
    return kurz if kurz != title else ""


def kurzbeleg(csl: dict, narrative: bool = False) -> str:
    """APA-7-Kurzbeleg aus CSL: 'Morath et al., 2024' oder erzählend 'Morath et al. (2024)'."""
    people = csl.get("author") or csl.get("editor") or []
    names = [a.get("family") or kurzname(a.get("literal", "?")) for a in as_list(people) if isinstance(a, dict)] or ["o. A."]
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


def capture_file(item, source_path: str | None = None) -> tuple[Path, str | None]:
    """(lokaler Pfad, Pfad im Quell-Repository) aus --file PFAD oder PFAD=REPO-PFAD; ohne REPO-PFAD gilt --source-path."""
    if isinstance(item, (tuple, list)):
        local, repo = item
    else:
        local, repo = str(item), None
        if "=" in local and not Path(local).expanduser().exists():
            local, _, repo = local.partition("=")
    return Path(local).expanduser(), repo or source_path
