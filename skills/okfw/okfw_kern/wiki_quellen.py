"""Wiki: Quellen aufnehmen und belegen (Abruf, Rückzug, capture, Fundweg, Lizenz, Rechte)."""

import datetime as dt
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
from pathlib import Path

from .basis import (ACTOR_RE, FENCE_RE, FOOTNOTE_REF_RE, FOOTNOTE_TEXT_RE, LIST_ITEM_RE, Page, SLUG_RE, SOURCE_TYPE,
                    WikiError, _manifest_lesen, as_list, is_timestamp, now_iso, one_line, sha256_of)
from .rechte import (LICENSE_CLASSES, RIGHTS_ACTIONS, RIGHTS_MATRIX, UNKNOWN_LICENSE_RE, classify_license,
                     license_name_from, license_parts, parse_right)
from .konfig import FUNDWEG_ARTEN
from .quellen import (arxiv_csl, capture_file, crossref_csl, crossref_notices, datacite_csl, datacite_rights,
                      http_bytes, http_json, kennungen, notice_kind, pubmed_csl, pubmed_notices)


class WikiQuellen:
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

    def source_cards(self) -> list[Page]:
        """Quellenkarten: type source oder eine Karte im Quellenordner (z. B. Altlasten mit anderem type)."""
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        return [p for p in self.pages() if p.fm and (p.type == SOURCE_TYPE or p.folder == folder)]

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
