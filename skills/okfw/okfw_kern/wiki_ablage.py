"""Wiki: Karten ablösen, löschen und aus dem Papierkorb zurückholen.

Ablösen setzt status: deprecated, die Karte bleibt. Löschen ist die zweite Stufe: Es verlangt eine abgelöste Karte (eine
mit unlesbarem Frontmatter lässt sich nicht ablösen und darf direkt) und human:<kennung> und verschiebt nur, nach
<bundle>/.papierkorb/<eintrag>/. Endgültig entfernt der Kern nichts.
"""

import fnmatch
import json
import os
import re
from pathlib import Path

from .basis import (ACTOR_RE, CODE_RE, FOOTNOTE_DEF_RE, FOOTNOTE_REF_RE, LINK_RE, PAPIERKORB, Page, RESERVED,
                    SOURCE_TYPE, WikiError, as_list, now_iso, sha256_of)

EINTRAG_RE = re.compile(r"\d{8}T\d{6}Z-[A-Za-z0-9_-]+")
PAKET_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
EINTRAG_DATEI = "eintrag.json"
KARTE_DATEI = "karte.md"
ROH_ORDNER = "rohquelle"
MAX_EINTRAG_BYTES = 64 * 1024


def folgen_zeilen(f: dict, kappen: int | None = None) -> list[str]:
    """Die Folgen eines Löschens als Text, für Befehl und Dashboard gleich. kappen: höchstens so viele Zeilen je Art."""
    zeilen: list[str] = []

    def art(titel: str, eintraege: list[str]) -> None:
        if eintraege:
            zeilen.append(f"{titel} ({len(eintraege)}):")
            zeigen = eintraege if kappen is None else eintraege[:kappen]
            zeilen.extend(f"  {e}" for e in zeigen)
            if len(zeigen) < len(eintraege):
                zeilen.append(f"  … und {len(eintraege) - len(zeigen)} weitere")

    art("Gesperrt", f["sperren"])
    art("Belege, die brechen (Lint source_missing, Fehler)",
        [f"{b['seite']}: sources-Eintrag {b['id'] or b['resource']}, Fußnoten im Text: {b['fussnoten']}" for b in f["belege"]])
    art("Fußnoten, deren Link ins Leere zeigt (Lint broken_link, Warnung)",
        [f"{v['seite']}: [^{v['fussnote']}] → {v['ziel']}" for v in f["fussnoten"]])
    art("Links, die ins Leere zeigen (Lint broken_link, Warnung)", [f"{v['seite']}: {v['ziel']}" for v in f["links"]])
    art("Als Nachfolger genannt (Lint superseded_missing, Warnung)", f["nachfolger"])
    art("Symlinks auf diese Datei", f["symlinks"])
    art("Nicht geprüft, Frontmatter unlesbar", f["unlesbar"])
    if not (f["belege"] or f["fussnoten"] or f["links"] or f["nachfolger"] or f["symlinks"]):
        zeilen.append("Keine andere Karte verweist auf diese.")
    if f["rohquelle"]:
        zeilen.append(f"Rohquelle: {f['rohquelle']} bleibt liegen, außer du nimmst sie ausdrücklich mit "
                      "(ohne Karte meldet der Lint raw_uningested).")
        art("Dieselbe Rohquelle nutzen auch oder lassen sich nicht prüfen, darum bleibt sie liegen", f["rohquelle_geteilt"])
    if f["rohquelle_hinweis"]:
        zeilen.append(f["rohquelle_hinweis"])
    return zeilen


