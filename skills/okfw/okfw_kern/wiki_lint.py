"""Wiki: Lint-Regeln."""

import datetime as dt
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import yaml

from .basis import (ACTOR_RE, CODE_RE, CONTRADICTION_RE, DATENKLASSEN, FIGURE_CAPTION_RE, FIGURES_DIR, FOOTNOTE_DEF_RE,
                    FOOTNOTE_REF_RE, FigureError, Finding, H2_RE, LINK_RE, Page, SLUG_RE, SOURCE_TYPE, STATUS,
                    VERIFIED_HASH, WikiError, _manifest_lesen, as_datetime, as_list, is_empty, is_timestamp,
                    sha256_of)
from .frontmatter import MODELL_ARTEN, MODELL_UNBEKANNT, load_yaml, split_frontmatter
from .rechte import LICENSE_CLASSES, LICENSE_HINT, RIGHTS_ACTIONS, RIGHT_WORDS
from .pfad import PFAD_TYPE
from .konfig import FUNDWEG_ARTEN, SEVERITY, SEVERITY_ORDER, TYPE_DEFAULTS, unknown_config
from .quellen import UNDATED_RE, claim_lines


class WikiLint:
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
            if p.type == PFAD_TYPE:   # ein Pfad verweist nur: Er macht aus einer verwaisten Karte keine verbundene
                continue
            out = edges.setdefault(p.path.resolve(), set())
            if q.path.resolve() not in out:
                out.add(q.path.resolve())
                inbound[q.path.resolve()] += 1
        self._ereignis_modelle: dict[str, set] = {}
        self._abbildung_modell: dict[str, str] = {}   # jüngstes Ereignis zählt: Wer das Bild von Hand ersetzt, hebt es auf
        for ev in self.read_events():
            for v in as_list(ev.get("modelle")):
                if isinstance(v, dict) and v.get("modell"):
                    self._ereignis_modelle.setdefault(str(ev.get("page")), set()).add(v["modell"])
            seite = str(ev.get("page"))
            if seite.startswith(f"{FIGURES_DIR}/"):
                modelle = [v["modell"] for v in as_list(ev.get("modelle")) if isinstance(v, dict) and v.get("modell")]
                if modelle:
                    self._abbildung_modell[seite.removeprefix(f"{FIGURES_DIR}/")] = modelle[-1]
                else:
                    self._abbildung_modell.pop(seite.removeprefix(f"{FIGURES_DIR}/"), None)
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
        self._lint_modelle(p, gen if isinstance(gen, dict) else {}, add)
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
            add("order_invalid", path, f"order {order!r} ist keine Zahl; die Reihenfolge unter „Zuerst lesen“ braucht eine Zahl")
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
                "Inhalt: Niemand sonst hat diesen Text geprüft; gegenprüfen lassen, zum Beispiel mit /lesen --pruefen")
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

        if fm.get("superseded_by"):
            r = self.resolve(path, str(fm["superseded_by"]))
            if r is not None and not r.exists():
                add("superseded_missing", path, f"Nachfolger {fm['superseded_by']} existiert nicht")

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
        if p.type == PFAD_TYPE:
            self._lint_pfad(p, add)
        elif inbound[me] == 0 and (not isolated or not edges.get(me)):
            add("orphan", path, "keine eingehenden Links" + (" und keine ausgehenden" if isolated else ""))
        self._lint_contradictions(p, add, by_path)

        if p.type == SOURCE_TYPE:
            self._lint_source(p, add, spec)

    def _lint_modelle(self, p: Page, gen: dict, add) -> None:
        """generated.modelle: Form, unbekanntes Modell und Einträge, die im Ereignisprotokoll stehen, aber fehlen."""
        eintraege = as_list(gen.get("modelle"))
        for e in eintraege:
            if not isinstance(e, dict) or not e.get("modell") or e.get("art") not in MODELL_ARTEN:
                add("modelle_invalid", p.path, f"generated.modelle braucht modell und art aus {' | '.join(MODELL_ARTEN)}: {e!r}")
            elif e["modell"] == MODELL_UNBEKANNT and e["art"] != "akteur":
                add("modell_unbekannt", p.path, f"generated.modelle: Modell von {e.get('harness', '?')} unbekannt; "
                                                "WIKI_ACTOR oder [wiki] actor setzen, bei body --rolle auch --modell")
        vorhanden = {e.get("modell") for e in eintraege if isinstance(e, dict)}
        fehlt = sorted(self._ereignis_modelle.get(p.id, set()) - vorhanden)
        if fehlt:
            add("modell_herkunft_fehlt", p.path, f"Ereignisprotokoll nennt {', '.join(fehlt)}, generated.modelle nicht; "
                                                 "Eintrag wurde von Hand entfernt? Wieder ergänzen")

    def _lint_figures(self, p: Page, add, sources: dict, by_path: dict) -> None:
        for ref in self.figure_refs(p.body):
            name, caption = ref["name"], ref["caption"]
            try:
                self.figure_svg(name)
            except FigureError as exc:
                add("figure_missing", p.path, f"Abbildung {name}: {exc}")
            modell = self._abbildung_modell.get(name)
            if modell and not re.search(r"\bKI\b|generiert", caption, re.I):
                add("figure_generated", p.path, f"Abbildung {name} stammt laut Ereignisprotokoll von {modell}; die Zeile darunter sagt das nicht. "
                    f"Ergänzen, etwa „Mit KI erzeugt ({modell})“")
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

    def _lint_raw(self, add, pages) -> None:
        if not self.raw or not self.raw.is_dir():
            return
        linked = {str((p.fm or {}).get("raw_packet", "")).rstrip("/") for p in pages}
        stems = {p.path.stem for p in pages if p.type == SOURCE_TYPE}
        for packet in sorted(d for d in self.raw.iterdir() if d.is_dir()):
            if self.rel(packet) not in linked and packet.name not in stems:
                add("raw_uningested", packet, "Rohquelle ohne Quellenkarte")
