"""Wiki: Pfade. Ein Pfad ist eine Karte mit `type: pfad`; Stationen und Karten stehen im Körper, die Lage im Frontmatter.
Der Pfad verweist nur: Jede Karte zeigt ihren eigenen Stand, eine Freigabe des Pfads deckt nur seinen Text und die Folge."""

import datetime as dt
import html
import json
import re
import secrets
from pathlib import Path

from .basis import (CODE_RE, CONTRADICTION_RE, FOOTNOTE_DEF_RE, FOOTNOTE_REF_RE, Page, SKILL_DIR, SLUG_RE, WikiError, as_list, is_empty,
                    one_line, replace_file)
from .frontmatter import FRONTMATTER_RE
from .wiki_dashboard import embedded_fonts
from .pfad import (PFAD_ORDNER, PFAD_TYPE, RASTER, ZEIGT, fussnoten_heraus, ist_kartenziel, lage_anwenden, lage_aus,
                   pos_gueltig, station_slug, stationen_kern, stationen_lesen, stationen_schreiben)

MAX_STATIONEN = 200
STANDARD = "zuerst-lesen"
_ZAHL_RE = re.compile(r"(?<![\w.])\d")
_LISTENZIFFER_RE = re.compile(r"^\s*\d+[.)]\s", re.M)
_INNERER_LINK_RE = re.compile(r'<a\b[^>]*\bdata-page="([^"]*)"[^>]*>(.*?)</a>', re.S)
# Befund → (Abzeichen der Karte, Stopp beim öffentlichen Export)
_ZUSTAENDE = {"abgeloest": ("pfad_karte_abgeloest", True), "veraltet": ("pfad_karte_veraltet", False),
              "entwurf": ("pfad_karte_entwurf", False), "widerspruch": ("pfad_karte_widerspruch", False)}