class WikiAblage:
    def _karte(self, page) -> Page:
        """Nur was pages() als Seite kennt: kein index.md oder log.md, nichts Verstecktes, nichts außerhalb des Bundles."""
        pid = str(page).removeprefix("/").removesuffix(".md")
        for p in self.pages():
            if p.id == pid:
                return p
        raise WikiError(f"Seite fehlt: {pid}")

    def _geschuetzt(self, pfad: Path) -> str | None:
        rel = self.rel(pfad).casefold()
        for muster in self.cfg["paths"].get("protected", []):
            if fnmatch.fnmatchcase(rel, str(muster).casefold()):
                return str(muster)
        return None

    def _rohquelle_von(self, p: Page) -> tuple[Path | None, str]:
        """Das Paket einer Karte (raw_packet, sonst <raw>/<slug> bei Quellenkarten), nur direkt unter <raw> und kein
        Symlink. Der Text sagt, warum ein genanntes Paket nicht in Frage kommt: Es stammt womöglich aus einem fremden Wiki."""
        if self.raw is None or p.fm is None:
            return None, ""
        feld = p.fm.get("raw_packet")
        if feld:
            paket = self._im_projekt(str(feld).rstrip("/"))
        elif p.type == SOURCE_TYPE:
            paket = self.raw / p.path.stem
        else:
            return None, ""
        if paket is not None and not os.path.lexists(paket):
            return None, ""
        if paket is None or paket.is_symlink() or not paket.is_dir() or paket.resolve().parent != self.raw.resolve():
            return None, f"raw_packet {feld or p.path.stem} liegt nicht als Ordner direkt unter {self.rel(self.raw)}: bleibt unangetastet."
        return paket, ""

    def folgen(self, page) -> dict:
        """Alles, was am Löschen einer Karte hängt. Die Verweise liest es wie der Lint: Links ohne Codeblöcke und
        sources[].resource, aufgelöst mit resolve()."""
        p = self._karte(page)
        ziel = p.path.resolve()
        paket, hinweis = self._rohquelle_von(p)
        f = {"seite": p.id, "titel": p.title, "status": (p.fm or {}).get("status"), "pfad": self.rel(p.path), "sperren": [],
             "belege": [], "fussnoten": [], "links": [], "nachfolger": [], "symlinks": [], "unlesbar": [],
             "rohquelle": self.rel(paket) if paket else None, "rohquelle_geteilt": [], "rohquelle_hinweis": hinweis}
        if p.path.is_symlink():
            f["sperren"].append("Die Karte ist ein symbolischer Link; bitte von Hand auflösen.")
        if p.fm is not None and f["status"] != "deprecated":
            f["sperren"].append(f"status ist {f['status'] or 'nicht gesetzt'}: erst ablösen (status deprecated), dann löschen.")
        muster = self._geschuetzt(p.path)
        if muster:
            f["sperren"].append(f"Die Karte ist laut wiki.toml geschützt ({muster}).")
        for q in self.pages():
            if q is p:
                continue
            if q.path.resolve() == ziel:
                f["symlinks"].append(q.id)
                continue
            if q.fm is None:
                f["unlesbar"].append(q.id)
            text = CODE_RE.sub("", q.body)
            for zeile in text.splitlines():
                definition = FOOTNOTE_DEF_RE.match(zeile)
                for t in LINK_RE.findall(zeile):
                    if self.resolve(q.path, t) != ziel:
                        continue
                    if definition:
                        f["fussnoten"].append({"seite": q.id, "fussnote": definition.group(1), "ziel": t})
                    else:
                        f["links"].append({"seite": q.id, "ziel": t})
            fm = q.fm or {}
            verweise = FOOTNOTE_REF_RE.findall(text)
            for s in as_list(fm.get("sources")):
                if isinstance(s, dict) and s.get("resource") and self.resolve(q.path, str(s["resource"])) == ziel:
                    f["belege"].append({"seite": q.id, "id": s.get("id"), "resource": str(s["resource"]),
                                        "fussnoten": verweise.count(str(s.get("id")))})
            if fm.get("superseded_by") and self.resolve(q.path, str(fm["superseded_by"])) == ziel:
                f["nachfolger"].append(q.id)
            if paket and (q.fm is None or paket == self._rohquelle_von(q)[0]):   # unlesbar: raw_packet nicht prüfbar
                f["rohquelle_geteilt"].append(q.id)
        return f

    def deprecate_page(self, page, by: str, nachfolger: str | None = None, note: str = "") -> dict:
        """Erste Stufe: status deprecated, auf Wunsch mit superseded_by. Das darf wie verify --status deprecated jeder Akteur."""
        if not isinstance(by, str) or not ACTOR_RE.fullmatch(by):
            raise WikiError("--by muss der Akteurskonvention folgen, z. B. human:<id> (OKF §7)")
        p = self._karte(page)
        self._kein_symlink(p.path)
        if p.fm is None:
            raise WikiError(f"{p.id}: Frontmatter nicht lesbar: {p.error}")
        updates = {"status": "deprecated"}
        if nachfolger:
            neu = self._karte(nachfolger)
            if neu.id == p.id:
                raise WikiError(f"{p.id} kann nicht sein eigener Nachfolger sein")
            updates["superseded_by"] = f"/{neu.id}.md"
        elif p.fm.get("status") == "deprecated":
            raise WikiError(f"{p.id} ist schon abgelöst; zum Löschen: delete")
        self._write_frontmatter(p.path, updates)
        detail = " ".join(x for x in (f"Nachfolger {updates['superseded_by']}." if nachfolger else "", note.strip()) if x)
        self.append_event("deprecate", p.id, detail, by=by)
        return {"seite": p.id, "nachfolger": updates.get("superseded_by")}

    def _papierkorb(self, anlegen: bool = False) -> Path:
        korb = self.bundle / PAPIERKORB
        if korb.is_symlink() or (os.path.lexists(korb) and not korb.is_dir()):
            raise WikiError(f"{self.rel(korb)} ist kein Ordner im Bundle (Symlink?): nichts verschoben")
        if anlegen:
            korb.mkdir(exist_ok=True)
            # Was gelöscht ist, soll nicht mit dem nächsten Commit wieder ins Repo; der Ereignisstrom bleibt die Spur
            if not os.path.lexists(korb / ".gitignore"):
                (korb / ".gitignore").write_text("*\n", encoding="utf-8")
        return korb

    def _umziehen(self, zuege: list[tuple[Path, Path]], ereignis) -> None:
        """Alle Züge samt Ereignis oder keiner: Ohne Spur im Ereignisstrom bleibt nichts verschoben."""
        fertig: list[tuple[Path, Path]] = []
        try:
            for von, nach in zuege:
                nach.parent.mkdir(parents=True, exist_ok=True)
                os.replace(von, nach)
                fertig.append((von, nach))
            ereignis()
        except (OSError, WikiError) as exc:
            haengt = []
            for von, nach in reversed(fertig):
                try:
                    os.replace(nach, von)
                except OSError:
                    haengt.append(f"{self.rel(nach)} gehört nach {self.rel(von)}")
            if haengt:
                raise WikiError(f"{exc}. Zurückverschieben gescheitert, bitte von Hand: {'; '.join(haengt)}") from exc
            raise WikiError(f"Nichts verschoben: {exc}") from exc
        finally:
            self._pages = None

    def delete_page(self, page, by: str, belege_brechen: bool = False, mit_rohquelle: bool = False) -> dict:
        """Zweite Stufe: Karte, auf eigenen Wunsch auch ihre Rohquelle, in den Papierkorb. Ereignis delete."""
        if not isinstance(by, str) or not by.startswith("human:") or not ACTOR_RE.fullmatch(by):
            raise WikiError("Löschen entscheidet ein Mensch: --by human:<id>")
        f = self.folgen(page)
        if f["sperren"]:
            raise WikiError(f"{f['seite']}: " + " ".join(f["sperren"]))
        if f["belege"] and not belege_brechen:
            raise WikiError(f"{f['seite']} ist Beleg auf {', '.join(sorted({b['seite'] for b in f['belege']}))}; "
                            "der Lint meldete danach source_missing. Erst dort ersetzen oder ausdrücklich --belege-brechen.")
        paket = None
        if mit_rohquelle:
            if not f["rohquelle"]:
                raise WikiError(f"{f['seite']} hat keine Rohquelle, die sich mitnehmen ließe. {f['rohquelle_hinweis']}".strip())
            if f["rohquelle_geteilt"]:
                raise WikiError(f"{f['rohquelle']} nutzen auch oder lassen sich nicht prüfen: "
                            f"{', '.join(f['rohquelle_geteilt'])}. Sie bleibt liegen")
            paket = self.root / f["rohquelle"]
        p = self._karte(page)
        korb = self._papierkorb(anlegen=True)
        name = now_iso().replace("-", "").replace(":", "") + "-" + re.sub(r"[^A-Za-z0-9_-]+", "-", p.id)[:80]
        eintrag, nummer = korb / name, 1
        while True:
            try:
                eintrag.mkdir()
                break
            except FileExistsError:
                nummer += 1
                eintrag = korb / f"{name}-{nummer}"
        gebrochen = sorted({b["seite"] for b in f["belege"]})
        daten = {"eintrag": eintrag.name, "seite": p.id, "titel": p.title, "status": f["status"], "by": by, "at": now_iso(),
                 "sha256": sha256_of(p.path), "rohquelle": paket.name if paket else None, "belege_gebrochen": gebrochen}
        (eintrag / EINTRAG_DATEI).write_text(json.dumps(daten, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        detail = f"In den Papierkorb: {eintrag.name}."
        if gebrochen:
            detail += f" Belege gebrochen, ausdrücklich bestätigt: {', '.join(gebrochen)}."
        detail += f" Rohquelle mit: {f['rohquelle']}." if paket else (f" Rohquelle bleibt: {f['rohquelle']}." if f["rohquelle"] else "")
        zuege = ([(paket, eintrag / ROH_ORDNER)] if paket else []) + [(p.path, eintrag / KARTE_DATEI)]
        try:
            self._umziehen(zuege, lambda: self.append_event("delete", p.id, detail, by=by))
        except WikiError:
            # Hängt etwas im Eintrag fest, braucht restore seine Angaben
            if not any(os.path.lexists(eintrag / n) for n in (KARTE_DATEI, ROH_ORDNER)):
                (eintrag / EINTRAG_DATEI).unlink(missing_ok=True)
                eintrag.rmdir()
            raise
        self._leere_indexe_weg(p.path.parent)
        return daten

    def _leere_indexe_weg(self, ordner: Path) -> None:
        """rebuild schreibt index.md nur für Ordner mit Seiten: Nach der letzten Karte bliebe sonst ein Index, der auf sie
        zeigt. Das ist eine Projektion, restore und rebuild erzeugen sie wieder."""
        while ordner != self.bundle and ordner.is_relative_to(self.bundle):
            if any(q.path.is_relative_to(ordner) for q in self.pages()):
                break
            (ordner / "index.md").unlink(missing_ok=True)
            ordner = ordner.parent

    def _eintrag_lesen(self, ordner: Path) -> dict:
        datei = ordner / EINTRAG_DATEI
        if datei.is_symlink() or not datei.is_file() or datei.stat().st_size > MAX_EINTRAG_BYTES:
            raise WikiError(f"{ordner.name}: {EINTRAG_DATEI} fehlt oder ist keine kleine Datei")
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
        except (ValueError, RecursionError) as exc:
            raise WikiError(f"{ordner.name}: {EINTRAG_DATEI} nicht lesbar") from exc
        if not isinstance(daten, dict) or not isinstance(daten.get("seite"), str):
            raise WikiError(f"{ordner.name}: {EINTRAG_DATEI} nennt keine Seite")
        return daten

    def papierkorb(self) -> list[dict]:
        """Einträge, die neuesten zuerst. Die Felder kommen aus dem Bundle, also womöglich aus einem fremden Wiki."""
        korb = self._papierkorb()
        if not korb.is_dir():
            return []
        out = []
        for ordner in sorted(korb.iterdir(), key=lambda d: d.name, reverse=True):
            if not EINTRAG_RE.fullmatch(ordner.name) or ordner.is_symlink() or not ordner.is_dir():
                continue
            try:
                daten = self._eintrag_lesen(ordner)
                out.append({k: daten.get(k) for k in ("seite", "titel", "by", "at", "rohquelle")} | {"eintrag": ordner.name})
            except WikiError as exc:
                out.append({"eintrag": ordner.name, "fehler": str(exc)})
        return out

    def restore_page(self, eintrag: str, by: str) -> dict:
        """Holt einen Eintrag zurück. Der Eintrag gilt als fremd: Sein Ziel muss wieder eine Seite im Bundle ergeben,
        frei sein, und nichts darin darf ein Symlink sein."""
        if not isinstance(by, str) or not ACTOR_RE.fullmatch(by):
            raise WikiError("--by muss der Akteurskonvention folgen, z. B. human:<id> (OKF §7)")
        if not isinstance(eintrag, str) or not EINTRAG_RE.fullmatch(eintrag):
            raise WikiError(f"{eintrag!r} ist kein Eintrag im Papierkorb")
        ordner = self._papierkorb() / eintrag
        if ordner.is_symlink() or not ordner.is_dir():
            raise WikiError(f"{eintrag} ist kein Eintrag im Papierkorb")
        daten = self._eintrag_lesen(ordner)
        pid = daten["seite"]
        ziel = self.bundle / f"{pid}.md"
        if pid.startswith("/") or "\\" in pid or ".." in Path(pid).parts \
                or not ziel.resolve().is_relative_to(self.bundle.resolve()) or self._hidden(ziel) or ziel.name in RESERVED:
            raise WikiError(f"{eintrag}: Ziel {pid!r} ist keine Seite in diesem Bundle")
        muster = self._geschuetzt(ziel)
        if muster:
            raise WikiError(f"{eintrag}: Ziel {self.rel(ziel)} ist laut wiki.toml geschützt ({muster})")
        karte = ordner / KARTE_DATEI
        if karte.is_symlink() or not karte.is_file():
            raise WikiError(f"{eintrag}: {KARTE_DATEI} fehlt oder ist ein Symlink")
        if os.path.lexists(ziel):
            raise WikiError(f"{self.rel(ziel)} existiert schon: nichts zurückgeholt")
        zuege = [(karte, ziel)]
        name = daten.get("rohquelle")
        if name:
            paket = ordner / ROH_ORDNER
            if self.raw is None or not isinstance(name, str) or not PAKET_RE.fullmatch(name):
                raise WikiError(f"{eintrag}: Rohquelle {name!r} hat keinen Platz in diesem Projekt")
            if paket.is_symlink() or not paket.is_dir() or any(x.is_symlink() for x in paket.rglob("*")):
                raise WikiError(f"{eintrag}: Rohquelle fehlt oder enthält Symlinks")
            if os.path.lexists(self.raw / name):
                raise WikiError(f"{self.rel(self.raw / name)} existiert schon: nichts zurückgeholt")
            zuege.append((paket, self.raw / name))
        anders = sha256_of(karte) != daten.get("sha256")
        detail = f"Aus dem Papierkorb: {eintrag}." + (" Weicht vom Stand beim Löschen ab." if anders else "")
        self._umziehen(zuege, lambda: self.append_event("restore", pid, detail, by=by))
        (ordner / EINTRAG_DATEI).unlink(missing_ok=True)
        try:
            ordner.rmdir()
        except OSError:
            pass   # Fremdes im Eintrag bleibt liegen; Löschen entscheidet ein Mensch
        return {"seite": pid, "rohquelle": name or None, "abweichend": anders}
