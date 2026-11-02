"""Wiki: Dashboard (Daten, Leseansicht, Schaubilder, Design) und seine Textbausteine."""

import html
import json
import re
import secrets
import shlex
from collections import Counter
from pathlib import Path

from .basis import (ACTOR_RE, CHECK_RE, CODE_RE, FIGURES_DIR, FIGURE_SRC_RE, FOOTNOTE_DEF_RE, FOOTNOTE_REF_RE, FigureError, IMAGE_RE, Page, SKILL_DIR,
                    SOURCE_TYPE, VERIFIED_HASH, VERSION, WikiError, as_datetime, as_list, is_empty, now_iso, one_line, replace_file)
from .bereinigung import FIGURE_MAX_BYTES, LINK_SCHEMES, clean_svg, css_problem, link_scheme, safe_link
from .frontmatter import MODELL_ARTEN, MODELL_KENNUNG, body_sha256
from .rechte import LICENSE_CLASSES, RIGHTS_ACTIONS
from .konfig import FUNDWEG_ARTEN
from .pfad import PFAD_TYPE, stationen_lesen
from .quellen import LOCATOR_RE


# Dashboard: Codeblöcke dieser Sprachen (und unmarkierte mit Rahmenzeichen) sind Textdiagramme
DIAGRAM_LANGS = {"text", "txt", "plain", "plaintext", "ascii", "tree", "diagram"}
DIAGRAM_PRE_RE = re.compile(r'<pre><code(?: class="([^"]*)")?>(.*?)</code></pre>', re.S)
BOX_DRAWING_RE = re.compile(r"[─-╿]")
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
    faces = [("Bricolage Grotesque", "200 800", "normal", "bricolagegrotesque-latin-var.woff2", "OFL-Bricolage-Grotesque.txt"),
             ("Instrument Sans", "400 700", "normal", "instrumentsans-latin-var.woff2", "OFL-Instrument-Sans.txt"),
             ("Newsreader", "200 800", "normal", "newsreader-latin-var.woff2", "OFL-Newsreader.txt"),
             ("Newsreader", "200 800", "italic", "newsreader-italic-latin-var.woff2", "OFL-Newsreader.txt"),
             ("JetBrains Mono", "400", "normal", "jetbrainsmono-regular.woff2", "OFL-JetBrains-Mono.txt")]
    rules, notices = [], {}
    for family, weight, style, name, license_file in faces:
        font, license_text = fonts / name, fonts / license_file
        # Erste Zeile des Lizenztexts = Copyright-Vermerk des Rechteinhabers; ohne ihn keine Einbettung
        lines = license_text.read_text(encoding="utf-8").splitlines() if license_text.is_file() else []
        notice = lines[0].strip().replace("*/", "* /") if lines else ""
        if not (font.is_file() and notice):
            continue
        if family not in notices.setdefault(notice, []):
            notices[notice].append(family)
        data = base64.b64encode(font.read_bytes()).decode("ascii")
        rules.append(f"@font-face {{ font-family: '{family}'; font-style: {style}; font-weight: {weight}; "
                     f"font-display: swap; src: url(data:font/woff2;base64,{data}) format('woff2'); }}")
    if not rules:
        return ""
    lines = [f"   {', '.join(families)}: {notice}" for notice, families in notices.items()]
    comment = ("/* Eingebettete Schriften (JetBrains Mono vollständig, die übrigen als Latin-Teilmengen):\n" + "\n".join(lines)
               + "\n   Lizenz: SIL Open Font License 1.1, https://openfontlicense.org */")
    return "\n".join([comment, *rules])