class WikiPfad:
    def pfade(self) -> list[Page]:
        return [p for p in self.pages() if p.type == PFAD_TYPE and p.fm is not None]

    def pfad_seite(self, name: str) -> Page | None:
        """Ein Pfad nach ID oder Dateiname. None: der Standardpfad `zuerst-lesen`, den es nur ohne Datei gibt."""
        name = name.removeprefix("/").removesuffix(".md")
        for p in self.pfade():
            if name in (p.id, p.path.stem):
                return p
        if name == STANDARD and self.learning_path():
            return None
        raise WikiError(f"Pfad fehlt: {name}. Vorhanden: {', '.join(p.id for p in self.pfade()) or 'keiner'}")

    def pfad_raster(self, p: Page) -> int:
        raster = (p.fm or {}).get("raster", RASTER)
        return raster if isinstance(raster, int) and not isinstance(raster, bool) and 1 <= raster <= 48 else RASTER

    def pfad_stationen(self, p: Page) -> tuple[list[dict], list[str], list[str]]:
        """Stationen mit aufgelösten Karten und Lage, die Fußnoten des Körpers und die Meldungen zur Lage."""
        stationen, fussnoten = stationen_lesen(p.body)
        by_path = {q.path.resolve(): q for q in self.pages()}
        for s in stationen:
            gesehen, elemente = set(), []
            for e in s["elemente"]:
                if e["art"] == "karte":
                    ziel = self.resolve(p.path, e["ziel"])
                    q = by_path.get(ziel)
                    if q is None or q.type == PFAD_TYPE:
                        e["fehlt"] = True
                        e["intern"] = ziel in self.withheld
                    else:
                        e["id"], e["schluessel"] = q.id, f"/{q.id}.md"
                    if e["schluessel"] in gesehen:   # dieselbe Karte zweimal auf einer Station hätte nur eine Lage
                        continue
                    gesehen.add(e["schluessel"])
                elemente.append(e)
            s["elemente"] = elemente
        meldungen = lage_anwenden(stationen, (p.fm or {}).get("lage"), self.pfad_raster(p))
        return stationen, fussnoten, meldungen

    def pfad_karten(self, p: Page) -> list[Page]:
        """Die Karten des Pfads in der Reihenfolge des ersten Auftretens."""
        seiten = {q.id: q for q in self.pages()}
        ids = [e["id"] for s in self.pfad_stationen(p)[0] for e in s["elemente"] if e.get("id")]
        return [seiten[i] for i in dict.fromkeys(ids)]

    def karte_stand(self, q: Page) -> list[dict]:
        """Abzeichen einer Karte auf einer Station: ihr eigener Stand, unabhängig vom Pfad."""
        fm = q.fm or {}
        out = []
        if fm.get("status") == "deprecated" or fm.get("superseded_by"):
            eintrag = {"art": "abgeloest", "text": "Abgelöst"}
            nachfolger = next((s for s in self.pages() if s.path.resolve() == self.resolve(q.path, str(fm["superseded_by"]))),
                              None) if fm.get("superseded_by") else None
            if nachfolger is not None:
                eintrag |= {"nachfolger": nachfolger.id, "text": f"Abgelöst durch {nachfolger.title}"}
            out.append(eintrag)
        if q.fm is not None:
            state = self.release_state(q)
            if state["outdated"]:
                out.append({"art": "veraltet", "text": "Freigabe verfallen"})
            elif state["valid"]:
                out.append({"art": "geprueft", "text": "geprüft"})
        if fm.get("status") == "draft":
            out.append({"art": "entwurf", "text": "Entwurf"})
        if CONTRADICTION_RE.search(CODE_RE.sub("", q.body)):
            out.append({"art": "widerspruch", "text": "Widerspruch"})
        return out

    def pfad_befunde(self, p: Page) -> list[dict]:
        """Was vor einem Vortrag zu wissen ist; `pfad pruefen`, der Lint und „Vortrag starten“ zeigen dieselbe Liste.
        stopp: Der öffentliche Export bricht deswegen ab."""
        seiten = {q.id: q for q in self.pages()}
        out = []
        for s in self.pfad_stationen(p)[0]:
            name = s["titel"] or p.title
            for e in s["elemente"]:
                if e["art"] == "text":
                    text = _LISTENZIFFER_RE.sub("", CODE_RE.sub("", e["text"]))
                    if _ZAHL_RE.search(text) and not FOOTNOTE_REF_RE.search(text):
                        out.append({"code": "pfad_text_ohne_beleg", "station": name, "karte": "", "stopp": False,
                                    "text": f"Station „{name}“: eigener Text mit Zahl, ohne Fußnote"})
                elif e.get("fehlt"):
                    out.append({"code": "pfad_karte_fehlt", "station": name, "karte": e["ziel"], "stopp": True,
                                "text": f"Station „{name}“: Karte {e['ziel']} fehlt"})
                else:
                    q = seiten[e["id"]]
                    for z in self.karte_stand(q):
                        if z["art"] in _ZUSTAENDE:
                            code, stopp = _ZUSTAENDE[z["art"]]
                            out.append({"code": code, "station": name, "karte": q.id, "stopp": stopp,
                                        "text": f"Station „{name}“: {q.title}: {z['text']}"})
        return out

    def _lint_pfad(self, p: Page, add) -> None:
        stationen, _, meldungen = self.pfad_stationen(p)
        if not stationen:
            add("pfad_ohne_station", p.path, "keine Station: Eine Überschrift erster Ebene beginnt eine Station")
        for meldung in meldungen:
            add("pfad_lage", p.path, meldung)
        for b in self.pfad_befunde(p):
            add(b["code"], p.path, b["text"])
        field = self.cfg["visibility"].get("field")
        if field and is_empty(p.fm.get(field)):
            add("pfad_sichtbarkeit_fehlt", p.path, f"ein Pfad braucht einen eigenen Wert für {field}: Sein Text ist nicht "
                "dadurch geschützt, dass seine Karten es sind; ohne Wert bleibt er intern")

    # ---------------------------------------------------------------- Daten für Dashboard und Export

    def _pfad_element(self, e: dict, texte: dict, seiten: dict) -> dict | None:
        out = {"art": e["art"], "pos": e["pos"], "schluessel": e["schluessel"]}
        if e["art"] == "text":
            return out | {"html": texte.get(e["schluessel"]) or f"<pre>{html.escape(e['text'])}</pre>"}
        if e.get("intern"):
            return None   # öffentliche Ausgabe: nichts über eine weggelassene Karte, auch nicht ihr Titel
        out["zeigt"] = e.get("zeigt", "kurz")
        if e.get("fehlt"):
            return out | {"fehlt": True, "titel": e["name"] or e["ziel"]}
        q = seiten[e["id"]]
        spec = self.types.get(q.type) or {}
        return out | {"id": q.id, "titel": q.title, "typ": spec.get("label") or q.type,
                      "beschreibung": q.description, "stand": self.karte_stand(q)}

    def pfad_daten(self, p: Page, reader: dict, notizen: bool = False) -> dict:
        """Ein Pfad für die Oberfläche. Sprechernotizen nur auf Wunsch: Sie gehören nicht in eine Datei zum Weitergeben."""
        seiten = {q.id: q for q in self.pages()}
        stationen, _, _ = self.pfad_stationen(p)
        texte = reader.get(p.id, {}).get("texte", {})
        daten = []
        for s in stationen:
            elemente = [self._pfad_element(e, texte.get(s["slug"], {}), seiten) for e in s["elemente"]]
            eintrag = {"slug": s["slug"], "titel": s["titel"] or p.title, "zeilen": s["zeilen"],
                       "elemente": [e for e in elemente if e]}
            if notizen:
                eintrag["notizen"] = s["notizen"]
            daten.append(eintrag)
        return {"id": p.id, "titel": p.title, "beschreibung": p.description,
                "standard": False, "raster": self.pfad_raster(p), "stationen": daten,
                "befunde": [] if self.public else self.pfad_befunde(p)}

    def standard_pfad_daten(self) -> dict | None:
        """Karten mit Schlagwort und `order`, eine je Station, damit ein Wiki seine Reihenfolge behält. Steht in keiner
        Datei; legt jemand `pfade/zuerst-lesen.md` an, gilt nur noch die."""
        seiten = self.learning_path()
        if not seiten or (self.bundle / f"{PFAD_ORDNER}/{STANDARD}.md").exists():
            return None
        stationen = []
        vergeben: set[str] = set()
        befunde = []
        for q in seiten:
            spec = self.types.get(q.type) or {}
            stationen.append({"slug": station_slug(q.title, vergeben), "titel": q.title, "zeilen": 7, "elemente": [
                {"art": "karte", "pos": [0, 0, RASTER, 7], "schluessel": f"/{q.id}.md", "zeigt": "absatz", "id": q.id,
                 "titel": q.title, "typ": spec.get("label") or q.type, "beschreibung": q.description,
                 "stand": self.karte_stand(q)}]})
            befunde += [{"code": _ZUSTAENDE[z["art"]][0], "station": q.title, "karte": q.id, "stopp": _ZUSTAENDE[z["art"]][1],
                         "text": f"Station „{q.title}“: {q.title}: {z['text']}"}
                        for z in stationen[-1]["elemente"][0]["stand"] if z["art"] in _ZUSTAENDE]
        return {"id": STANDARD, "titel": "Zuerst lesen", "beschreibung": "", "standard": True,
                "raster": RASTER, "stationen": stationen, "befunde": [] if self.public else befunde}

    def pfade_daten(self, reader: dict) -> list[dict]:
        daten = [self.pfad_daten(p, reader) for p in sorted(self.pfade(), key=lambda p: p.title.lower())]
        standard = self.standard_pfad_daten()
        return ([standard] if standard else []) + daten

    def render_pfad(self, name: str, notizen: bool = False) -> str:
        """Ein Pfad als eine Datei: nur seine Karten, nicht das ganze Wiki. Abzeichen und Datum sind eingefroren."""
        p = self.pfad_seite(name)
        reader = self.reader_html()
        if p is None:
            pfad = self.standard_pfad_daten()
            if pfad is None:
                raise WikiError("Unter „Zuerst lesen“ steht keine Karte, die hier gezeigt werden darf")
            ids = [e["id"] for s in pfad["stationen"] for e in s["elemente"]]
        else:
            pfad = self.pfad_daten(p, reader, notizen=notizen)
            ids = [e["id"] for s in pfad["stationen"] for e in s["elemente"] if e.get("id")]
        ids = list(dict.fromkeys(ids))
        enthalten = set(ids)

        def nur_enthaltene(text: str) -> str:
            # sonst führte ein Link ins Leere und nennte die ID einer Karte, die nicht mitgeht
            return _INNERER_LINK_RE.sub(lambda m: m.group(0) if m.group(1) in enthalten else f"<span>{m.group(2)}</span>", text)

        for s in pfad["stationen"]:
            for e in s["elemente"]:
                if e["art"] == "text":
                    e["html"] = nur_enthaltene(e["html"])
        karten = {i: {"html": nur_enthaltene(reader[i]["html"]), "excerpt": reader[i]["excerpt"],
                      "refs": [r["entry"] for r in reader[i]["refs"]]} for i in ids}
        data = {"titel": self.dashboard_title(), "pfad": pfad | {"befunde": []}, "karten": karten,
                "stand": dt.date.today().isoformat()}
        return self._pfad_seite_html(data, pfad["titel"])

    def _pfad_seite_html(self, data: dict, titel: str) -> str:
        vorlage = (SKILL_DIR / "templates" / "pfad.html").read_text(encoding="utf-8")
        stil = re.search(r"<style>(.*?)</style>", (SKILL_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8"), re.S)
        nonce = secrets.token_urlsafe(16)
        policy = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src data: blob:; "
                  "font-src data:; base-uri 'none'; form-action 'none'")
        vorlage = vorlage.replace("<script>", f'<script nonce="{nonce}">').replace("__POLICY__", policy)
        vorlage = vorlage.replace("/*__STIL__*/", stil.group(1) if stil else "", 1)
        design = re.sub(r"<(?=script)", r"\\3c ", self.design_css(), flags=re.I)
        teile = {"/*__FONTS__*/": embedded_fonts(), "/*__DESIGN__*/": design, "__TITLE__": html.escape(titel),
                 "/*__PFAD_CSS__*/": (SKILL_DIR / "templates" / "pfad.css").read_text(encoding="utf-8"),
                 "/*__PFAD_JS__*/": (SKILL_DIR / "templates" / "pfad.js").read_text(encoding="utf-8"),
                 "__DATA__": json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")}
        return re.sub("|".join(map(re.escape, teile)), lambda m: teile[m.group(0)], vorlage)

    def pfad_export(self, name: str, out: str | Path | None = None, public: bool = False, notizen: bool = False,
                    ohne_interne: bool = False) -> tuple[Path, list[str]]:
        """Schreibt den Pfad als eine Datei; gibt den Pfad und die Warnungen zurück. Öffentlich stoppt, was im Vortrag
        falsch stünde: fehlende und abgelöste Karten, interne Karten (außer mit ohne_interne), ein Pfad ohne eigenen Wert."""
        if public and notizen:
            raise WikiError("--notizen geht nicht zusammen mit --oeffentlich: Sprechernotizen bleiben bei dir")
        p = self.pfad_seite(name)
        standard = self.standard_pfad_daten() if p is None else None
        befunde = self.pfad_befunde(p) if p is not None else (standard or {}).get("befunde", [])
        warnungen = [b["text"] for b in befunde]
        quelle = self
        if public:
            quelle = self.public_view()
            hidden = self.hidden_ids()
            if ohne_interne:   # was wegfällt, hält den Export nicht auf
                befunde = [b for b in befunde if b["karte"] not in hidden]
                warnungen = [b["text"] for b in befunde]
            if p is not None and p.id in hidden:
                field = self.cfg["visibility"]["field"]
                wert = p.fm.get(field)
                raise WikiError(f"{p.id} trägt keinen eigenen Wert für {field}: öffentlich nichts geschrieben" if is_empty(wert)
                                else f"{p.id} ist selbst {wert}: öffentlich nichts geschrieben")
            stopp = [b["text"] for b in befunde if b["stopp"]]
            if stopp:
                raise WikiError("Öffentlich nichts geschrieben:\n  " + "\n  ".join(stopp))
            karten = self.pfad_karten(p) if p is not None else self.learning_path()
            intern = [q for q in karten if q.id in hidden]
            if intern and not ohne_interne:
                stellen = [f"Station „{s['titel'] or p.title}“: {e['id']}" for s in self.pfad_stationen(p)[0] for e in s["elemente"]
                           if e.get("id") in {q.id for q in intern}] if p is not None else [q.id for q in intern]
                raise WikiError("Interne Karten im Pfad, öffentlich nichts geschrieben (weglassen: --ohne-interne):\n  "
                                + "\n  ".join(stellen))
            warnungen += [f"weggelassen, weil intern: {q.id}" for q in intern]
        stem = p.path.stem if p is not None else STANDARD
        ziel = Path(out).expanduser().resolve() if out else self.root / "build" / f"pfad-{stem}{'-oeffentlich' if public else ''}.html"
        inhalt = quelle.render_pfad(name, notizen=notizen)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        replace_file(ziel, inhalt)
        return ziel, warnungen

    # ---------------------------------------------------------------- Schreiben

    def _pfad_extra(self) -> dict:
        """Neue Pfade sind in einem Wiki mit [visibility] erst einmal intern; freigeben ist eine eigene Entscheidung."""
        field, hidden = self.cfg["visibility"].get("field"), as_list(self.cfg["visibility"].get("hidden"))
        return {field: str(hidden[0])} if field and hidden else {}

    def pfad_neu(self, titel: str) -> Path:
        titel = one_line(titel)
        if not titel:
            raise WikiError("Ein Pfad braucht einen Titel")
        ohne = titel.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
        basis = re.sub(r"[^a-z0-9]+", "-", ohne).strip("-") or "pfad"
        slug, n = basis, 2
        while (self.bundle / PFAD_ORDNER / f"{slug}.md").exists():
            slug, n = f"{basis}-{n}", n + 1
        if not SLUG_RE.fullmatch(slug):
            raise WikiError(f"Aus {titel!r} entsteht kein Dateiname")
        return self.new_page(PFAD_TYPE, slug, title=titel, extra=self._pfad_extra())

    def _pfad_stationen_pruefen(self, eingabe, raster: int, bisher: dict) -> tuple[list[dict], list[str]]:
        """Stationen aus der Oberfläche in die Form von stationen_lesen und die Fußnoten aus den Textfeldern. Neue Karten
        werden Links auf die Bundle-Wurzel; bisher (ID → Element) behält Schreibweise, Anker und Beschriftung der alten."""
        if not isinstance(eingabe, list) or len(eingabe) > MAX_STATIONEN:
            raise WikiError(f"stationen: eine Liste mit höchstens {MAX_STATIONEN} Einträgen")
        if not all(isinstance(s, dict) and isinstance(s.get("elemente"), list) for s in eingabe):
            raise WikiError("Station: titel und elemente")
        if sum(s.get("vorspann") is True for s in eingabe) > 1:
            raise WikiError("Ein Pfad hat höchstens einen Vorspann")
        seiten = {q.id: q for q in self.pages()}
        neu, vergeben, fussnoten = [], set(), []
        for s in sorted(eingabe, key=lambda s: s.get("vorspann") is not True):   # Vorspann heißt: vor der ersten Überschrift
            vorspann = s.get("vorspann") is True
            if not vorspann and not isinstance(s.get("titel"), str):
                raise WikiError("Jede Station braucht einen Titel")
            titel = one_line(s.get("titel")) if not vorspann else ""
            if not titel and not vorspann:
                raise WikiError("Jede Station braucht einen Titel")
            notizen = s.get("notizen", [])
            if not isinstance(notizen, list) or not all(isinstance(n, str) for n in notizen):
                raise WikiError("notizen: eine Liste von Texten")
            elemente, gesehen, textnr = [], set(), 0
            for e in s["elemente"]:
                art = e.get("art") if isinstance(e, dict) else None
                if art == "text":
                    if not isinstance(e.get("text"), str) or not e["text"].strip():
                        continue   # leere Textfelder entfallen
                    text, eigene = fussnoten_heraus(e["text"])
                    fussnoten += eigene
                    if not text.strip():
                        continue
                    textnr += 1
                    neu_e = {"art": "text", "schluessel": f"text-{textnr}", "text": text}
                elif art == "karte":
                    kid = e.get("id")
                    q = seiten.get(kid) if isinstance(kid, str) else None
                    if q is not None and q.type != PFAD_TYPE:
                        alt = bisher.get(q.id)
                        neu_e = {"art": "karte", "name": alt["name"] if alt else q.title, "ziel": alt["ziel"] if alt else f"/{q.id}.md",
                                 "schluessel": f"/{q.id}.md", "id": q.id}
                    elif isinstance(e.get("ziel"), str) and ist_kartenziel(e["ziel"]):   # fehlende Karte bleibt sichtbar stehen
                        name = e.get("name") if isinstance(e.get("name"), str) else ""
                        neu_e = {"art": "karte", "name": one_line(name), "ziel": e["ziel"], "schluessel": e["ziel"]}
                    else:
                        raise WikiError(f"Karte nicht gefunden: {e.get('id') or e.get('ziel')}")
                    if neu_e["schluessel"] in gesehen:
                        continue
                    gesehen.add(neu_e["schluessel"])
                    if e.get("zeigt", "kurz") not in ZEIGT:
                        raise WikiError(f"zeigt: {', '.join(ZEIGT)}")
                    neu_e["zeigt"] = e.get("zeigt", "kurz")
                else:
                    raise WikiError("Element: art karte oder text")
                if e.get("pos") is not None and not e.get("auto"):
                    if not pos_gueltig(e["pos"], raster):
                        raise WikiError(f"Lage ungültig: {e['pos']!r}")
                    neu_e["pos"] = e["pos"]
                elemente.append(neu_e)
            notizen = [n.strip() for n in notizen if n.strip()]
            if vorspann and not elemente and not notizen:
                continue   # ein leerer Vorspann steht in keiner Datei
            neu.append({"titel": titel, "vorspann": vorspann, "slug": station_slug("vorspann" if vorspann else titel, vergeben),
                        "elemente": elemente, "notizen": notizen})
        return neu, fussnoten

    def pfad_speichern(self, name: str, stationen) -> bool:
        """Schreibt Stationen und Lage in einem Zug. Der Körper ändert sich nur, wenn sich Text, Folge oder Notizen ändern:
        Eine verschobene Karte lässt die Freigabe des Pfads stehen. Gibt zurück, ob sich etwas geändert hat."""
        p = self.pfad_seite(name)
        if p is None:
            raise WikiError("Der Standardpfad steht in keiner Datei: erst übernehmen (pfad uebernehmen)")
        path = self.page_path(p.id)
        self._kein_symlink(path)
        alt, fussnoten, _ = self.pfad_stationen(p)
        bisher = {e["id"]: e for s in alt for e in s["elemente"] if e.get("id")}
        neu, eigene = self._pfad_stationen_pruefen(stationen, self.pfad_raster(p), bisher)
        namen = {FOOTNOTE_DEF_RE.match(f).group(1) for f in eigene}
        fussnoten_neu = [f for f in fussnoten if FOOTNOTE_DEF_RE.match(f).group(1) not in namen] + eigene
        koerper_gleich = stationen_kern(alt, schluessel=True) == stationen_kern(neu, schluessel=True) and fussnoten_neu == fussnoten
        koerper = stationen_schreiben(neu, fussnoten_neu)
        if stationen_kern(stationen_lesen(koerper)[0]) != stationen_kern(neu):
            raise WikiError("Ein Textfeld enthält etwas, das den Aufbau ändert: eine Überschrift erster Ebene, einen "
                            "Listenpunkt nur mit Karten-Link, `[!notiz]` oder eine Zeile `---`. Bitte umformulieren")
        lage = lage_aus(neu)
        updates, remove = {}, []
        if lage != (p.fm.get("lage") or {}):
            if lage:
                updates["lage"] = lage
            elif "lage" in p.fm:
                remove.append("lage")
        if koerper_gleich and not updates and not remove:
            return False
        updates["generated"] = self.generated_now(p.fm)
        vorher = path.read_text(encoding="utf-8")
        text = self._frontmatter_changed(path, updates, remove)
        if not koerper_gleich:
            m = FRONTMATTER_RE.match(text)
            text = text[:m.end()] + "\n" + koerper.strip() + "\n"
        try:
            path.write_text(text, encoding="utf-8")
            self._pages = None
            if koerper_gleich:
                self.append_event("update", p.id, "Pfad: Anordnung")
            else:
                self.body_changed(p.id, "Pfad: Stationen")
        except (OSError, WikiError):
            path.write_text(vorher, encoding="utf-8")
            self._pages = None
            raise
        return True

    def pfad_uebernehmen(self, dry_run: bool = False) -> dict:
        """Legt aus den Karten mit dem Schlagwort `pfade/zuerst-lesen.md` an, intern. Die Karten bleiben unverändert: Das
        Schlagwort liefert weiter „Zuerst lesen“ im Root-Index. Gibt den Bericht zurück."""
        seiten = self.learning_path()
        if not seiten:
            raise WikiError("Keine Karte trägt das Schlagwort aus [index] entry_tag: nichts zu übernehmen")
        auffaellig = []
        for q in seiten:
            order = q.fm.get("order")
            if order is None:
                auffaellig.append(f"{q.id}: ohne order, steht am Ende")
            elif isinstance(order, (str, bool)) or self.order_rank(order) == 99 and order != 99:
                auffaellig.append(f"{q.id}: order {order!r} ist keine Zahl")
        ziel = f"{PFAD_ORDNER}/{STANDARD}"
        bericht = {"pfad": ziel, "schritte": [{"id": q.id, "titel": q.title} for q in seiten],
                   "auffaellig": auffaellig, "geschrieben": False}
        if (self.bundle / f"{ziel}.md").exists():
            raise WikiError(f"{ziel}.md gibt es schon; nichts überschrieben")
        if dry_run:
            return bericht
        vergeben: set[str] = set()
        stationen = [{"titel": q.title, "slug": station_slug(q.title, vergeben), "notizen": [], "elemente": [
            {"art": "karte", "name": q.title, "ziel": f"/{q.id}.md", "schluessel": f"/{q.id}.md", "pos": [0, 0, RASTER, 7],
             "zeigt": "absatz"}]} for q in seiten]   # dieselbe Anzeige wie der abgeleitete Pfad
        koerper = stationen_schreiben(stationen)
        self.new_page(PFAD_TYPE, STANDARD, title="Zuerst lesen", description="", extra=self._pfad_extra() | {"lage": lage_aus(stationen)})
        self.replace_body(ziel, koerper, "Pfad aus den Karten mit dem Schlagwort angelegt")
        return bericht | {"geschrieben": True}
