"""Wiki: abgeleitete Dateien (Indexe, Log, CSL, APA) und rebuild."""

import html
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .basis import FIGURES_DIR, Page, SKILL_DIR, SOURCE_TYPE, WikiError, _manifest_lesen, as_list, one_line, replace_file
from .bereinigung import clean_entry, safe_link
from .konfig import LOG_LABELS
from .quellen import csl_date, csl_names, csl_year, kurzbeleg, kurzname, kurztitel

# Typen, die APA im Klammerbeleg mit dem Titel nennt, auch wenn ein Autor angegeben ist
TITEL_ZITIERT = {"legislation", "legal_case", "bill", "regulation", "treaty"}


class WikiIndexe:
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
        """Karten mit dem Schlagwort aus [index] entry_tag nach `order`, dann Titel: „Zuerst lesen“ im Root-Index."""
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

    def csl_item(self, page: Page) -> dict:
        """CSL-JSON für eine Quellenkarte: der csl-Block, sonst aus flachen Feldern oder Repo-Angaben abgeleitet.

        Wer im Klammerbeleg mit Titel steht (ohne Autor, Rechtsquellen), bekommt einen title-short, wenn keiner da ist.
        """
        item = self._csl_basis(page)
        if not item.get("title-short") and (not item.get("author") or item.get("type") in TITEL_ZITIERT):
            kurz = kurztitel(str(item.get("title") or ""))
            if kurz:
                item["title-short"] = kurz
        return item

    def _csl_basis(self, page: Page) -> dict:
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
        Organisationen stehen im Beleg mit ihrer Abkürzung (kurzname), Titel als title-short. Ohne pandoc einfache
        Ersatzform aus Autor, Jahr, Titel, URL.
        """
        if getattr(self, "_apa", None) is not None:
            return self._apa
        items = [self.csl_item(p) for p in self.source_cards()]
        by_id = {it["id"]: it for it in items}
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
                for name in by_id.get(key, {}).get("author") or []:
                    lang = one_line(name.get("literal", "")) if isinstance(name, dict) else ""
                    if lang and kurzname(lang) != lang:
                        cite = cite.replace(html.escape(lang, quote=False), html.escape(kurzname(lang), quote=False))
                catalog[key] = {"cite": clean_entry(cite[1:-1] if cite.startswith("(") and cite.endswith(")") else cite),
                                "entry": clean_entry(" ".join(entry.split())), "order": order}
        for order, it in enumerate(sorted(items, key=lambda i: i["id"])):
            if it["id"] in catalog:
                continue
            year = csl_year(it)
            names = kurzbeleg(it).rsplit(",", 1)[0] if it.get("author") else str(it.get("title", it["id"]))
            who = str(it.get("title-short") or it.get("title") or it["id"]) \
                if not it.get("author") or it.get("type") in TITEL_ZITIERT else names
            link = str(it.get("URL") or (f"https://doi.org/{it['DOI']}" if it.get("DOI") else ""))
            entry = f"{html.escape(names)} ({html.escape(year)}). <em>{html.escape(str(it.get('title', '')))}</em>."
            if safe_link(link):
                entry += f' <a href="{html.escape(link)}">{html.escape(link)}</a>'
            elif link:
                entry += f" {html.escape(link)}"
            catalog[it["id"]] = {"cite": html.escape(f"{who}, {year}"), "entry": entry, "order": 10000 + order}
        self._apa = catalog
        return catalog