class WikiDashboard:
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

    def subtype_field(self, type_: str, fm: dict) -> str:
        """Feld mit der Unterart eines Typs: [types.X] subtype, sonst das erste vorhandene Standardfeld."""
        spec = self.types.get(type_, {})
        if spec.get("subtype"):
            return spec["subtype"]
        return next((f for f in ("category", "format", "evidenz", "worktype") if fm.get(f)), "")

    def modelle_sicht(self, p: Page) -> list[dict]:
        """generated.modelle fürs Dashboard, maschinenlesbar in dessen JSON und sichtbar in der Seite; `gleich` sagt, ob
        der Text noch der damals vermerkte ist. Die öffentliche Ausgabe nennt keine Kennung einer Person."""
        gen = (p.fm or {}).get("generated")
        aus = []
        for e in as_list(gen.get("modelle") if isinstance(gen, dict) else None):
            if not isinstance(e, dict) or not e.get("modell"):
                continue
            felder = {k: str(e[k]) for k in (*MODELL_KENNUNG, "art", "at") if e.get(k)}
            if not self.public and e.get("by"):
                felder["by"] = str(e["by"])
            if e.get(VERIFIED_HASH):
                felder["gleich"] = e[VERIFIED_HASH] == body_sha256(p.body)
            aus.append(felder)
        return aus

    def dashboard_data(self) -> dict:
        """Was das Dashboard zum inhaltlichen Erkunden braucht: Seiten mit Vorschau und Kategorien, Kanten.

        Bewusst ohne Lint-Befunde und Prüfstatus (dafür gibt es `lint` und `status`), außer der Freigabe gegen
        den jetzigen Inhalt, und die nur in der normalen Ausgabe. Dateipfade stehen nur als
        Ablageort der Volltexte einer Quelle darin (`stored`, mit Git-Status), und nur in der normalen Ausgabe.
        """
        # Pfade sind keine Knoten: Mit Kanten zu allem zerstörten sie das Bild. Sie stehen unter "pfade".
        edges = [e for e in self.edges() if PFAD_TYPE not in (e[0].type, e[1].type)]
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
            if p.type == PFAD_TYPE:
                continue
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
                **({"release": "", "trust": "", "ohne_gegenpruefung": False} if self.public or p.fm is None else
                   {"release": "outdated" if (st := self.release_state(p))["outdated"] else "valid" if st["valid"] else "",
                    "trust": st["tier"],
                    # wie Lint human_without_check: freigegeben, aber niemand sonst hat diesen Text geprüft
                    "ohne_gegenpruefung": fm.get("status") == "stable" and st["valid"] and not st["check"]
                                          and bool(st["human"].get(VERIFIED_HASH))}),
                "stable": fm.get("status") == "stable",
                "deprecated": fm.get("status") == "deprecated",
                "modelle": self.modelle_sicht(p),
            })
        types, labels = {n: spec for n, spec in self.types.items() if n != PFAD_TYPE}, self.cfg.get("labels", {})
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
            "modell_arten": MODELL_ARTEN,
            "pfade": self.pfade_daten(reader),
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
            if p.fm is None or p.fm.get("status") == "deprecated" or p.type == PFAD_TYPE:
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
        parts = {"/*__FONTS__*/": embedded_fonts(), "/*__DESIGN__*/": design, "__TITLE__": title, "__DATA__": data,
                 "/*__PFAD_CSS__*/": (SKILL_DIR / "templates" / "pfad.css").read_text(encoding="utf-8"),
                 "/*__PFAD_JS__*/": (SKILL_DIR / "templates" / "pfad.js").read_text(encoding="utf-8")}
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

            def rendern(text: str) -> str:
                text, cites = self._apa_cites(text, labels, catalog)
                rewrite.page, rewrite.cites, rewrite.spots = p, cites, []
                md.reset()
                try:
                    fertig = md.convert(text)
                except RecursionError:
                    fertig = f"<pre>{html.escape(text)}</pre>"   # zu tief verschachtelt: als Text statt Abbruch des Baus
                fertig = re.sub(r"⟦c(\d+)⟧", lambda m, c=cites: cite_at(c, m), fertig)
                return self.figures_html(DIAGRAM_PRE_RE.sub(diagram, fertig))

            texte = {}
            if p.type == PFAD_TYPE:   # jedes Textfeld für sich, mit den Fußnoten, auf die es verweist; alle stehen auf einer Seite
                stationen, fussnoten = stationen_lesen(p.body)
                feld = 0
                for s in stationen:
                    texte[s["slug"]] = {}
                    for e in s["elemente"]:
                        if e["art"] == "text":
                            feld += 1
                            namen = set(FOOTNOTE_REF_RE.findall(e["text"]))
                            quelle = "\n\n".join([e["text"], *(f for f in fussnoten if FOOTNOTE_DEF_RE.match(f).group(1) in namen)])
                            fertig = rendern(self._drop_withheld(p, quelle, refs)[0] if self.withheld else quelle)
                            texte[s["slug"]][e["schluessel"]] = re.sub(r'(id="|href="#)(fn(?:ref)?):', rf"\1\2-{feld}:", fertig)
            rendered = rendern(body)
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
                         "open": rewrite.spots, **({"texte": texte} if texte else {})}
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
