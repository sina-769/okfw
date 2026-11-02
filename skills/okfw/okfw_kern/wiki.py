"""Wiki: Kern der Klasse (Laden, Seiten, Schreibschutz der Pfade, Ereignisse, verify, Körper, neue Seiten); die übrigen Teile kommen aus den wiki_*-Modulen."""

import datetime as dt
import json
import os
import re
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from .basis import (ACTOR_RE, CODE_RE, ConfigMissing, FIGURES_DIR, FOOTNOTE_REF_RE, LINK_RE, Page, RESERVED,
                    SKILL_DIR, SLUG_RE, SOURCE_TYPE, VERIFIED_HASH, VERSION, WikiError, add_months, as_datetime,
                    as_list, deep_merge, is_timestamp, now_iso, one_line)
from .frontmatter import (FRONTMATTER_RE, MAX_FRONTMATTER_BYTES, MODELL_KENNUNG, MODELL_UNBEKANNT, akteur_modell, body_sha256,
                          dump_frontmatter, load_yaml, modelle_merge, remove_frontmatter_key, set_frontmatter_key, split_frontmatter)
from .konfig import DEFAULT_CONFIG, SEVERITY_ORDER, TODO_BY_ACCESS, TYPE_DEFAULTS, find_config
from .quellen import kurzbeleg
from .wiki_quellen import WikiQuellen
from .wiki_sicht import WikiSicht
from .wiki_indexe import WikiIndexe
from .wiki_lint import WikiLint
from .wiki_pfad import WikiPfad
from .pfad import PFAD_ORDNER, PFAD_TYPE
from .wiki_dashboard import WikiDashboard
from .wiki_ablage import WikiAblage


