"""wiki.toml: Standardwerte, bekannte Schlüssel, Schwere der Lint-Codes, Suche nach der Datei."""

import difflib
from pathlib import Path


DEFAULT_CONFIG = {
    "wiki": {"title": "Wiki", "bundle": "wiki", "actor": "claude-code/unknown", "okf_version": "0.2",
             "requires_core": ""},
    "paths": {"events": "", "raw": "", "protected": []},
    "index": {"root": "overview", "sort": "title", "entry_tag": "einstieg", "badges": [], "star_field": "",
              "banner": "", "start_heading": "Zuerst lesen", "areas_heading": "Bereiche", "unit": "Seiten"},
    "log": {"title": "Aktivitätslog", "banner": "", "show_actor": False, "link_text": "title", "labels": {}},
    "enums": {},
    "lint": {"orphans": "inbound", "fulltext_access": ["fulltext"], "claims_section": "Kernaussagen",
             "priority_field": "", "severity": {}},
    "types": {},
    "export": {},
    "dashboard": {"path": "build/wiki-dashboard.html", "auto": False, "public_path": "",
                  "glossary": ["concept", "entity"],   # Typen, deren Seiten ins Glossar kommen
                  "design": ""},                       # CSS-Datei, die nach der Vorlage eingebettet wird; leer: design.css, falls vorhanden
    "taxonomy": {"field": "domain"},   # Feld, das die Dashboard-Suche als Themenbereich mitliest
    "labels": {},
    "licenses": {"patterns": {}},     # weitere Muster: {"example.org" = "vorbehalten"}
    "rights": {},                     # Matrix überschreiben: [rights.vorbehalten] abbildung = "nein: …"
    "scope": {"field": "", "label": "Umfang", "default": "", "derive": True},
    # Seiten mit einem Wert aus hidden fehlen in der öffentlichen Ausgabe (dashboard --oeffentlich) ganz
    "visibility": {"field": "", "label": "Sichtbarkeit", "default": "", "hidden": [], "derive": True},
    "fundweg": {"field": "fundweg"},
    # Freigabe: body auf einer stable-Seite setzt draft (die Freigabe gilt dem alten Inhalt)
    # signed_human: Lint human_unsigned; human: Kennung, die „Zur Freigabe“ im Dashboard vorbelegt (human:jdoe);
    # check_command: Aufruf der Gegenprüfung für die Liste „Gegenprüfung ohne Prüfwert“ (z. B. /lesen --pruefen)
    "trust": {"edit_resets_stable": True, "signed_human": False, "human": "", "check_command": ""},
    # new source --doi/--pmid/--arxiv/--manuell: wohin Metadatenprüfung und Crossref-Hinweise gehen
    "sources": {"checked_field": "metadaten_geprueft", "updates_field": "crossref_hinweise",
                "retraction_field": "rueckzug", "manual_csl_type": "document"},
    # Modellrollen (rollen.py): Datenklasse des Wikis und gesperrte Anbieter; je Rolle eine freie Tabelle
    # [rollen.lesen] harness, anbieter, modell, die rollen.py prüft
    "rollen": {"daten": "", "sperre": []},
    # Befehl bild: eigene Stilvorlagen, Name → Text; {motiv} steht für den Prompt, sonst wird die Vorlage angehängt
    "bild": {"stile": {}},
}
TYPE_DEFAULTS = {"folder": "", "label": "", "description": "", "required": [], "recommended": ["title", "description"],
                 "sections": [], "min_links": 2, "needs_footnotes": False, "stale_months": 0, "template": "",
                 "defaults": {}, "todo": {}, "subtype": ""}
