"""okfw – gemeinsamer Kern für Wissens-Wikis im Open Knowledge Format v0.2.

Muster: pi-llm-wiki (https://github.com/zosmaai/pi-llm-wiki), übertragen auf Claude Code.
Jedes Projekt beschreibt sein Wiki in einer wiki.toml (Bundle-Ordner, Seitentypen,
Pflichtfelder, Ereignisort, Exporte). Ohne wiki.toml schreibt der Kern nichts.

  okfw.py status [--hook]
  okfw.py lint [--strict] [--json] [--summary]
  okfw.py rebuild [--check] [--quiet | --hook [--befunde]]
  okfw.py new <typ> <slug> --title T [--description D] [--sources a,b] [--tags x,y]
                 [--access A] [--resource URI] [--csl DATEI|-] [--set feld=wert ...]
                 [--doi D | --pmid P | --arxiv A | --manuell --titel T --url U [--autor --jahr --csl-typ]]
                 [--volltext PFAD [--von URL]] [--evidenz E] [--fundweg ART [--db --query] [--fundweg-von SLUG]
                 [--at ZEIT] [--by AKTEUR]] [--aus-lauf LAUF --kandidat N] [--trotz-rueckzug]
                 (deutsche Optionen: --titel, --beschreibung, --quellen)
  okfw.py new-source <slug> …   (= new source <slug> …)
  okfw.py capture <slug> --url URL --file PFAD[=REPO-PFAD] [--file ...] [--commit SHA]
                 [--source-path PFAD] [--license NAME --license-url URL] [--note N]
  okfw.py hash <pfad> [--from URL]
  okfw.py verify <seite> [<seite> ...] --by AKTEUR [--status stable] [--note N] [--force]   (stable nur mit human:<id>)
  okfw.py event <ereignisart> <seite> [detail] [--by AKTEUR]
  okfw.py deprecate <seite> --by AKTEUR [--nachfolger SEITE] [--note N]   (erste Stufe: Karte bleibt, status deprecated)
  okfw.py delete <seite> [--yes --by human:<id>] [--belege-brechen] [--mit-rohquelle] [--json]
                 (zweite Stufe: ohne --yes nur die Folgen; mit --yes in den Papierkorb <bundle>/.papierkorb/)
  okfw.py restore [<eintrag> --by AKTEUR] [--json]   (ohne Eintrag: den Papierkorb zeigen)
  okfw.py body <seite> <datei|-> | --nachziehen [--neu] [--detail D] [--rolle R [--modell M]]
                                                                       (Seitenname hinter "--", wenn er mit "-" beginnt)
  okfw.py sources <seite> [--add a,ID=b] [--remove c]
  okfw.py init [--profile NAME]
  okfw.py fundweg <seite> --art suche|modellvorschlag|material|person|zitatverfolgung
                 [--db DB --query Q] [--von SLUG] [--at ZEIT] [--by AKTEUR] [--add]
  okfw.py fundweg <seite> --aus-lauf LAUF --kandidat N [--add]
  okfw.py abbildung <name> --file x.svg [--ersetzen] [--rolle R [--modell M]]
  okfw.py rechte [<seite>] [--aktion abbildung] [--json]
  okfw.py lizenz <seite> --name N [--url U] [--class K] [--via V] [--dry-run] | --fetch [--force] | --missing --fetch [--dry-run]
  okfw.py dashboard [--out PFAD] [--oeffentlich] [--open]
  okfw.py pfad neu|pruefen|uebernehmen|export …   (Pfade, Hilfe mit pfad --help)
  okfw.py gitattributes
  okfw.py serve [--port N] [--no-open]   (Dashboard mit Bearbeiten, nur lokal; Modul okfw_serve.py)

Globale Optionen: --config PFAD (sonst wiki.toml aufwärts ab cwd), --root DIR (Projektordner = Ordner der wiki.toml).
Als Bibliothek: Wiki.load(config=..., root=...) mit pages(), lint(), projections(),
rebuild(), new_page(), capture(), verify(), append_event():

  sys.path.insert(0, str(Path.home() / ".claude/skills/okfw")); import okfw
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import webbrowser
from collections import Counter
from pathlib import Path

import yaml

from .basis import (AUFTRAEGE, ConfigMissing, FIGURES_DIR, FigureError, PAPIERKORB, RetractionFound, SKILL_DIR, SLUG_RE, SOURCE_TYPE,
                    STATUS, VERSION, WikiError, as_list, replace_file)
from .frontmatter import MODELL_UNBEKANNT, load_yaml
from .bereinigung import FIGURE_MAX_BYTES, clean_svg, svg_verlust
from .rechte import LICENSE_CLASSES, RIGHTS_ACTIONS, RIGHT_VALUES, RIGHT_WORDS, classify_license
from .konfig import FUNDWEG_ARTEN, SEVERITY_ORDER
from .quellen import kandidat_abgleich, kennungen, notice_kind, notice_text, today_parts
from .wiki import Wiki
from .wiki_ablage import folgen_zeilen


def write_gitattributes(wiki: Wiki) -> list[str]:
    """Trägt in <root>/.gitattributes merge=union für alles ein, was parallele Zweige beide ändern: den Ereignisstrom
    und die Projektionen, die rebuild danach neu erzeugt. Vorhandene Zeilen bleiben. Gibt die neuen Zeilen zurück."""
    bundle = wiki.rel(wiki.bundle)
    paths = [wiki.rel(wiki.events_path), f"{bundle}/**/index.md", f"{bundle}/log.md"]
    paths += [v["path"] for v in wiki.cfg["export"].values() if isinstance(v, dict) and v.get("path")]
    paths = [p.removeprefix("./") for p in paths]
    rules = [(f'"{p}"' if " " in p else p) + " merge=union" for p in paths]
    path = wiki.root / ".gitattributes"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = [r for r in rules if r not in old.splitlines()]
    if new:
        note = "# okfw: parallele Zweige hängen hier beide an; nach dem Merge `okfw.py rebuild`, nie von Hand lösen"
        new_text = "\n".join(([] if note in old else [note]) + new) + "\n"
        replace_file(path, old + ("" if not old or old.endswith("\n") else "\n") + new_text)
    return new


def protected_paths(wiki: Wiki) -> list[str]:
    """Was nur der Kern schreibt. Der Guard (hooks/okf_hook.py, ohne PyYAML) schützt dieselben Pfade;
    tests/test_doku_code.py hält beide Listen gleich."""
    paths = [wiki.rel(wiki.events_path), f"{wiki.rel(wiki.bundle)}/**/index.md", f"{wiki.rel(wiki.bundle)}/log.md",
             f"{wiki.rel(wiki.bundle)}/{PAPIERKORB}/**", f"{wiki.rel(wiki.bundle)}/{AUFTRAEGE}/**"]
    if wiki.raw:
        paths.append(f"{wiki.rel(wiki.raw)}/**")
    paths += [v["path"] for v in wiki.cfg["export"].values() if isinstance(v, dict) and v.get("path")]
    paths.append(wiki.cfg["dashboard"]["path"])
    if wiki.cfg["visibility"].get("field"):
        paths.append(wiki.rel(wiki.dashboard_path(public=True)))
    return paths + list(wiki.cfg["paths"].get("protected", []))


def cmd_status(wiki: Wiki, args) -> int:
    s = wiki.status()
    if args.hook:
        counts = ", ".join(f"{k} {v}" for k, v in s["by_type"].items())
        text = [
            f"OKF-v0.2-Wiki unter {wiki.bundle}: {s['pages']} Seiten ({counts}), davon {s['draft']} draft, "
            f"{s['stale']} veraltet, {s['todo']} offene [!todo].",
            f"Letztes Ereignis: {s['last_event'] or 'keins'}.",
            f"Rohquellen ohne Quellenkarte: {', '.join(s['raw_pending'])}." if s["raw_pending"] else "",
            f"{s['hidden']} Seiten nicht öffentlich ([visibility]); öffentliche Seiten nicht auf sie verweisen lassen."
            if s["hidden"] else "",
            f"{s['release']['stable_outdated']} Seiten stable, seit Änderung ungeprüft (lint: verified_outdated)."
            if s["release"]["stable_outdated"] else "",
            f"{s['release']['pending']} Seiten gegengeprüft und zur Freigabe durch einen Menschen (Dashboard „Zur Freigabe“)."
            if s["release"]["pending"] else "",
            f"Vor inhaltlicher Arbeit {wiki.rel(wiki.bundle)}/index.md lesen und im Wiki suchen.",
            f"Nicht direkt bearbeiten: {', '.join(protected_paths(wiki))}.",
            f"Werkzeug: uv run --no-config --quiet {SKILL_DIR / 'okfw.py'} (new, capture, verify, lint vor Abschluss).",
        ]
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": " ".join(t for t in text if t)}},
                         ensure_ascii=False))
        return 0
    print(f"Bundle: {s['bundle']} ({s['pages']} Seiten)")
    print("Seiten nach Typ: " + ", ".join(f"{k} {v}" for k, v in s["by_type"].items()))
    print(f"draft: {s['draft']} · veraltet: {s['stale']} · offene [!todo]: {s['todo']}")
    print("Vertrauen: " + ", ".join(f"{k} {v}" for k, v in sorted(s["trust"].items()))
          + (f" · {s['trust_outdated']} verfallen" if s["trust_outdated"] else ""))
    rel = s["release"]
    if rel["valid"] or rel["outdated"] or rel["pending"]:
        print(f"Freigaben: {rel['valid']} gültig · {rel['outdated']} verfallen"
              + (f" (davon {rel['stable_outdated']} stable, seit Änderung ungeprüft)" if rel["stable_outdated"] else "")
              + f" · {rel['pending']} zur Freigabe")
    if rel["unhashed"]:
        print(f"Gegenprüfung ohne Prüfwert: {rel['unhashed']} Seiten (zählen nicht für eine Freigabe; "
              "lint verified_unhashed)")
    if wiki.raw:
        print("Rohquellen ohne Karte: " + (", ".join(s["raw_pending"]) or "keine"))
    if s["scope"]:
        field = wiki.cfg["scope"]["field"]
        print(f"{wiki.cfg['scope'].get('label') or field}: " + " · ".join(
            f"{wiki.cfg['labels'].get(field, {}).get(v, v)} {n}" for v, n in sorted(s["scope"].items())))
    if wiki.cfg["visibility"].get("field"):
        print(f"{wiki.cfg['visibility'].get('label') or 'Sichtbarkeit'}: {s['hidden']} Seiten fehlen in der "
              "öffentlichen Ausgabe (dashboard --oeffentlich)")
    if set(s["fundweg"]) - {"fehlt"}:
        total = sum(s["fundweg"].values())
        print("Fundweg der Quellen: " + " · ".join(f"{FUNDWEG_ARTEN.get(a, a)} {n}" for a, n in
                                                   sorted(s["fundweg"].items(), key=lambda t: -t[1]))
              + f" (Modellvorschlag {100 * s['fundweg'].get('modellvorschlag', 0) // max(total, 1)} %)")
    print(f"Ereignisse: {s['events']}" + (f", zuletzt {s['last_event']}" if s["last_event"] else "")
          + f" ({wiki.rel(wiki.events_path)})")
    return 0


def cmd_lint(wiki: Wiki, args) -> int:
    findings = wiki.lint()
    counts = Counter(f.severity for f in findings)
    if args.json:
        print(json.dumps({"pages": len(wiki.pages()), "counts": dict(counts),
                          "findings": [f.as_dict() for f in findings]}, ensure_ascii=False, indent=1))
    elif args.summary:
        for (sev, code), n in sorted(Counter((f.severity, f.code) for f in findings).items(),
                                     key=lambda t: (SEVERITY_ORDER[t[0][0]], -t[1])):
            print(f"{sev:5}  {code:24}  {n}")
    else:
        for f in findings:
            print(f"{f.severity:5}  {f.code:24}  {f.path}  {f.message}")
    if not args.json:
        print(f"\n{len(wiki.pages())} Seiten · {counts['error']} Fehler · {counts['warn']} Warnungen · "
              f"{counts['info']} Hinweise")
    return 1 if counts["error"] or (args.strict and counts["warn"]) else 0


def cmd_rebuild(wiki: Wiki, args) -> int:
    changed = wiki.rebuild(check=args.check)
    if args.check:
        for path in changed:
            print(f"veraltet: {wiki.rel(path)}")
        return 1 if changed else 0
    if args.hook:
        # Stop-Hook: Lint-Fehler als eine Zeile für die Person; ob der Abschluss blockiert wird, entscheidet okf_hook.py
        errors = [f for f in wiki.lint() if f.severity == "error"]
        if errors:
            codes = ", ".join(f"{code} {n}" for code, n in Counter(f.code for f in errors).most_common(4))
            out = {"systemMessage": f"okfw: Lint meldet {len(errors)} Fehler ({codes}); "
                                    "vor Abschluss `lint` ausführen und beheben."}
            if args.befunde:
                pages: dict[str, list[str]] = {}
                for f in errors:
                    pages.setdefault(f.path, []).append(f"{f.code}: {f.message}")
                out["befunde"] = [f"{path}: {'; '.join(items)}" for path, items in pages.items()]
            print(json.dumps(out, ensure_ascii=False))
        return 0
    if not args.quiet:
        print(f"Projektionen neu erzeugt: {len(changed)} Datei(en) geändert")
    return 0


def cmd_new(wiki: Wiki, args) -> int:
    csl = None
    if args.csl:
        raw = sys.stdin.read() if args.csl == "-" else Path(args.csl).read_text(encoding="utf-8")
        csl = json.loads(raw)
        csl = csl[0] if isinstance(csl, list) else csl
    extra = {}
    for item in args.set:
        key, _, value = item.partition("=")
        try:
            extra[key] = load_yaml(value) if value else ""
        except yaml.YAMLError as exc:
            raise WikiError(f"--set {key}: kein gültiges YAML: {str(exc).splitlines()[0]}") from exc
    funde, kandidat = [], None
    if args.aus_lauf or args.kandidat:
        if not (args.aus_lauf and args.kandidat):
            raise WikiError("--aus-lauf und --kandidat gehören zusammen")
        if args.type != SOURCE_TYPE or args.fundweg:
            raise WikiError("--aus-lauf gibt es nur für Quellen und ersetzt --fundweg (die Funde bringt der Lauf mit)")
        kandidat, funde, notes = wiki.fundwege_aus_lauf(args.aus_lauf, args.kandidat)
        for note in notes:
            print(f"okfw: Hinweis: {note}", file=sys.stderr)
        if not (args.doi or args.pmid or args.arxiv or args.manuell or args.csl):   # Kennung des Kandidaten
            args.doi, args.pmid, args.arxiv = (kandidat.get(k) or None for k in ("doi", "pmid", "arxiv"))
            args.pmid = None if args.doi else args.pmid
            args.arxiv = None if args.doi or args.pmid else args.arxiv
            if not (args.doi or args.pmid or args.arxiv):
                raise WikiError(f"Kandidat {kandidat.get('nr')} hat weder DOI noch PMID oder arXiv-ID: "
                                "Karte mit --manuell oder --csl anlegen, Fundweg mit `fundweg --aus-lauf`")
            kandidat = None   # die Karte nimmt seine Kennung, es gibt nichts abzugleichen
        else:   # vor dem Abruf: Ist es eine andere Arbeit, sollen weder Netz noch Karte etwas davon sehen
            kandidat_abgleich(kandidat, kennungen(args.doi or (csl or {}).get("DOI"), args.pmid or (csl or {}).get("PMID"),
                                                  args.arxiv, args.url), "die angegebene Karte")
    if (args.at or args.by) and not args.fundweg:
        raise WikiError("--at und --by gehören zu --fundweg")
    lookup = args.doi or args.pmid or args.arxiv
    if (lookup or args.manuell or args.volltext or args.evidenz) and args.type != SOURCE_TYPE:
        raise WikiError("--doi, --pmid, --arxiv, --manuell, --volltext und --evidenz gibt es nur für Quellen")
    if sum(map(bool, (args.doi, args.pmid, args.arxiv, args.manuell, args.csl))) > 1:
        raise WikiError("nur eine Metadatenquelle: --doi, --pmid, --arxiv, --manuell oder --csl")
    if args.trotz_rueckzug and not (args.doi or args.pmid):
        raise WikiError("--trotz-rueckzug gilt nur mit --doi, --pmid oder --aus-lauf mit einer DOI oder PMID")
    src = wiki.cfg["sources"]
    if lookup:
        csl, resource, checked, flags = wiki.fetch_source(args.doi or "", args.pmid or "", args.arxiv or "")
        args.resource = args.resource or resource
        extra[src["checked_field"]] = checked
        if flags:
            extra[src["updates_field"]] = flags
        if not args.arxiv:   # Rückzug, Bedenken, Korrektur: vor dem Schreiben, ohne Treffer-Karte
            check = wiki.check_retraction(doi=str(csl.get("DOI") or args.doi or ""),
                                          pmid=str(args.pmid or csl.get("PMID") or ""), flags=flags)
            for warning in check.pop("warnungen"):
                print(f"okfw: Achtung: {warning}", file=sys.stderr)
            hits = [h for h in check["hinweise"] if notice_kind(h["art"]) in ("rueckzug", "bedenken")]
            if hits and not args.trotz_rueckzug:
                raise RetractionFound("Rückzug oder Bedenken gemeldet, keine Karte angelegt: "
                                      + "; ".join(notice_text(h) for h in hits)
                                      + ". Trotzdem anlegen (mit Vermerk): --trotz-rueckzug")
            if not check["hinweise"]:
                del check["hinweise"]
            if hits:
                check["trotz"] = True
            extra[src["retraction_field"]] = check
    elif args.manuell:
        if not (args.title and args.url):
            raise WikiError("--manuell braucht --titel und --url")
        csl = {"type": args.csl_typ or src["manual_csl_type"], "title": args.title,
               "author": [{"literal": args.autor or "?"}],
               "issued": {"date-parts": [[args.jahr or dt.date.today().year]]}, "URL": args.url,
               "accessed": today_parts()}
        args.resource = args.resource or args.url
    if args.type == SOURCE_TYPE and not args.access and (lookup or args.manuell):
        manual_access = "originaldokument" in wiki.cfg["enums"].get("access", [])
        args.access = "originaldokument" if args.manuell and manual_access else "citation"
    if args.evidenz:
        extra["evidenz"] = args.evidenz
    if args.volltext:
        extra["fulltext"] = wiki.fulltext_block(args.volltext, args.von or args.resource)["fulltext"]
    if args.fundweg:
        if args.type != SOURCE_TYPE:
            raise WikiError("--fundweg gibt es nur für Quellen")
        extra[wiki.cfg["fundweg"].get("field") or "fundweg"] = wiki.fundweg_entry(
            args.fundweg, db=args.db, query=args.query, von=args.fundweg_von, at=args.at, by=args.by)
    if funde:
        extra[wiki.cfg["fundweg"].get("field") or "fundweg"] = funde[0] if len(funde) == 1 else funde
    if kandidat:   # jetzt auch mit dem, was der Abruf an Kennungen gebracht hat
        hinweis = kandidat_abgleich(kandidat, kennungen(args.doi or (csl or {}).get("DOI"),
                                                        args.pmid or (csl or {}).get("PMID"), args.arxiv, args.url),
                                    "die angegebene Karte")
        if hinweis:
            print(f"okfw: Hinweis: {hinweis}", file=sys.stderr)
    path = wiki.new_page(args.type, args.slug, title=args.title or "", description=args.description or "",
                         sources=[s for s in (args.sources or "").split(",") if s],
                         tags=[t for t in (args.tags or "").split(",") if t],
                         access=args.access, resource=args.resource, csl=csl, extra=extra)
    print(f"angelegt: {wiki.rel(path)}")
    return 0


def cmd_capture(wiki: Wiki, args) -> int:
    for warning in wiki.capture_warnings(args.commit, args.license, args.license_url):
        print(f"okfw: Achtung: {warning}", file=sys.stderr)
    packet = wiki.capture(args.slug, args.url, args.file, commit=args.commit, license_name=args.license,
                          license_url=args.license_url, note=args.note, source_path=args.source_path)
    print(f"erfasst: {wiki.rel(packet)} ({len(args.file)} Datei(en))")
    return 0


def cmd_hash(wiki: Wiki, args) -> int:
    print(yaml.safe_dump(wiki.fulltext_block(args.path, args.retrieved_from), allow_unicode=True, sort_keys=False,
                         default_flow_style=False), end="")
    return 0


def cmd_verify(wiki: Wiki, args) -> int:
    release = args.status == "stable" and args.by.startswith("human:")
    unchecked = [p for p in args.pages if release and wiki.release_state(wiki.parse(wiki.page_path(p)))["check"] is None]
    paths = wiki.verify_pages(args.pages, args.by, status=args.status, note=args.note or "", force=args.force)
    for path in paths:
        print(f"verifiziert: {wiki.page_id(path)} durch {args.by}" + (f", status {args.status}" if args.status else ""))
    if unchecked:
        print(f"Achtung: freigegeben ohne Gegenprüfung zum jetzigen Inhalt (--force): {', '.join(unchecked)}",
              file=sys.stderr)
    return 0


def indexe_nachziehen(wiki: Wiki) -> None:
    """Die Änderung steht schon und ist im Ereignisstrom: Ein Fehler hier ist eine Meldung, kein Abbruch."""
    try:
        wiki.rebuild()
    except (WikiError, OSError) as exc:
        print(f"Achtung: Projektionen (index.md, log.md) nicht neu erzeugt: {exc}", file=sys.stderr)


def cmd_deprecate(wiki: Wiki, args) -> int:
    done = wiki.deprecate_page(args.page, args.by, nachfolger=args.nachfolger, note=args.note or "")
    indexe_nachziehen(wiki)
    print(f"abgelöst: {done['seite']} durch {args.by}" + (f", Nachfolger {done['nachfolger']}" if done["nachfolger"] else ""))
    return 0


def cmd_delete(wiki: Wiki, args) -> int:
    folgen = wiki.folgen(args.page)
    if args.yes:
        if not (args.by or "").startswith("human:"):
            raise WikiError("delete --yes braucht --by human:<id>")
        done = wiki.delete_page(args.page, args.by, belege_brechen=args.belege_brechen, mit_rohquelle=args.mit_rohquelle)
        indexe_nachziehen(wiki)
    if args.json:
        print(json.dumps(done if args.yes else folgen, ensure_ascii=False, indent=1))
    elif args.yes:
        print(f"im Papierkorb: {done['seite']} ({wiki.rel(wiki.bundle)}/{PAPIERKORB}/{done['eintrag']}), zurück mit: "
              f"restore {done['eintrag']} --by …")
    else:
        print(f"{folgen['pfad']} ({folgen['titel']}), status {folgen['status']}")
        print("\n".join(folgen_zeilen(folgen)))
        print("Nichts geändert. Löschen mit --yes --by human:<id>; Belege brechen nur mit --belege-brechen, "
              "die Rohquelle geht nur mit --mit-rohquelle mit.")
    return 0


def cmd_restore(wiki: Wiki, args) -> int:
    if args.eintrag is None:
        eintraege = wiki.papierkorb()
        if args.json:
            print(json.dumps(eintraege, ensure_ascii=False, indent=1))
        for e in () if args.json else eintraege:
            print(f"{e['eintrag']}  " + (e["fehler"] if "fehler" in e else f"{e['seite']}, gelöscht von {e['by']}"
                                         + (f", mit Rohquelle {e['rohquelle']}" if e["rohquelle"] else "")))
        return 0
    if not args.by:
        raise WikiError("restore <eintrag> braucht --by")
    done = wiki.restore_page(args.eintrag, args.by)
    indexe_nachziehen(wiki)
    if args.json:
        print(json.dumps(done, ensure_ascii=False, indent=1))
    else:
        print(f"zurück: {done['seite']}" + (f", Rohquelle {done['rohquelle']}" if done["rohquelle"] else "")
              + (" (weicht vom Stand beim Löschen ab)" if done["abweichend"] else ""))
    return 0


def cmd_event(wiki: Wiki, args) -> int:
    wiki.append_event(args.kind, args.page, args.detail or "", by=args.by)
    return 0


def rollen_vermerk(wiki: Wiki, rolle: str | None, modell: str | None) -> dict | None:
    """generated.modelle-Eintrag für body --rolle: Der Text beruht auf dem Entwurf dieser Rolle. --modell, wenn die
    Rolle in der Sitzung lief und das Modell dort bekannt ist; ohne Angabe steht unknown da und der Lint warnt."""
    if not rolle:
        if modell:
            raise WikiError("--modell gilt nur mit --rolle")
        return None
    import rollen
    try:
        ziel = rollen.loese(rollen.lade(wiki.config_path), rolle)
    except (rollen.Fehler, KeyError) as exc:
        raise WikiError(f"--rolle {rolle}: {exc}") from exc
    return {"harness": ziel["harness"], **({"anbieter": ziel["anbieter"]} if ziel["anbieter"] else {}),
            "modell": modell or ziel["modell"] or MODELL_UNBEKANNT, "rolle": rolle, "art": "entwurf"}


def cmd_body(wiki: Wiki, args) -> int:
    if args.nachziehen == (args.file is not None):
        raise WikiError("body braucht genau eins: <datei|-> oder --nachziehen")
    if args.neu and not args.nachziehen:
        raise WikiError("--neu gilt nur mit --nachziehen")
    before = wiki.parse(wiki.page_path(args.page))
    vermerk = rollen_vermerk(wiki, args.rolle, args.modell)
    if args.nachziehen:
        path = wiki.body_changed(args.page, args.detail or ("Seite angelegt" if args.neu else "Körper geändert"),
                                 kind="create" if args.neu else "update", modell=vermerk)
        after = wiki.parse(path)
        print(f"nachgezogen: {wiki.rel(path)} (generated durch {wiki.actor})")
        if (before.fm or {}).get("status") == "stable" and (after.fm or {}).get("status") == "draft":
            print("  status stable → draft: die Freigabe galt dem alten Inhalt; neu prüfen und mit verify freigeben")
        return 0
    body = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    path = wiki.replace_body(args.page, body, args.detail or "", modell=vermerk)
    after = wiki.parse(path)
    if after.raw_fm == before.raw_fm and after.body == before.body:
        print(f"unverändert: {wiki.rel(path)} (gleicher Inhalt)")
        return 0
    print(f"aktualisiert: {wiki.rel(path)}")
    if (before.fm or {}).get("status") == "stable" and (after.fm or {}).get("status") == "draft":
        print("  status stable → draft: die Freigabe galt dem alten Inhalt; neu prüfen und mit verify freigeben")
    return 0


def cmd_sources(wiki: Wiki, args) -> int:
    add = [s for s in (args.add or "").split(",") if s]
    remove = [s for s in (args.remove or "").split(",") if s]
    if not add and not remove:
        raise WikiError("--add a,b oder --remove a,b angeben")
    path, added, notes = wiki.set_sources(args.page, add=add, remove=remove)
    for note in notes:
        print(f"okfw: {note}", file=sys.stderr)
    print(f"{wiki.page_id(path)}: Quellen " + ", ".join([f"+{s}" for s in added] + [f"−{s}" for s in remove] or ["unverändert"]))
    entries = {str(e.get("id")): e for e in as_list(wiki.parse(path).fm.get("sources")) if isinstance(e, dict)}
    if added:
        print("Fußnoten für den Text (Fundstelle wie „S. 4“ oder „Z. 120–135“ in den Fußnotentext):")
        for sid in added:
            card = wiki.resolve(path, str(entries[sid]["resource"]))
            print(f"  [^{sid}]: [{wiki.parse(card).title}]({entries[sid]['resource']})")
    return 0


def cmd_fundweg(wiki: Wiki, args) -> int:
    if args.aus_lauf or args.kandidat:
        if not (args.aus_lauf and args.kandidat):
            raise WikiError("--aus-lauf und --kandidat gehören zusammen")
        if args.art or args.db or args.query or args.von or args.at or args.by:
            raise WikiError("--aus-lauf bringt Art, Datenbank, Suchstring, Zeitpunkt und Akteur aus dem Lauf mit")
        kandidat, entries, notes = wiki.fundwege_aus_lauf(args.aus_lauf, args.kandidat)
        karte = wiki.parse(wiki.page_path(args.page))
        hinweis = kandidat_abgleich(kandidat, wiki.source_keys(karte), f"Karte {karte.id}")
        for note in notes + [hinweis] * bool(hinweis):
            print(f"okfw: Hinweis: {note}", file=sys.stderr)
        vorher = wiki.fundweg_of(karte)
        path = wiki.set_fundwege(args.page, entries, append=args.add)
        neu = [e for e in entries if e not in vorher] if args.add else entries
        print(f"{wiki.page_id(path)}: {len(neu)} Fundweg(e) aus Lauf, Kandidat {kandidat.get('nr')}"
              + (" (ergänzt)" if neu and args.add else " (standen schon da, unverändert)" if args.add else ""))
        return 0
    if not args.art:
        raise WikiError("--art oder --aus-lauf mit --kandidat angeben")
    entry = wiki.fundweg_entry(args.art, db=args.db, query=args.query, von=args.von, at=args.at, by=args.by)
    path = wiki.set_fundweg(args.page, entry, append=args.add)
    print(f"{wiki.page_id(path)}: Fundweg {FUNDWEG_ARTEN[args.art]}" + (" (ergänzt)" if args.add else ""))
    return 0


def cmd_abbildung(wiki: Wiki, args) -> int:
    if not SLUG_RE.fullmatch(args.name):
        raise WikiError(f"Ungültiger Name {args.name!r}: nur kebab-case (a-z, 0-9, -)")
    vermerk = rollen_vermerk(wiki, args.rolle, args.modell)
    source = Path(args.file).expanduser()
    if not source.is_file():
        raise WikiError(f"Keine Datei: {source}")
    if source.stat().st_size > FIGURE_MAX_BYTES:
        raise WikiError(f"{source} ist größer als {FIGURE_MAX_BYTES // 1024} KB: Abbildung vereinfachen")
    try:
        svg, removed = clean_svg(source.read_bytes())
    except FigureError as exc:
        raise WikiError(f"{source} abgelehnt: {exc}") from exc
    target = wiki.figure_file(args.name)
    if target.is_symlink() or not wiki._in_bundle(target):
        raise WikiError(f"{target} ist ein Symlink oder liegt außerhalb des Bundles: nicht geschrieben")
    existed = target.exists()
    if existed and not args.ersetzen:
        raise WikiError(f"{wiki.rel(target)} existiert schon; mit --ersetzen überschreiben")
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_file(target, svg + "\n")
    wiki.append_event("update" if existed else "create", f"{FIGURES_DIR}/{args.name}",
                      f"Abbildung {wiki.rel(target)} ({target.stat().st_size} Byte)", modelle=[vermerk] if vermerk else None)
    if removed:
        print(f"okfw: Achtung: aus dem SVG entfernt: {', '.join(removed)}", file=sys.stderr)
        for hinweis in svg_verlust(removed)["hinweise"]:
            print(f"okfw: {hinweis}", file=sys.stderr)
    print(f"abgelegt: {wiki.rel(target)} ({target.stat().st_size} Byte)\n"
          f"Einbinden: ![Titel](/{FIGURES_DIR}/{args.name}.svg)\n"
          "           *Abb.: Was sie zeigt. Eigene Darstellung nach Müller[^mueller].*")
    return 0


def cmd_rechte(wiki: Wiki, args) -> int:
    cards = [p for p in wiki.source_cards() if p.fm is not None]
    if args.page:
        page = wiki.parse(wiki.page_path(args.page))
        lic, rights, stored = wiki.license_of(page), wiki.rights_of(page), wiki.fulltext_locations(page)
        if args.json:
            print(json.dumps({"page": page.id, "license": lic, "stored": stored,
                              "rights": {a: {"value": v, "note": n} for a, (v, n) in rights.items()}},
                             ensure_ascii=False, indent=1))
            return 0
        print(f"{page.title}\nLizenz: {lic['name'] or '–'}" + (f" ({lic['url']})" if lic["url"] else "")
              + f"\nKlasse: {lic['label']}")
        for s in stored:
            print(f"Volltext: {s['path']} ({'von Git versioniert' if s['tracked'] else 'nicht versioniert'})")
        for a, (value, note) in rights.items():
            print(f"  {RIGHTS_ACTIONS[a]:42} {RIGHT_WORDS[value]}" + (f" – {note}" if note else ""))
        return 0
    if args.aktion:
        if args.aktion not in RIGHTS_ACTIONS:
            raise WikiError(f"--aktion: eine von {', '.join(RIGHTS_ACTIONS)}")
        groups: dict[str, list[str]] = {v: [] for v in RIGHT_VALUES}
        for p in cards:
            groups[wiki.rights_of(p)[args.aktion][0]].append(p.id)
        if args.json:
            print(json.dumps(groups, ensure_ascii=False, indent=1))
            return 0
        print(f"{RIGHTS_ACTIONS[args.aktion]} ({len(cards)} Quellen)")
        for value, ids in groups.items():
            if ids:
                print(f"\n{RIGHT_WORDS[value]} ({len(ids)}):\n  " + "\n  ".join(sorted(ids)))
        return 0
    by_class = Counter(wiki.license_of(p)["cls"] for p in cards)
    matrix = wiki.rights_matrix()
    if args.json:
        print(json.dumps({"classes": dict(by_class), "matrix": {c: {a: list(v) for a, v in matrix[c].items()}
                                                               for c in by_class}}, ensure_ascii=False, indent=1))
        return 0
    print(f"Lizenzen und Nutzungsrechte ({len(cards)} Quellen)")
    for cls in LICENSE_CLASSES:
        if not by_class[cls]:
            continue
        print(f"\n{LICENSE_CLASSES[cls]} ({by_class[cls]})")
        for a, (value, note) in matrix[cls].items():
            print(f"  {RIGHTS_ACTIONS[a]:42} {RIGHT_WORDS[value]}" + (f" – {note}" if note else ""))
    open_ = sorted(p.id for p in cards if wiki.license_of(p)["cls"] == "unbekannt")
    if open_:
        print(f"\nLizenz ungeklärt ({len(open_)}): " + ", ".join(open_))
    return 0


def cmd_lizenz(wiki: Wiki, args) -> int:
    skipped = 0
    if args.missing:
        unknown = [p for p in wiki.source_cards() if p.fm is not None and wiki.license_of(p)["cls"] == "unbekannt"]
        targets = [p for p in unknown if args.force or not wiki.license_named(p)]
        skipped = len(unknown) - len(targets)
    elif args.page:
        targets = [wiki.parse(wiki.page_path(args.page))]
    else:
        raise WikiError("Seite angeben oder --missing")
    if not args.fetch:
        if args.missing or not (args.name or args.cls):
            raise WikiError("--name (mit --url, --class) setzen oder --fetch nutzen")
        if args.cls and args.cls not in LICENSE_CLASSES:
            raise WikiError(f"--class: eine von {', '.join(LICENSE_CLASSES)}")
        page = targets[0]
        lic = dict((page.fm or {}).get("license") or {}) if isinstance((page.fm or {}).get("license"), dict) else {}
        lic.update({k: v for k, v in (("name", args.name), ("url", args.url), ("class", args.cls)) if v})
        lic.update({"checked": dt.date.today().isoformat(), "via": args.via or "manuell"})
        if args.dry_run:
            print(f"{page.id}: {lic}")
        else:
            wiki.set_license(page.id, lic)
            print(f"{page.id}: {lic.get('name') or 'ohne Namen'} → {LICENSE_CLASSES[classify_license(lic, wiki.cfg['licenses'].get('patterns'))[0]]}")
        return 0
    found = 0
    for i, page in enumerate(targets):
        if not args.missing and not args.force and wiki.license_named(page):
            print(f"{page.id}: hat schon eine Lizenz ({wiki.license_of(page)['name']}); --force zum Überschreiben")
            continue
        ids = wiki.identifiers(page)
        if not any(ids):
            print(f"{page.id}: weder DOI noch PMID oder PMCID – Lizenz am Original prüfen und mit --name setzen")
            continue
        if i:
            time.sleep(0.3)
        lic = wiki.lookup_license(*ids)
        if not lic:
            print(f"{page.id}: keine Lizenzangabe bei Crossref, DataCite, Europe PMC oder OpenAlex")
            continue
        found += 1
        cls = LICENSE_CLASSES[classify_license(lic, wiki.cfg["licenses"].get("patterns"))[0]]
        if args.dry_run:
            print(f"{page.id}: {lic['name']} ({lic['via']}) → {cls}")
        else:
            wiki.set_license(page.id, lic, f"Lizenz über {lic['via']}: {lic['name']}")
            print(f"{page.id}: {lic['name']} ({lic['via']}) → {cls}")
    if args.missing:
        print(f"\n{found} von {len(targets)} Lizenzen gefunden" + (" (nichts geschrieben)" if args.dry_run else ""))
        if skipped:
            print(f"{skipped} benannte Lizenz(en) ohne Klasse übersprungen (`lizenz <seite> --class …` oder `--force`)")
    return 0


def cmd_dashboard(wiki: Wiki, args) -> int:
    path = wiki.write_dashboard(args.out, public=args.public)
    if args.public:
        hidden = wiki.hidden_ids()
        print(f"Öffentliches Dashboard: {path} ({len(wiki.pages()) - len(hidden)} Seiten, {len(hidden)} weggelassen)")
        for p, targets in wiki.visibility_leaks():
            print(f"  Achtung {p.id} verweist auf verborgene {', '.join(targets)}: Link wird zu Text, Beleg entfällt "
                  "(lint: visibility_leak)", file=sys.stderr)
    else:
        print(f"Dashboard: {path}")
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


def cmd_pfad(wiki: Wiki, args) -> int:
    if args.was == "neu":
        print(wiki.rel(wiki.pfad_neu(args.name)))
    elif args.was == "pruefen":
        p = wiki.pfad_seite(args.name)
        befunde = wiki.pfad_befunde(p) if p is not None else wiki.standard_pfad_daten()["befunde"]
        if args.json:
            print(json.dumps(befunde, ensure_ascii=False, indent=2))
        else:
            for b in befunde:
                print(f"{'STOPP ' if b['stopp'] else ''}{b['code']}: {b['text']}")
            print(f"{len(befunde)} Befunde" + (f", {sum(b['stopp'] for b in befunde)} stoppen den öffentlichen Export" if befunde else ""))
    elif args.was == "uebernehmen":
        bericht = wiki.pfad_uebernehmen(dry_run=args.dry_run)
        if args.json:
            print(json.dumps(bericht, ensure_ascii=False, indent=2))
        else:
            print(f"{'Würde anlegen' if args.dry_run else 'Angelegt'}: {bericht['pfad']}.md, die Karten bleiben unverändert")
            for i, s in enumerate(bericht["schritte"], 1):
                print(f"  {i}. {s['titel']} ({s['id']})")
            for a in bericht["auffaellig"]:
                print(f"  Auffällig: {a}")
    elif args.was == "export":
        ziel, warnungen = wiki.pfad_export(args.name, out=args.out, public=args.public, notizen=args.notizen,
                                           ohne_interne=args.ohne_interne)
        for w in warnungen:
            print(f"  Achtung {w}", file=sys.stderr)
        print(f"Pfad: {ziel}")
    return 0


def cmd_serve(wiki: Wiki, args) -> int:
    if str(SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(SKILL_DIR))
    import okfw_serve
    return okfw_serve.run(wiki, sys.modules.get("okfw") or sys.modules["__main__"], port=args.port, oeffnen=not args.no_open)


def cmd_init(args) -> int:
    if args.config:
        raise WikiError("init legt die wiki.toml im Projektordner an: --root DIR statt --config angeben")
    root = Path(args.root or os.getcwd()).resolve()
    target = root / "wiki.toml"
    if target.exists():
        raise WikiError(f"{target} existiert schon")
    profile = SKILL_DIR / "profiles" / f"{args.profile}.toml"
    if not profile.is_file():
        raise WikiError(f"Profil fehlt: {profile}")
    shutil.copyfile(profile, target)
    wiki = Wiki.load(config=target)
    for spec in wiki.types.values():
        (wiki.bundle / spec["folder"]).mkdir(parents=True, exist_ok=True)
    wiki.append_event("init", ".", f"Bundle angelegt (Profil {args.profile}, okfw {VERSION})")
    wiki.rebuild()
    write_gitattributes(wiki)
    print(f"angelegt: {wiki.rel(target)}, Bundle {wiki.rel(wiki.bundle)}/ – Titel und Typen in wiki.toml anpassen")
    return 0


def cmd_gitattributes(wiki: Wiki, args) -> int:
    new = write_gitattributes(wiki)
    print(f".gitattributes: {len(new)} Zeile(n) ergänzt" if new else ".gitattributes: schon vollständig")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="Pfad zur wiki.toml")
    parser.add_argument("--root", help="Projektordner mit der wiki.toml (Standard: aufwärts ab dem aktuellen Ordner)")
    parser.add_argument("--version", action="version", version=f"okfw {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="Kurzstatistik")
    p.add_argument("--hook", action="store_true", help="als SessionStart-Hook-JSON ausgeben")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("lint", help="OKF-Konformität und Profilregeln prüfen")
    p.add_argument("--strict", "--streng", action="store_true", help="Warnungen ebenfalls als Fehler werten")
    p.add_argument("--json", action="store_true")
    p.add_argument("--summary", action="store_true", help="nur Anzahl je Befund-Code")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("rebuild", aliases=["index"], help="index.md, log.md und Exporte neu erzeugen")
    p.add_argument("--check", action="store_true", help="nichts schreiben, veraltete Projektionen melden")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--hook", action="store_true", help="Stop-Hook: still neu erzeugen, Lint-Fehler als systemMessage-JSON")
    p.add_argument("--befunde", action="store_true", help="mit --hook: Lint-Fehler zusätzlich je Seite im JSON (für okf_hook.py)")
    p.set_defaults(func=cmd_rebuild)

    def new_options(p):
        p.add_argument("--title", "--titel")
        p.add_argument("--description", "--beschreibung")
        p.add_argument("--sources", "--quellen", help="Komma-Liste von Quellen-Slugs → sources[] für Fußnoten [^slug]")
        p.add_argument("--tags")
        p.add_argument("--access")
        p.add_argument("--resource")
        p.add_argument("--csl", help="CSL-JSON-Datei oder - für stdin")
        p.add_argument("--set", action="append", default=[], metavar="FELD=WERT", help="weiteres Feld (YAML-Wert)")
        p.add_argument("--doi", help="Quelle: Metadaten aus Crossref")
        p.add_argument("--pmid", help="Quelle: Metadaten aus PubMed (Crossref lückenhaft oder keine DOI)")
        p.add_argument("--arxiv", help="Quelle: Metadaten aus arXiv")
        p.add_argument("--manuell", action="store_true", help="Quelle ohne DOI: braucht --titel und --url")
        p.add_argument("--autor", help="--manuell: Autor oder Institution (als literal)")
        p.add_argument("--jahr", type=int, help="--manuell: Jahr")
        p.add_argument("--url", help="--manuell: Fundort")
        p.add_argument("--csl-typ", help="--manuell: CSL-Typ (Standard: [sources] manual_csl_type)")
        p.add_argument("--volltext", help="Quelle: lokaler Volltext (Pfad relativ zum Projekt) → fulltext-Block")
        p.add_argument("--evidenz", help="Quelle: Evidenzart laut Profil ([enums] evidenz)")
        p.add_argument("--fundweg", choices=list(FUNDWEG_ARTEN), help="wie die Quelle gefunden wurde")
        p.add_argument("--db", help="Fundweg: Datenbank oder Werkzeug, z. B. pubmed, crossref, tooluniverse:PubMed_search")
        p.add_argument("--query", help="Fundweg: exakter Suchstring")
        p.add_argument("--fundweg-von", help="Fundweg zitatverfolgung: Slug der Quelle, deren Literatur verfolgt wurde")
        p.add_argument("--von", help="mit --volltext: Abruf-URL des Volltexts")
        p.add_argument("--at", help="mit --fundweg: Zeitpunkt der Suche (ISO 8601 mit Offset), Standard: jetzt")
        p.add_argument("--by", help="mit --fundweg: Akteur der Suche, Standard: [wiki] actor")
        p.add_argument("--aus-lauf", help="Quelle: Ordner einer Recherche mit kandidaten.json; Kennung und alle Funde "
                                          "des Kandidaten (--kandidat) kommen von dort")
        p.add_argument("--kandidat", help="mit --aus-lauf: Nr, DOI oder PMID des Kandidaten")
        p.add_argument("--trotz-rueckzug", action="store_true",
                       help="mit --doi/--pmid: auch bei Rückzug oder Bedenken anlegen, mit Vermerk")
        p.set_defaults(func=cmd_new)

    p = sub.add_parser("new", help="Seite aus Vorlage anlegen")
    p.add_argument("type")
    p.add_argument("slug")
    new_options(p)

    p = sub.add_parser("new-source", help="Quellenkarte anlegen (= new source)")
    p.add_argument("slug")
    new_options(p)
    p.set_defaults(type=SOURCE_TYPE)

    p = sub.add_parser("fundweg", help="Fundweg einer Quelle setzen oder ergänzen")
    p.add_argument("page")
    p.add_argument("--art", choices=list(FUNDWEG_ARTEN))
    p.add_argument("--db")
    p.add_argument("--query")
    p.add_argument("--von")
    p.add_argument("--at", help="Zeitpunkt (ISO 8601 mit Offset), Standard: jetzt")
    p.add_argument("--by", help="Akteur, Standard: [wiki] actor")
    p.add_argument("--add", action="store_true", help="weiteren Fundweg anhängen statt ersetzen")
    p.add_argument("--aus-lauf", help="Ordner einer Recherche mit kandidaten.json: alle Funde des Kandidaten (--kandidat) "
                                      "mit ihrem echten Zeitpunkt, db, query und by")
    p.add_argument("--kandidat", help="mit --aus-lauf: Nr, DOI oder PMID des Kandidaten")
    p.set_defaults(func=cmd_fundweg)

    p = sub.add_parser("abbildung", help="SVG-Schaubild prüfen, bereinigen und unter <bundle>/abbildungen/ ablegen")
    p.add_argument("name", help="Dateiname ohne .svg, kebab-case")
    p.add_argument("--file", required=True, help="SVG-Datei, höchstens 200 KB")
    p.add_argument("--ersetzen", action="store_true", help="eine vorhandene Abbildung gleichen Namens überschreiben")
    p.add_argument("--rolle", help="Das Bild stammt von einem Modell dieser Rolle: Das Ereignis nennt es, Lint figure_generated prüft die Unterschrift")
    p.add_argument("--modell", help="mit --rolle: Modell, wenn die Rolle in der Sitzung lief")
    p.set_defaults(func=cmd_abbildung)

    p = sub.add_parser("sources", aliases=["quellen"], help="sources[] einer Seite ergänzen oder kürzen")
    p.add_argument("page")
    p.add_argument("--add", help="Komma-Liste von Quellen-Slugs, je auch ID=SLUG (eigene Fußnote auf dieselbe Karte)")
    p.add_argument("--remove", help="Komma-Liste von IDs; nur, wenn der Text sie nicht mehr zitiert")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("capture", help="Rohquelle unveränderlich als Paket ablegen")
    p.add_argument("slug")
    p.add_argument("--url", required=True)
    p.add_argument("--file", action="append", default=[], required=True, metavar="PFAD[=REPO-PFAD]",
                   help="Datei, je Datei auch mit ihrem Pfad im Quell-Repository; mehrfach angeben")
    p.add_argument("--source-path", help="Pfad im Quell-Repository für alle Dateien ohne eigenen REPO-PFAD")
    p.add_argument("--commit")
    p.add_argument("--license")
    p.add_argument("--license-url")
    p.add_argument("--note")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("hash", aliases=["sha"], help="fulltext-Block (path, sha256, retrieved_at) ausgeben")
    p.add_argument("path")
    p.add_argument("--from", dest="retrieved_from")
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("verify", help="Prüfvermerk eintragen (OKF §5.2), auch für mehrere Seiten")
    p.add_argument("pages", nargs="+", metavar="page", help="Konzept-IDs, z. B. concepts/local-tool")
    p.add_argument("--by", required=True)
    p.add_argument("--status", choices=STATUS)
    p.add_argument("--note", help="Notiz für alle Seiten, z. B. „Charge 3“")
    p.add_argument("--force", action="store_true", help="freigeben, auch ohne Gegenprüfung zum jetzigen Inhalt")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("deprecate", help="Karte ablösen: status deprecated, die Karte bleibt")
    p.add_argument("page", metavar="seite")
    p.add_argument("--by", required=True)
    p.add_argument("--nachfolger", help="Seite, die sie ablöst; steht danach in superseded_by")
    p.add_argument("--note", help="Notiz im Ereignis")
    p.set_defaults(func=cmd_deprecate)

    p = sub.add_parser("delete", help="Folgen des Löschens zeigen; mit --yes die abgelöste Karte in den Papierkorb")
    p.add_argument("page", metavar="seite")
    p.add_argument("--yes", action="store_true", help="wirklich verschieben, sonst nur die Folgen zeigen")
    p.add_argument("--by", help="human:<id>; nur Menschen löschen")
    p.add_argument("--belege-brechen", action="store_true", help="auch wenn andere Karten sie in sources[] belegen")
    p.add_argument("--mit-rohquelle", action="store_true", help="auch das Rohquellen-Paket in den Papierkorb")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("restore", help="Papierkorb zeigen oder einen Eintrag zurückholen")
    p.add_argument("eintrag", nargs="?")
    p.add_argument("--by")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("event", help="Ereignis anhängen")
    p.add_argument("kind")
    p.add_argument("page")
    p.add_argument("detail", nargs="?")
    p.add_argument("--by")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("body", help="Körper einer Seite ersetzen, Frontmatter bleibt byte-genau")
    p.add_argument("page")
    p.add_argument("file", nargs="?", help="Markdown-Datei, - für stdin")
    p.add_argument("--nachziehen", action="store_true",
                   help="Körper ist schon geändert (Edit/Write): nur generated nachziehen, stable wie bei body")
    p.add_argument("--neu", action="store_true", help="mit --nachziehen: Seite ist neu (Write), Ereignis create statt update")
    p.add_argument("--detail")
    p.add_argument("--rolle", help="der Text beruht auf dem Entwurf dieser Rolle: Modell kommt nach generated.modelle")
    p.add_argument("--modell", help="mit --rolle: Modell, wenn die Rolle in der Sitzung lief")
    p.set_defaults(func=cmd_body)

    p = sub.add_parser("rechte", aliases=["rights"], help="Lizenzen und Nutzungsrechte der Quellen anzeigen")
    p.add_argument("page", nargs="?", help="eine Quelle, z. B. sources/x")
    p.add_argument("--aktion", help=f"Quellen nach einem Recht gruppieren: {', '.join(RIGHTS_ACTIONS)}")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rechte)

    p = sub.add_parser("lizenz", aliases=["license"], help="Lizenz einer Quelle setzen oder über APIs suchen")
    p.add_argument("page", nargs="?")
    p.add_argument("--name")
    p.add_argument("--url")
    p.add_argument("--class", dest="cls", help=f"Klasse festlegen: {', '.join(LICENSE_CLASSES)}")
    p.add_argument("--via", help="Herkunft der Angabe: originaldokument, repo-lizenz, github-api … (Standard: manuell)")
    p.add_argument("--fetch", action="store_true", help="über Crossref, DataCite, Europe PMC und OpenAlex suchen (DOI oder PMID)")
    p.add_argument("--missing", action="store_true", help="alle Quellen ohne Lizenzname oder mit unknown")
    p.add_argument("--force", action="store_true",
                   help="vorhandene Lizenz mit Namen überschreiben (mit --missing auch nicht eingeordnete)")
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p.set_defaults(func=cmd_lizenz)

    p = sub.add_parser("dashboard", help="HTML-Dashboard mit Netz, Struktur und Befunden schreiben")
    p.add_argument("--out", help="Zieldatei (Standard: [dashboard] path, build/wiki-dashboard.html)")
    p.add_argument("--oeffentlich", "--public", dest="public", action="store_true",
                   help="ohne Seiten aus [visibility] hidden, nach build/wiki-dashboard-oeffentlich.html")
    p.add_argument("--open", action="store_true", help="im Browser öffnen")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("pfad", help="Pfade: neu, pruefen, uebernehmen, export")
    was = p.add_subparsers(dest="was", required=True)
    q = was.add_parser("neu", help="Pfad unter pfade/ anlegen")
    q.add_argument("name", help="Titel")
    q.set_defaults(func=cmd_pfad)
    q = was.add_parser("pruefen", help="Was vor einem Vortrag zu wissen ist")
    q.add_argument("name", help="Pfad, oder zuerst-lesen für die Karten mit dem Schlagwort")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_pfad)
    q = was.add_parser("uebernehmen", help="Karten mit Schlagwort und order als pfade/zuerst-lesen.md anlegen")
    q.add_argument("--dry-run", action="store_true", help="nur berichten")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_pfad)
    q = was.add_parser("export", help="Pfad als eine HTML-Datei mit nur seinen Karten")
    q.add_argument("name", help="Pfad, oder zuerst-lesen für die Karten mit dem Schlagwort")
    q.add_argument("--out", help="Zieldatei (Standard: build/pfad-<name>.html)")
    q.add_argument("--oeffentlich", "--public", dest="public", action="store_true", help="ohne Interna; stoppt bei Problemen")
    q.add_argument("--notizen", action="store_true", help="Sprechernotizen mitnehmen, nie zusammen mit --oeffentlich")
    q.add_argument("--ohne-interne", action="store_true", help="öffentlich: interne Karten weglassen statt abbrechen")
    q.set_defaults(func=cmd_pfad)

    p = sub.add_parser("serve", help="Dashboard mit Bearbeiten im Browser, nur auf diesem Rechner")
    p.add_argument("--port", type=int, default=0, help="Port (Standard: ein freier)")
    p.add_argument("--no-open", action="store_true", help="den Browser nicht öffnen, nur die Adresse ausgeben")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gitattributes", help="merge=union für Ereignisstrom und Projektionen in .gitattributes eintragen")
    p.set_defaults(func=cmd_gitattributes)

    p = sub.add_parser("init", help="wiki.toml aus einem Profil anlegen")
    p.add_argument("--profile", default="default")
    p.set_defaults(func=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    hook = (args.cmd == "status" and args.hook) or (args.cmd in ("rebuild", "index") and args.hook)
    quiet = args.cmd in ("rebuild", "index") and args.quiet and not args.hook
    try:
        if args.cmd == "init":
            return cmd_init(args)
        wiki = Wiki.load(config=args.config, root=args.root)
        return args.func(wiki, args)
    except WikiError as exc:
        if (quiet or hook) and isinstance(exc, ConfigMissing):
            return 0   # Hooks in Ordnern ohne Wiki bleiben still
        if hook:
            # Bedienfehler (requires_core, kaputte wiki.toml …) als eine Zeile statt still; Exit 0 blockiert nichts
            out = {"systemMessage": f"okfw: {exc}"}
            if args.cmd == "status":
                out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext":
                                             f"okfw meldet beim Sitzungsstart: {exc}. Vor der Arbeit am Wiki beheben."}
            print(json.dumps(out, ensure_ascii=False))
            return 0
        print(f"okfw: {exc}", file=sys.stderr)
        return exc.exit_code