class Wiki(WikiQuellen, WikiSicht, WikiIndexe, WikiLint, WikiPfad, WikiDashboard, WikiAblage):
    def __init__(self, root: Path, cfg: dict, config_path: Path | None = None):
        no_table = [f"[{sec}]" for sec in DEFAULT_CONFIG if not isinstance(cfg.get(sec), dict)]
        if no_table:
            raise WikiError(f"{', '.join(no_table)} muss eine Tabelle sein")
        self.root = root.resolve()
        self.cfg = cfg
        self.config_path = config_path
        self._pruefe_schreibpfade()
        self.bundle = self.root / cfg["wiki"]["bundle"]
        events = cfg["paths"].get("events") or f"{cfg['wiki']['bundle']}/.events.jsonl"
        self.events_path = self.root / events
        self.raw = self.root / cfg["paths"]["raw"] if cfg["paths"].get("raw") else None
        self.actor = os.environ.get("WIKI_ACTOR") or cfg["wiki"]["actor"]
        self.types: dict[str, dict] = {}
        for name, spec in cfg["types"].items():
            merged = {**TYPE_DEFAULTS, **({"min_links": 0} if name == SOURCE_TYPE else {}), **spec}
            merged["folder"] = merged["folder"] or name
            merged["label"] = merged["label"] or merged["folder"]
            self.types[name] = merged
        # Pfade gibt es in jedem Wiki, ohne Eintrag in der wiki.toml
        self.types.setdefault(PFAD_TYPE, {**TYPE_DEFAULTS, "folder": PFAD_ORDNER, "label": "Pfade", "min_links": 0})
        self.folder_type = {spec["folder"]: name for name, spec in self.types.items()}
        self._pages: list[Page] | None = None
        self.crossref_seen: dict[str, list[dict]] = {}   # DOI → Hinweise aus der Antwort von fetch_source
        self.figure_count = 0             # Dashboard: Abbildungen dieses Baus, für eindeutige ids
        self.bad_links: list[Path] = []   # pages(): Symlinks, die nicht auf eine Datei im Bundle zeigen
        self.public = False               # public_view(): öffentliche Ausgabe ohne verborgene Seiten
        self.withheld: set[Path] = set()  # public_view(): Pfade der weggelassenen Seiten
        required = cfg["wiki"].get("requires_core")
        if required:
            if not re.fullmatch(r"\d+\.\d+(\.\d+)?", str(required)):
                raise WikiError(f"[wiki] requires_core {required!r} ist keine Version MAJOR.MINOR, z. B. \"0.3\"")
            if tuple(map(int, VERSION.split(".")[:2])) < tuple(map(int, str(required).split(".")[:2])):
                raise WikiError(f"wiki.toml verlangt okfw {required}, installiert ist {VERSION}")
        wrong = {c: v for c, v in cfg["lint"]["severity"].items() if v not in (*SEVERITY_ORDER, "off")}
        if wrong:
            raise WikiError("[lint.severity] erlaubt error, warn, info oder off: "
                            + ", ".join(f"{c} = {v!r}" for c, v in wrong.items()))

    def _pruefe_schreibpfade(self) -> None:
        """Jeder Pfad der wiki.toml, in den der Kern schreibt, muss aufgelöst (auch über Symlinks) im Projektordner
        liegen: Ein geklontes Wiki darf über `..`, einen absoluten Pfad oder einen Symlink im Pfad keine Datei außerhalb
        überschreiben."""
        cfg = self.cfg
        pfade = [("[wiki] bundle", cfg["wiki"].get("bundle")), ("[paths] events", cfg["paths"].get("events")),
                 ("[paths] raw", cfg["paths"].get("raw")), ("[dashboard] path", cfg["dashboard"].get("path")),
                 ("[dashboard] public_path", cfg["dashboard"].get("public_path"))]
        pfade += [(f"[export.{name}] path", spec.get("path")) for name, spec in cfg["export"].items()
                  if isinstance(spec, dict)]
        for name, wert in pfade:
            if wert in (None, ""):
                continue
            if not isinstance(wert, str):
                raise WikiError(f"{name} muss ein Pfad (Text) sein, nicht {wert!r}")
            pfad = self.root / wert
            # ein Symlink als letztes Glied wird beim Schreiben ersetzt (replace_file), nicht verfolgt
            aufgeloest = pfad.parent.resolve() / pfad.name if pfad.is_symlink() else pfad.resolve()
            if not aufgeloest.is_relative_to(self.root):
                raise WikiError(f"{name} = {wert!r} liegt außerhalb des Projektordners {self.root}: "
                                "Pfade der wiki.toml müssen im Projektordner bleiben (auch über Symlinks)")
            if {".git", ".claude"} & set(aufgeloest.relative_to(self.root).parts):
                raise WikiError(f"{name} = {wert!r} liegt in .git oder .claude: dorthin schreibt der Kern nicht")

    @classmethod
    def load(cls, config: str | Path | None = None, root: str | Path | None = None,
             start: str | Path | None = None) -> "Wiki":
        if config:
            config_path = Path(config).expanduser().resolve()
        elif root:
            config_path = Path(root).expanduser().resolve() / "wiki.toml"
        else:
            config_path = find_config(Path(start or os.getcwd()).resolve())
        if not config_path or not config_path.is_file():
            raise ConfigMissing("keine wiki.toml gefunden (Projektordner mit --root oder Datei mit --config angeben)")
        if config and root and Path(root).expanduser().resolve() != config_path.parent:
            # Projektordner ist der Ordner der wiki.toml: alle Pfade darin gelten relativ zu ihm, auch im Guard
            raise WikiError(f"--root {root} ist nicht der Ordner von --config {config}: Projektordner ist der Ordner "
                            "der wiki.toml; nur --root DIR (nimmt DIR/wiki.toml) oder nur --config angeben")
        try:
            with config_path.open("rb") as f:
                cfg = deep_merge(DEFAULT_CONFIG, tomllib.load(f))
        except tomllib.TOMLDecodeError as exc:
            raise WikiError(f"{config_path.name} nicht lesbar: {exc}") from exc
        return cls(config_path.parent, cfg, config_path)

    def rel(self, path) -> str:
        if not isinstance(path, Path):
            return str(path)
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def page_id(self, path: Path) -> str:
        """Konzept-ID nach OKF §2: Pfad im Bundle ohne .md."""
        return path.relative_to(self.bundle).with_suffix("").as_posix()

    def _hidden(self, path: Path) -> bool:
        """Versteckte Ordner und Dateien (Punkt) und der Ablageort der Schaubilder zählen nicht als Seiten."""
        parts = path.relative_to(self.bundle).parts
        return any(part.startswith(".") for part in parts) or parts[:1] == (FIGURES_DIR,) and not self._figures_typed()

    def _figures_typed(self) -> bool:
        """Ein Typ nutzt den Ordner der Schaubilder (Wikis von vor 0.5): .md-Seiten darin bleiben normale Seiten."""
        return any(f"{spec['folder']}/".startswith(f"{FIGURES_DIR}/") for spec in self.types.values())

    def _in_bundle(self, path: Path) -> bool:
        """Liegt der Pfad nach Auflösen aller Symlinks im Bundle?"""
        return path.resolve().is_relative_to(self.bundle.resolve())

    def _im_projekt(self, rel) -> Path | None:
        """Pfad einer Karte oder eines Manifests im Projektordner, sonst None: Sie stammen womöglich aus einem fremden Wiki."""
        ziel = self.root / str(rel)
        return ziel if ziel.resolve().is_relative_to(self.root.resolve()) else None

    def parse(self, path: Path) -> Page:
        pid = self.page_id(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return Page(path, pid, None, "", None, "Datei ist nicht UTF-8")
        raw, body = split_frontmatter(text)
        if raw is None:
            return Page(path, pid, None, text, None, "kein Frontmatter-Block")
        if len(raw.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
            return Page(path, pid, None, body, raw, "Frontmatter größer als 64 KB")
        try:
            fm = load_yaml(raw)
        except yaml.YAMLError as exc:
            return Page(path, pid, None, body, raw, f"YAML nicht lesbar: {exc}".splitlines()[0])
        if fm is None:
            fm = {}
        if not isinstance(fm, dict):
            return Page(path, pid, None, body, raw, "Frontmatter ist keine Zuordnung")
        return Page(path, pid, fm, body, raw)

    def pages(self) -> list[Page]:
        if self._pages is None:
            found, self.bad_links = [], []
            if self.bundle.is_dir():
                for path in sorted(self.bundle.rglob("*.md")):
                    if path.name in RESERVED or self._hidden(path) or path.is_dir():
                        continue
                    if self._in_bundle(path) and path.is_file():
                        found.append(self.parse(path))
                    else:
                        self.bad_links.append(path)
            self._pages = found
        return self._pages

    def resolve(self, src: Path, target: str) -> Path | None:
        """Interner Linkziel-Pfad oder None für externe Links, Anker und Beschreibungen."""
        target = str(target).strip()
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#") or " " in target:
            return None
        target = target.split("#", 1)[0]
        if not target:
            return None
        base = self.bundle if target.startswith("/") else src.parent
        resolved = (base / target.lstrip("/")).resolve()
        if target.endswith("/"):
            resolved = resolved / "index.md"
        return resolved

    def read_events(self) -> list[dict]:
        """Ereignisse nach Zeit, jede Zeile einmal. Nach einem Merge paralleler Zweige (merge=union) stehen sie in
        der Datei nicht mehr nach Zeit, und eine Zeile kann doppelt kommen. Gleichstand: Reihenfolge der Datei."""
        if not self.events_path.exists():
            return []
        events = []
        for line in dict.fromkeys(self.events_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                continue
            if isinstance(event, dict):
                events.append(event)
        undated = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        return sorted(events, key=lambda e: as_datetime(e.get("ts")) or undated)

    def append_event(self, kind: str, page: str, detail: str = "", by: str | None = None,
                     modelle: list | None = None) -> dict:
        if self.events_path.is_symlink() and not self.events_path.resolve().is_relative_to(self.root):
            raise WikiError(f"{self.events_path} ist ein Symlink aus dem Projekt heraus: kein Ereignis geschrieben")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": now_iso(), "kind": kind, "page": page, "by": by or self.actor, "detail": one_line(detail)}
        if modelle:   # zweite Ablage neben dem Frontmatter, das sich von Hand ändern lässt (Lint modell_herkunft_fehlt)
            event["modelle"] = [{k: v[k] for k in MODELL_KENNUNG if v.get(k)} for v in modelle]
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def page_path(self, page: str) -> Path:
        page = page.removeprefix("/").removesuffix(".md")
        path = self.bundle / f"{page}.md"
        if not path.exists():
            raise WikiError(f"Seite fehlt: {self.rel(path)}")
        if not self._in_bundle(path):
            raise WikiError(f"{path} liegt außerhalb des Bundles (Symlink?): nicht gelesen, nicht geschrieben")
        return path

    def _kein_symlink(self, path: Path) -> None:
        """Eine Karte als Symlink auf eine andere: Vermerk und Text landeten sonst in der anderen."""
        if path.is_symlink():
            raise WikiError(f"{path.name} ist ein symbolischer Link; nicht geschrieben. Bitte durch eine Datei ersetzen.")

    def _frontmatter_changed(self, path: Path, updates: dict, remove=()) -> str:
        """Der Dateitext mit chirurgisch gesetzten und entfernten Top-Level-Schlüsseln, geprüft und noch nicht geschrieben."""
        self._kein_symlink(path)
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            raise WikiError(f"{self.rel(path)}: kein Frontmatter")
        raw = m.group(1)
        for key, value in updates.items():
            raw = set_frontmatter_key(raw, key, value)
        for key in remove:
            raw = remove_frontmatter_key(raw, key)
        try:
            check = load_yaml(raw)
        except yaml.YAMLError:
            check = None
        if (not isinstance(check, dict) or any(check.get(k) != v for k, v in updates.items())
                or any(k in check for k in remove)):
            raise WikiError(f"{self.rel(path)}: Frontmatter ließ sich nicht sicher ändern, bitte von Hand")
        return text[:m.start(1)] + raw + text[m.end(1):]

    def _write_frontmatter(self, path: Path, updates: dict, remove=()) -> None:
        """Setzt und entfernt Top-Level-Schlüssel chirurgisch und prüft das Ergebnis vor dem Schreiben."""
        path.write_text(self._frontmatter_changed(path, updates, remove), encoding="utf-8")
        self._pages = None

    def verify(self, page: str, by: str, status: str | None = None, note: str = "", force: bool = False) -> Path:
        return self.verify_pages([page], by, status=status, note=note, force=force)[0]

    def verify_pages(self, pages, by: str, status: str | None = None, note: str = "",
                     force: bool = False) -> list[Path]:
        """Prüfvermerk mit Prüfwert für mehrere Seiten (eine Charge), ein Ereignis je Seite.

        Erst werden alle Seiten geprüft, dann geschrieben: Scheitert eine, ändert sich keine. Eine Freigabe
        (human:, --status stable) verlangt je Seite eine Gegenprüfung durch einen anderen Akteur mit Prüfwert
        zum jetzigen Inhalt; force schreibt trotzdem und vermerkt das im Ereignis.
        """
        if not ACTOR_RE.match(by):
            raise WikiError("--by muss der Akteurskonvention folgen, z. B. human:<id> (OKF §7)")
        if status == "stable" and not by.startswith("human:"):
            raise WikiError("status stable nur mit --by human:<id>, also nach Prüfung durch einen Menschen; "
                            "ohne --status hält der Vermerk die Prüfung fest, ohne den Status zu heben")
        targets = []
        for page in dict.fromkeys(pages):
            path = self.page_path(page)
            parsed = self.parse(path)
            if parsed.error:
                raise WikiError(f"{parsed.id}: Frontmatter nicht lesbar: {parsed.error}")
            targets.append((path, parsed))
        release = status == "stable" and by.startswith("human:")
        unchecked = [p.id for _, p in targets if release and self.release_state(p)["check"] is None]
        if unchecked and not force:
            raise WikiError("keine Gegenprüfung zum jetzigen Inhalt für " + ", ".join(unchecked)
                            + ": Niemand sonst hat diesen Text geprüft. Erst gegenprüfen lassen, zum Beispiel mit "
                            "/lesen --pruefen, oder mit --force trotzdem freigeben")
        for path, parsed in targets:
            verified = as_list(parsed.fm.get("verified")) + [{"by": by, "at": now_iso(),
                                                               VERIFIED_HASH: body_sha256(parsed.body)}]
            updates = {"verified": verified}
            if status:
                updates["status"] = status
            self._write_frontmatter(path, updates)
            detail = note + (" (freigegeben ohne Gegenprüfung, --force)" if parsed.id in unchecked else "")
            self.append_event("verify", parsed.id, detail.strip(), by=by)
        return [path for path, _ in targets]

    def generated_now(self, fm: dict | None, *vermerke: dict) -> dict:
        """generated {by, at} für eine inhaltliche Änderung jetzt; weitere Schlüssel im Block bleiben. `modelle` wächst
        nur: Wer generated.by überschreibt, nimmt einen früheren Akteur mit Modell als Eintrag der Art akteur mit."""
        old = (fm or {}).get("generated")
        old = old if isinstance(old, dict) else {}
        modelle = as_list(old.get("modelle"))
        frueher, jetzt = akteur_modell(old.get("by")), akteur_modell(self.actor)
        # „unbekannt“ sagt nichts mehr, sobald derselbe Harness sein Modell nennt: Hätte es Text geschrieben, gäbe es den Eintrag
        if frueher and frueher["modell"] == MODELL_UNBEKANNT and jetzt and jetzt["harness"] == frueher["harness"]:
            frueher = None
        if frueher and old["by"] != self.actor and not any(
                isinstance(e, dict) and (e.get("harness"), e.get("modell")) == (frueher["harness"], frueher["modell"])
                for e in modelle):
            modelle = [*modelle, {**frueher, "art": "akteur", "by": old["by"], **({"at": old["at"]} if old.get("at") else {})}]
        for vermerk in vermerke:
            modelle = modelle_merge(modelle, {**vermerk, "by": self.actor, "at": now_iso()})
        return {**old, "by": self.actor, "at": now_iso(), **({"modelle": modelle} if modelle else {})}

    def replace_body(self, page: str, body: str, detail: str = "", modell: dict | None = None) -> Path:
        """Körper ersetzen und generated nachziehen. Gleicher Inhalt (gleicher Prüfwert) ändert nichts, außer der Aufrufer
        nennt ein Modell: Dann kommt der Vermerk dazu.

        Auf einer stable-Seite setzt das status: draft ([trust] edit_resets_stable): Die Freigabe galt dem
        alten Inhalt. verified bleibt stehen, das Ereignis nennt die verfallene Freigabe."""
        path = self.page_path(page)
        self._kein_symlink(path)
        parsed = self.parse(path)
        text = path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            raise WikiError(f"{self.rel(path)}: kein Frontmatter")
        if parsed.fm is not None and body_sha256(body) == body_sha256(parsed.body):
            return self.body_changed(page, detail or "Modellherkunft nachgetragen", modell=modell) if modell else path
        path.write_text(text[:m.end()] + "\n" + body.strip() + "\n", encoding="utf-8")
        self._pages = None
        try:
            return self.body_changed(page, detail or "Inhalt geschrieben", modell=modell)
        except WikiError:   # sonst bliebe eine stable-Seite mit neuem Text stable
            path.write_text(text, encoding="utf-8")
            self._pages = None
            raise

    def body_changed(self, page: str, detail: str, kind: str = "update", modell: dict | None = None) -> Path:
        """Nach einer Änderung am Körper (body, oder Edit/Write über den PostToolUse-Hook): generated nachziehen,
        Ereignis update (create, wenn Write die Seite neu angelegt hat). Auf einer stable-Seite, deren Freigabe nicht
        mehr zum Inhalt passt, status: draft ([trust] edit_resets_stable); passt sie (Text wieder wie freigegeben),
        bleibt stable. Schreibt ein Akteur mit Modell, oder nennt der Aufrufer ein Modell (Rolle, Vorschlag), steht es
        mit dem Prüfwert dieses Körpers in generated.modelle und im Ereignis."""
        path = self.page_path(page)
        parsed = self.parse(path)
        vermerke = []
        if parsed.fm is not None:   # unlesbares Frontmatter bleibt unangetastet, der Lint meldet es
            selbst = akteur_modell(self.actor)
            vermerke = [{**v, VERIFIED_HASH: body_sha256(parsed.body)}
                        for v in (modell, selbst and {**selbst, "art": "text"}) if v]
            self._write_frontmatter(path, {"generated": self.generated_now(parsed.fm, *vermerke)})
            parsed = self.parse(path)
            state = self.release_state(parsed)
            if parsed.fm.get("status") == "stable" and not state["valid"] \
                    and self.cfg["trust"].get("edit_resets_stable", True):
                self._write_frontmatter(path, {"status": "draft"})
                human = state["human"]
                detail += ("; status stable → draft, Freigabe durch "
                           + (f"{human['by']} vom {str(human['at'])[:10]} verfallen" if human else "niemanden")
                           + ", neu prüfen und freigeben (verify)")
        self._pages = None
        self.append_event(kind, self.page_id(path), detail, modelle=vermerke)
        return path

    def release_state(self, page: Page) -> dict:
        """Freigabe und Gegenprüfung einer Seite gegen ihren jetzigen Inhalt.

        Ein verified-Eintrag gilt, solange er zum Inhalt passt: mit Prüfwert, wenn der Prüfwert gleich ist; ohne
        Prüfwert (Eintrag aus der Zeit davor, nur Zeitvergleich), wenn weder generated.at noch ein späterer
        Eintrag mit anderem Prüfwert eine Änderung nach ihm zeigt.

        human: letzter Eintrag durch human:<id>; outdated/why: er gilt nicht mehr; valid: er gilt. check: letzter
        gültige Eintrag eines anderen Akteurs (Claude, Prozess) mit Prüfwert, nur er zählt als Gegenprüfung für
        eine Freigabe. machine: letzter gültige Eintrag eines anderen Akteurs, auch ohne Prüfwert; unhashed: es
        gibt nur solche ohne Prüfwert und keine gültige Freigabe. machine_outdated: Einträge anderer Akteure, aber
        keiner gilt. tier: Vertrauensstufe aus gültigen Einträgen (human-reviewed, machine-confirmed), sonst
        outdated (Einträge, aber keiner gilt) oder unverified.
        """
        fm = page.fm or {}
        sha = body_sha256(page.body)
        entries = [v for v in as_list(fm.get("verified")) if isinstance(v, dict) and as_datetime(v.get("at"))]
        gen = fm.get("generated")
        changed = as_datetime(gen.get("at")) if isinstance(gen, dict) else None
        mismatch = max((as_datetime(v["at"]) for v in entries if v.get(VERIFIED_HASH) and v[VERIFIED_HASH] != sha),
                       default=None)

        def why_not(v: dict, word: str) -> str:
            if v.get(VERIFIED_HASH):
                return "" if v[VERIFIED_HASH] == sha else f"Inhalt seit der {word} geändert (Prüfwert passt nicht)"
            at = as_datetime(v["at"])
            if changed and changed > at:
                return f"generated.at jünger als die {word} (Eintrag ohne Prüfwert, nur Zeitvergleich)"
            if mismatch and mismatch >= at:
                return "spätere Prüfung galt anderem Inhalt (Eintrag ohne Prüfwert, nur Zeitvergleich)"
            return ""

        def latest(items):   # bei gleichem Zeitpunkt der spätere in der Liste
            return max(reversed(items), key=lambda v: as_datetime(v["at"]), default=None)

        humans = [v for v in entries if str(v.get("by", "")).startswith("human:")]
        others = [v for v in entries if not str(v.get("by", "")).startswith("human:")]
        human = latest(humans)
        why = why_not(human, "Freigabe") if human else ""
        machine = latest([v for v in others if not why_not(v, "Prüfung")])
        check = latest([v for v in others if v.get(VERIFIED_HASH) == sha])
        valid = bool(human) and not why
        tier = ("human-reviewed" if valid else "machine-confirmed" if machine
                else "outdated" if entries else "unverified")
        last_other = latest(others)
        return {"sha": sha, "human": human, "check": check, "outdated": bool(why), "why": why, "valid": valid,
                "machine": machine, "unhashed": bool(machine) and not check and not valid,
                "machine_outdated": bool(others) and not machine,
                "machine_why": why_not(last_other, "Prüfung") if others and not machine else "", "last_other": last_other,
                "tier": tier}

    def set_sources(self, page: str, add=(), remove=()) -> tuple[Path, list[str], list[str]]:
        """sources[] chirurgisch ergänzen oder kürzen: (Pfad, hinzugefügte IDs, Hinweise).

        Ein Eintrag in add ist ein Quellen-Slug oder ID=SLUG (eigene Fußnote je Fundstelle auf dieselbe Karte).
        Entfernt wird nur, was der Text nicht mehr als [^id] zitiert.
        """
        path = self.page_path(page)
        parsed = self.parse(path)
        if parsed.fm is None:
            raise WikiError(f"Frontmatter nicht lesbar: {parsed.error}")
        folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        entries = as_list(parsed.fm.get("sources"))
        ids = [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]
        added, notes = [], []
        for item in add:
            sid, _, slug = item.partition("=") if "=" in item else (item, "", item)
            if not (SLUG_RE.fullmatch(sid) and SLUG_RE.fullmatch(slug)):
                raise WikiError(f"--add {item!r}: Quellen-Slug oder ID=SLUG, beides kebab-case")
            card = self.bundle / folder / f"{slug}.md"
            if not card.exists():
                raise WikiError(f"Quellenkarte {self.rel(card)} fehlt")
            if sid in ids:
                notes.append(f"{sid} steht schon in sources[]")
                continue
            entries.append({"id": sid, "resource": f"/{folder}/{slug}.md"})
            ids.append(sid)
            added.append(sid)
        cited = set(FOOTNOTE_REF_RE.findall(CODE_RE.sub("", parsed.body)))
        for sid in remove:
            if sid not in ids:
                raise WikiError(f"{sid} steht nicht in sources[]")
            if sid in cited:
                raise WikiError(f"[^{sid}] steht noch im Text; erst den Beleg aus dem Text nehmen, dann entfernen")
            entries = [e for e in entries if not (isinstance(e, dict) and str(e.get("id")) == sid)]
            ids.remove(sid)
        if added or remove:
            self._write_frontmatter(path, {"sources": entries, "generated": self.generated_now(parsed.fm)})
            self.append_event("update", parsed.id, "Quellen: " + ", ".join([f"+{s}" for s in added]
                                                                           + [f"−{s}" for s in remove]))
        return path, added, notes

    def new_page(self, type_: str, slug: str, title: str = "", description: str = "", sources=(), tags=(),
                 access: str | None = None, resource: str | None = None, csl: dict | None = None,
                 extra: dict | None = None) -> Path:
        """Neue Karte anlegen; status und verified gehören zur Freigabe (verify), nicht zu extra."""
        spec = self.types.get(type_)
        if spec is None:
            raise WikiError(f"Typ {type_!r} fehlt in wiki.toml [types] ({', '.join(self.types) or 'keine'})")
        if not SLUG_RE.fullmatch(slug):
            raise WikiError(f"Ungültiger Slug {slug!r}: nur kebab-case (a-z, 0-9, -)")
        path = self.bundle / spec["folder"] / f"{slug}.md"
        if os.path.lexists(path):   # auch ein Symlink ins Leere: Schreiben ginge durch ihn hindurch
            raise WikiError(f"{self.rel(path)} existiert schon – Karte von Hand bearbeiten")
        if not self._in_bundle(path):
            raise WikiError(f"{path} liegt außerhalb des Bundles (Symlink?): nicht angelegt")
        if csl:
            csl = {k: v for k, v in csl.items() if k != "id"}
            title = title or one_line(csl.get("title"))
            if not resource:
                resource = f"https://doi.org/{csl['DOI']}" if csl.get("DOI") else csl.get("URL")
        if not title:
            raise WikiError("--title fehlt")
        fm = {"type": type_, "title": title, "description": description}
        if resource:
            fm["resource"] = resource
        fm["status"] = "draft"
        fm["tags"] = list(tags)
        if csl:
            fm["aliases"] = [kurzbeleg(csl)]
        if access:
            fm["access"] = access
        if type_ == SOURCE_TYPE:
            fm["license"] = self.manifest_license((extra or {}).get("raw_packet")) or {"name": "unknown", "url": None}
        if csl:
            fm[self.cfg["export"].get("csl", {}).get("field", "csl")] = csl
        entries = []
        source_folder = self.types.get(SOURCE_TYPE, {}).get("folder", "sources")
        for sid in sources:
            card = self.bundle / source_folder / f"{sid}.md"
            if not card.exists():
                raise WikiError(f"Quellenkarte {self.rel(card)} fehlt")
            entries.append({"id": sid, "resource": f"/{source_folder}/{sid}.md", "title": self.parse(card).title})
        if entries:
            fm["sources"] = entries
        for key, value in spec.get("defaults", {}).items():   # [types.X] defaults, z. B. evidenz = "peer-reviewed"
            if key not in fm and key not in (extra or {}):
                fm[key] = value
        gesperrt = sorted({"status", "verified"} & set(extra or {}))
        if gesperrt:
            raise WikiError(f"--set {', '.join(gesperrt)}: setzt nur die Freigabe (verify), nicht new")
        fm.update(extra or {})
        titles = {e["id"]: e.pop("title") for e in entries}
        fm["generated"] = {"by": self.actor, "at": now_iso()}
        if spec["stale_months"]:
            moment = add_months(dt.datetime.now(dt.timezone.utc), int(spec["stale_months"]))
            fm["stale_after"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self.render_template(type_, fm, csl, titles)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + dump_frontmatter(fm) + "---\n\n" + body.lstrip(), encoding="utf-8")
        self._pages = None
        self.append_event("create", self.page_id(path), title if self.cfg["log"].get("link_text") == "id" else "")
        return path

    def render_template(self, type_: str, fm: dict, csl: dict | None, titles: dict | None = None) -> str:
        spec = self.types[type_]
        access = fm.get("access") or ""
        todo = {**TODO_BY_ACCESS.get(access, {}), **spec.get("todo", {}).get(access, {})}
        flags = as_list(fm.get(self.cfg["sources"]["updates_field"]))
        check = fm.get(self.cfg["sources"]["retraction_field"])
        found = [h for h in as_list((check if isinstance(check, dict) else {}).get("hinweise")) if isinstance(h, dict)]
        pubmed = [str(h.get("art")) for h in found if h.get("quelle") == "pubmed"]
        notices = [h for h in found if str(h.get("quelle")).endswith("update-to")]
        values = {
            "title": fm.get("title", ""), "description": fm.get("description", ""), "access": access,
            "kurzbeleg": kurzbeleg(csl, narrative=True) if csl else fm.get("title", ""),
            "apa": (self.render_apa([dict(csl, id="x")]) or "") if csl else "",
            "footnotes": "\n".join(f"[^{s['id']}]: [{(titles or {}).get(s['id'], s['id'])}]({s['resource']})"
                                   for s in fm.get("sources", [])),
            "todo_kurzreferat": todo.get("kurzreferat", "> [!todo] Ausstehend."),
            "todo_kernaussagen": todo.get("kernaussagen", "> [!todo] Ausstehend."),
            "hinweise": (f"\n- ⚠ Crossref meldet: {', '.join(map(str, flags))} – vor Nutzung ansehen." if flags else "")
                        + (f"\n- ⚠ PubMed meldet: {', '.join(pubmed)} – vor Nutzung ansehen." if pubmed else "")
                        + "".join(f"\n- ⚠ Crossref meldet: Diese Karte ist selbst ein Hinweis ({h.get('art')}) zu "
                                  f"{h.get('doi') or '?'}, nicht die Arbeit – vor Nutzung ansehen." for h in notices),
        }
        if spec["template"]:
            template = self.template_path(spec["template"]).read_text(encoding="utf-8")

            def fill(m):
                key = m.group(1)
                if key.startswith("fm."):
                    return one_line(fm.get(key[3:], ""))
                return str(values.get(key, m.group(0)))
            return re.sub(r"\{\{([\w.]+)\}\}", fill, template)
        lines = [f"# {values['kurzbeleg'] + ': ' if csl else ''}{values['title']}", ""]
        if values["apa"]:
            lines += [f"> {values['apa']}", ""]
        elif values["description"] and type_ != SOURCE_TYPE:
            lines += [values["description"], ""]
        if values["hinweise"]:   # Crossref-Hinweise (Rückzug, Korrektur) unter dem APA-Block
            lines += [values["hinweise"].strip(), ""]
        claims = self.cfg["lint"]["claims_section"].lower()
        for section in spec["sections"]:
            text = "> [!todo] Ausstehend."
            if type_ == SOURCE_TYPE and section.lower().startswith("kurzreferat"):
                text = values["todo_kurzreferat"]
            elif type_ == SOURCE_TYPE and section.lower().startswith(claims):
                text = values["todo_kernaussagen"]
            lines += [f"## {section}", "", text, ""]
        if values["footnotes"]:
            lines += [values["footnotes"], ""]
        return "\n".join(lines)

    def template_path(self, name: str) -> Path:
        """Vorlage aus [types.X] template: neben der wiki.toml, sonst im Skill (profiles/, templates/)."""
        base = self.config_path.parent if self.config_path else self.root
        for ordner, candidate in ((base, base / name), (SKILL_DIR, SKILL_DIR / "profiles" / name),
                                  (SKILL_DIR, SKILL_DIR / "templates" / name)):
            if candidate.is_file():
                # sonst holt ein geklontes Wiki über `..` oder einen absoluten Pfad fremde Dateien in neue Karten
                if not candidate.resolve().is_relative_to(ordner.resolve()):
                    raise WikiError(f"Vorlage {name} liegt außerhalb des Projektordners: "
                                    "Vorlagen müssen neben der wiki.toml oder im Skill liegen")
                return candidate
        raise WikiError(f"Vorlage {name} fehlt (gesucht neben der wiki.toml und im Skill unter profiles/, templates/)")

    def edges(self) -> list[tuple[Page, Page, str]]:
        """Gerichtete Kanten im Bundle: Links im Text (link) und sources[].resource (source).

        OKF §5.1: Zeigt eine Quelle auf ein Konzept im Bundle, ist sie bereits eine Kante im Graphen.
        """
        by_path = {p.path.resolve(): p for p in self.pages()}
        out = []
        for p in self.pages():
            if p.fm is None:
                continue
            targets = [(t, "link") for t in LINK_RE.findall(CODE_RE.sub("", p.body))]
            targets += [(str(s["resource"]), "source") for s in as_list(p.fm.get("sources"))
                        if isinstance(s, dict) and s.get("resource")]
            seen = set()
            for target, kind in targets:
                q = by_path.get(self.resolve(p.path, target))
                if q is not None and q is not p and (q.id, kind) not in seen:
                    seen.add((q.id, kind))
                    out.append((p, q, kind))
        return out

    def status(self) -> dict:
        by_type: Counter = Counter()
        tiers: Counter = Counter()
        drafts = stale = todos = 0
        release: Counter = Counter()
        now = dt.datetime.now(dt.timezone.utc)
        for p in self.pages():
            fm = p.fm or {}
            by_type[p.type or "?"] += 1
            drafts += fm.get("status") == "draft"
            when = as_datetime(fm.get("stale_after"))
            stale += bool(when and now >= when)
            todos += CODE_RE.sub("", p.body).count("[!todo]")   # Marker in Code-Spannen und -Blöcken zählen nicht
            # Vertrauensstufe nur aus Vermerken, die zum jetzigen Inhalt passen; verfallene zählen getrennt
            state = self.release_state(p) if p.fm is not None else None
            tiers[state["tier"] if state else "unverified"] += 1
            if state:
                release["valid"] += state["valid"]
                release["outdated"] += state["outdated"]
                release["stable_outdated"] += state["outdated"] and fm.get("status") == "stable"
                if fm.get("status") != "deprecated":   # wie „Zur Freigabe“ im Dashboard
                    release["pending"] += bool(state["check"]) and not state["valid"]
                    release["unhashed"] += state["unhashed"]
        pending = []
        if self.raw and self.raw.is_dir():
            linked = {str((p.fm or {}).get("raw_packet", "")).rstrip("/") for p in self.pages()}
            stems = {p.path.stem for p in self.pages() if p.type == SOURCE_TYPE}
            pending = [d.name for d in sorted(self.raw.iterdir())
                       if d.is_dir() and self.rel(d) not in linked and d.name not in stems]
        events = self.read_events()
        fundweg: Counter = Counter()
        for q in self.source_cards():
            if q.fm is not None:
                fundweg.update([e["art"] for e in self.fundweg_of(q) if e.get("art")][:1] or ["fehlt"])
        scope = Counter(v for v, _ in self.scopes().values())
        return {"bundle": self.rel(self.bundle), "pages": len(self.pages()), "by_type": dict(sorted(by_type.items())),
                "draft": drafts, "stale": stale, "todo": todos, "raw_pending": pending,
                "trust": {k: n for k, n in tiers.items() if k != "outdated"}, "trust_outdated": tiers["outdated"],
                "release": {k: release[k] for k in ("valid", "outdated", "stable_outdated", "pending", "unhashed")},
                "events": len(events), "last_event": next((e["ts"] for e in reversed(events) if is_timestamp(e.get("ts"))), ""),
                "fundweg": dict(fundweg), "scope": dict(scope), "hidden": len(self.hidden_ids())}