# Exporte, die der Kern schreibt, mit ihren Schlüsseln ([export.csl], [export.bibliography]); ohne Eintrag kein Export
EXPORT_KEYS = {"csl": ("path", "field", "style", "lang"), "bibliography": ("path", "title")}
# Abschnitte der wiki.toml mit Projektwerten als Schlüsseln (Feldnamen, Klassen): config_unknown prüft sie nicht.
# Ebenso frei, weil unknown_config in bekannte Schlüssel nicht absteigt: [log] labels, [licenses.patterns] und
# defaults und todo der Typen.
FREE_TABLES = {"enums", "labels", "rights"}
# Platzhalter für Kurzreferat und Kernaussagen neuer Quellenkarten je access; [types.source.todo.<access>] ersetzt sie.
TODO_BY_ACCESS = {
    "citation": {"kurzreferat": "> [!todo] Nur Metadaten geprüft – Inhalt noch nicht gelesen. Nichts erfinden.",
                 "kernaussagen": "> [!todo] Erst nach Lektüre (Abstract oder Volltext)."},
    "abstract": {"kurzreferat": "> [!todo] Kurzreferat aus dem Abstract (als *abstract-basiert* kennzeichnen).",
                 "kernaussagen": "> [!todo] Höchstens Abstract-Aussagen, mit „(Abstract)“ markiert."},
    "fulltext": {"kurzreferat": "> [!todo] Kurzreferat aus dem Volltext.",
                 "kernaussagen": "> [!todo] Kernaussagen mit Fundstelle (S./Tab./Abschn.)."},
    "originaldokument": {"kurzreferat": "> [!todo] Kurzreferat aus dem Originaldokument.",
                         "kernaussagen": "> [!todo] Kernaussagen mit Fundstelle (Art./Abs./Abschn./Zeile)."},
}
LOG_LABELS = {
    "init": "Initialisierung", "bootstrap": "Bootstrap", "capture": "Quelle erfasst", "create": "Erstellt",
    "creation": "Erstellt", "update": "Aktualisiert", "verify": "Verifiziert", "deprecate": "Als überholt markiert",
    "delete": "Entfernt", "restore": "Wiederhergestellt", "merge": "Zusammengeführt", "enrich": "Angereichert", "export": "Export", "note": "Notiz",
}
# Schwere je Befund. Projekte überschreiben einzelne Codes in [lint.severity] (auch mit "off").
SEVERITY = {
    # OKF §8, §9, §11 – Konformität
    "bundle_missing": "error", "frontmatter_invalid": "error", "missing_type": "error",
    "root_index_frontmatter": "error", "okf_version_missing": "warn", "index_frontmatter": "error",
    "log_frontmatter": "error", "log_date_heading": "error", "log_order": "warn",
    # OKF §5, §7 – Herkunft, Vertrauen, Lebenszyklus
    "status_invalid": "error", "generated_invalid": "error", "verified_invalid": "error",
    "timestamp_invalid": "error", "actor_format": "warn", "stable_unverified": "warn", "stale": "warn",
    "verified_outdated": "warn",   # bei status stable error
    "order_invalid": "warn", "modelle_invalid": "warn", "modell_unbekannt": "warn", "modell_herkunft_fehlt": "warn",
    "human_without_check": "warn", "human_unsigned": "warn", "verified_unhashed": "info", "check_outdated": "info",
    "source_invalid": "error", "source_duplicate_id": "error", "source_missing": "error",
    "footnote_unsourced": "error", "footnote_undefined": "warn", "source_uncited": "info",
    # Profil aus wiki.toml
    "missing_required": "error", "missing_recommended": "warn", "enum_invalid": "error",
    "type_unknown": "info", "type_folder_mismatch": "warn", "path_not_kebab": "warn",
    "sections_missing": "warn", "no_footnotes": "warn",
    # Graph
    "broken_link": "warn", "superseded_missing": "warn", "link_leaves_bundle": "warn", "few_links": "warn", "orphan": "warn",
    "contradiction_one_sided": "info", "projection_stale": "warn", "projection_unavailable": "info",
    # Pfade
    "pfad_karte_entwurf": "warn", "pfad_karte_veraltet": "warn", "pfad_karte_widerspruch": "warn",
    "pfad_karte_abgeloest": "warn", "pfad_karte_fehlt": "error", "pfad_text_ohne_beleg": "info",
    "pfad_sichtbarkeit_fehlt": "error", "pfad_lage": "warn", "pfad_ohne_station": "warn",
    # Quellenkarten und Rohquellen
    "raw_missing": "error", "raw_tampered": "error", "raw_uningested": "info",
    "fulltext_tampered": "error", "fulltext_absent": "warn", "fulltext_unanchored": "warn",
    "citation_with_claims": "error", "claim_on_citation_only": "warn",
    "priority_citation_only": "warn", "csl_incomplete": "error", "source_undated": "warn",
    # Lizenzen und Nutzungsrechte
    "license_missing": "warn", "license_unknown": "info", "license_unclassified": "info",
    "fulltext_rights": "warn", "fulltext_shared": "warn",
    # Umfang, Sichtbarkeit und Fundweg
    "scope_missing": "warn", "visibility_leak": "warn", "visibility_values_missing": "error",
    "fundweg_missing": "info", "fundweg_incomplete": "warn", "fundweg_invalid": "error",
    # Schaubilder
    "figure_caption": "warn", "figure_missing": "error", "figure_rights": "warn", "figure_generated": "warn",
    # wiki.toml
    "config_unknown": "warn", "rollen_unknown": "error", "design_unsafe": "error",
}
# Wie eine Quelle gefunden wurde; gemeinsames Vokabular aller Wikis.
FUNDWEG_ARTEN = {
    "suche": "Suche", "modellvorschlag": "Modellvorschlag", "material": "Material",
    "person": "Von der Person", "zitatverfolgung": "Zitatverfolgung",
}
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def known_config_keys() -> set[str]:
    """Schlüssel der wiki.toml, die der Kern kennt: „abschnitt.schlüssel“, „export.<export>.<schlüssel>“,
    „types.*.<schlüssel>“; Tabellen mit Projektwerten als Schlüsseln (FREE_TABLES) nur mit ihrem Namen."""
    keys = {f"export.{name}.{k}" for name, ks in EXPORT_KEYS.items() for k in ks}
    keys |= {f"types.*.{k}" for k in TYPE_DEFAULTS}
    for sec, spec in DEFAULT_CONFIG.items():
        if sec not in ("export", "types"):
            keys |= {f"{sec}.{k}" for k in spec} if spec and sec not in FREE_TABLES else {sec}
    return keys


def unknown_config(cfg: dict) -> list[tuple[str, str]]:
    """(Name, Vorschlag) je Schlüssel der wiki.toml, den der Kern nicht kennt, und je unbekanntem Code in
    [lint.severity]; Vorschlag ist der ähnlichste bekannte Name oder "". Tabellen aus FREE_TABLES bleiben ungeprüft."""
    out = []

    def check(name: str, key: str, choices) -> None:
        if key not in choices:
            close = difflib.get_close_matches(key, list(choices), n=1, cutoff=0.75)
            out.append((name, close[0] if close else ""))

    for sec, spec in cfg.items():
        if sec in FREE_TABLES:
            continue
        if sec not in DEFAULT_CONFIG:
            check(f"[{sec}]", sec, DEFAULT_CONFIG)
        elif sec == "export":
            for name, table in spec.items():
                check(f"[export.{name}]", name, EXPORT_KEYS)
                if name in EXPORT_KEYS and isinstance(table, dict):
                    for key in table:
                        check(f"[export.{name}] {key}", key, EXPORT_KEYS[name])
        elif sec == "types":
            for name, table in spec.items():
                for key in table if isinstance(table, dict) else ():
                    check(f"[types.{name}] {key}", key, TYPE_DEFAULTS)
        elif sec == "rollen":
            # Tabellen unter [rollen] sind Rollen ([rollen.lesen] …), deren Felder rollen.py prüft; geprüft wird der Rest
            for key, value in spec.items():
                if not isinstance(value, dict):
                    check(f"[rollen] {key}", key, DEFAULT_CONFIG["rollen"])
        else:
            for key in spec:
                check(f"[{sec}] {key}", key, DEFAULT_CONFIG[sec])
            if sec == "lint":
                for code in spec.get("severity") or {}:
                    check(f"[lint.severity] {code}", code, SEVERITY)
    return out


def find_config(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        candidate = directory / "wiki.toml"
        if candidate.is_file():
            return candidate
    return None
