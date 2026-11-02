"""Pfad: Stationen aus dem Körper einer Karte lesen und wieder schreiben, die Lage auf dem Raster ergänzen.

Eine Überschrift erster Ebene beginnt eine Station. Ein Listenpunkt, der nur aus einem Link auf eine `.md` besteht, setzt
diese Karte auf die Station, `> [!notiz]` ist eine Sprechernotiz, alles andere wird zum Textfeld. Eine Zeile `---` trennt
zwei Textfelder. Text vor der ersten Überschrift ist eine Station ohne eigene Überschrift (Vorspann)."""

import re
from itertools import groupby

PFAD_TYPE = "pfad"
PFAD_ORDNER = "pfade"
RASTER = 12
ZEILEN = 7   # Höhe einer Station im Raster, solange nichts tiefer liegt
ZEIGT = ("kurz", "absatz", "ganz")

_UEBERSCHRIFT = re.compile(r"^ {0,3}# +(.*?)(?: +#+)? *$")
_KARTE = re.compile(r"^ {0,3}[-*+] +\[([^\[\]]*)\]\(([^()\s]+)\) *$")
_NOTIZ = re.compile(r"^ {0,3}> ?\[!notiz\] *(.*)$", re.I)
_ZITAT = re.compile(r"^ {0,3}> ?(.*)$")
_FUSSNOTE = re.compile(r"^\[\^[^\]\s]+\]:")
_ZAUN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TRENNER = re.compile(r"^ {0,3}-{3,} *$")
_SCHEMA = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def ist_kartenziel(ziel: str) -> bool:
    return not _SCHEMA.match(ziel) and ziel.split("#", 1)[0].endswith(".md")


def station_slug(titel: str, vergeben: set[str]) -> str:
    """Schlüssel der Station in `lage`: aus dem Titel, im Pfad eindeutig."""
    basis = re.sub(r"[^\w]+", "-", titel.lower()).strip("-") or "station"
    slug, n = basis, 2
    while slug in vergeben:
        slug, n = f"{basis}-{n}", n + 1
    vergeben.add(slug)
    return slug


def stationen_lesen(body: str) -> tuple[list[dict], list[str]]:
    """Stationen des Körpers und die Fußnotendefinitionen, die allen Textfeldern gehören."""
    stationen: list[dict] = []
    fussnoten: list[str] = []
    puffer: list[str] = []
    vergeben: set[str] = set()

    def neu(titel: str, vorspann: bool = False) -> dict:
        s = {"titel": titel, "vorspann": vorspann, "slug": station_slug("vorspann" if vorspann else titel, vergeben),
             "elemente": [], "notizen": []}
        stationen.append(s)
        return s

    def aktuell() -> dict:
        return stationen[-1] if stationen else neu("", vorspann=True)

    def text_fertig() -> None:
        text = "\n".join(puffer).strip("\n").rstrip()
        puffer.clear()
        if text.strip():
            s = aktuell()
            nr = sum(e["art"] == "text" for e in s["elemente"]) + 1
            s["elemente"].append({"art": "text", "schluessel": f"text-{nr}", "text": text})

    zeilen = body.split("\n")
    i, zaun = 0, ""
    while i < len(zeilen):
        z = zeilen[i]
        i += 1
        m = _ZAUN.match(z)
        if zaun:
            puffer.append(z)
            if m and m.group(1)[0] == zaun[0] and len(m.group(1)) >= len(zaun):
                zaun = ""
            continue
        if m:
            zaun = m.group(1)
            puffer.append(z)
        elif m := _UEBERSCHRIFT.match(z):
            text_fertig()
            neu(m.group(1).strip())
        elif (m := _KARTE.match(z)) and ist_kartenziel(m.group(2)):
            text_fertig()
            aktuell()["elemente"].append({"art": "karte", "name": m.group(1).strip(), "ziel": m.group(2),
                                          "schluessel": m.group(2)})
        elif m := _NOTIZ.match(z):
            text_fertig()
            notiz = [m.group(1)]
            while i < len(zeilen) and (zitat := _ZITAT.match(zeilen[i])):
                notiz.append(zitat.group(1))
                i += 1
            aktuell()["notizen"].append("\n".join(notiz).strip())
        elif _FUSSNOTE.match(z):
            fussnote = [z]
            while i < len(zeilen) and zeilen[i].startswith(("    ", "\t")):
                fussnote.append(zeilen[i])
                i += 1
            fussnoten.append("\n".join(fussnote))
        elif _TRENNER.match(z):
            text_fertig()
        else:
            puffer.append(z)
    text_fertig()
    return stationen, fussnoten


def fussnoten_heraus(text: str) -> tuple[str, list[str]]:
    """Trennt Fußnotendefinitionen aus einem Textfeld ab: Im Körper gehören sie allen Textfeldern und stehen am Ende."""
    rest: list[str] = []
    fussnoten: list[str] = []
    zeilen = text.split("\n")
    i, zaun = 0, ""
    while i < len(zeilen):
        z = zeilen[i]
        i += 1
        m = _ZAUN.match(z)
        if zaun or m:
            zaun = "" if zaun and m and m.group(1)[0] == zaun[0] and len(m.group(1)) >= len(zaun) else zaun or m.group(1)
            rest.append(z)
        elif _FUSSNOTE.match(z):
            fussnote = [z]
            while i < len(zeilen) and zeilen[i].startswith(("    ", "\t")):
                fussnote.append(zeilen[i])
                i += 1
            fussnoten.append("\n".join(fussnote))
        else:
            rest.append(z)
    return "\n".join(rest).strip("\n").rstrip(), fussnoten


def stationen_schreiben(stationen: list[dict], fussnoten=()) -> str:
    """Der Körper zu den Stationen; stationen_lesen liest daraus wieder dasselbe."""
    teile = []
    for s in stationen:
        bloecke = [] if s.get("vorspann") else [f"# {s['titel']}"]
        karten: list[str] = []
        vorher = ""
        for e in s["elemente"]:
            if e["art"] == "karte":
                karten.append(f"- [{re.sub(r'[][]', '', e.get('name') or '')}]({e['ziel']})")
                vorher = "karte"
                continue
            if karten:
                bloecke.append("\n".join(karten))
                karten = []
            if vorher == "text":
                bloecke.append("---")
            bloecke.append(e["text"].strip("\n"))
            vorher = "text"
        if karten:
            bloecke.append("\n".join(karten))
        for notiz in s["notizen"]:
            bloecke.append("> [!notiz]\n" + "\n".join(f"> {z}".rstrip() for z in notiz.split("\n")))
        teile.append("\n\n".join(bloecke))
    if fussnoten:
        teile.append("\n".join(fussnoten))
    return "\n\n".join(t for t in teile if t) + "\n"


def stationen_kern(stationen: list[dict], schluessel: bool = False) -> list:
    """Was der Körper festlegt, ohne Lage und Anzeige: für den Vergleich, ob sich der Text geändert hat. Mit schluessel
    zählt die Karte, nicht die Schreibweise ihres Links."""
    wert = lambda e: e["schluessel"] if schluessel and e["art"] == "karte" else e.get("ziel") or e.get("text")
    return [(s["titel"], bool(s.get("vorspann")), [(e["art"], wert(e)) for e in s["elemente"]], list(s["notizen"]))
            for s in stationen]


def pos_gueltig(pos, raster: int = RASTER) -> bool:
    return (isinstance(pos, (list, tuple)) and len(pos) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in pos)
            and pos[0] >= 0 and pos[1] >= 0 and pos[2] >= 1 and pos[3] >= 1
            and pos[0] + pos[2] <= raster and pos[1] + pos[3] <= 200)


def anordnen(elemente: list[dict], raster: int = RASTER) -> int:
    """Gibt jedem Element ohne `pos` eine Lage unterhalb der gesetzten, in der Reihenfolge der Station: Karten nebeneinander,
    Text in voller Breite. Karten, die nur Titel und Beschreibung zeigen, bleiben niedrig. Gibt die Zeilenzahl der Station zurück."""
    tiefe = max((e["pos"][1] + e["pos"][3] for e in elemente if e.get("pos")), default=0)
    for art, gruppe in groupby((e for e in elemente if not e.get("pos")), key=lambda e: e["art"]):
        gruppe = list(gruppe)
        if art == "karte":
            je_zeile = min({1: 1, 2: 2, 3: 3, 4: 2}.get(len(gruppe), 3), raster)
            breite = raster // je_zeile
            kurz = all(e.get("zeigt", "kurz") == "kurz" for e in gruppe)
            hoehe = 3 if kurz else 5 if len(gruppe) <= 3 else 4
            for i, e in enumerate(gruppe):
                e["pos"], e["auto"] = [(i % je_zeile) * breite, tiefe + (i // je_zeile) * hoehe, breite, hoehe], True
            tiefe += (len(gruppe) + je_zeile - 1) // je_zeile * hoehe
        else:
            for e in gruppe:
                hoehe = min(ZEILEN, max(2, 1 + len(e.get("text") or "") // 180))
                e["pos"], e["auto"] = [0, tiefe, raster, hoehe], True
                tiefe += hoehe
    return max(ZEILEN, max((e["pos"][1] + e["pos"][3] for e in elemente), default=0))


def _ueberlappen(a: list[int], b: list[int]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def lage_anwenden(stationen: list[dict], lage, raster: int = RASTER) -> list[str]:
    """Trägt `pos` und `zeigt` aus `lage` ein und ordnet den Rest an. Gibt zurück, was in `lage` nichts mehr trifft oder
    ungültig ist; das betroffene Element wird dann angeordnet, als stünde dort nichts."""
    meldungen = []
    lage = lage if isinstance(lage, dict) else {}
    for s in stationen:
        eintrag = lage.get(s["slug"])
        eintrag = eintrag if isinstance(eintrag, dict) else {}
        schluessel = {e["schluessel"] for e in s["elemente"]}
        meldungen += [f"Station {s['slug']}: {k} steht nicht auf der Station" for k in eintrag if k not in schluessel]
        for e in s["elemente"]:
            werte = eintrag.get(e["schluessel"])
            if not isinstance(werte, dict):
                continue
            if "pos" in werte:
                if pos_gueltig(werte["pos"], raster):
                    e["pos"] = list(werte["pos"])
                else:
                    meldungen.append(f"Station {s['slug']}: {e['schluessel']} hat keine gültige Lage: {werte['pos']!r}")
            if e["art"] == "karte" and "zeigt" in werte:
                if werte["zeigt"] in ZEIGT:
                    e["zeigt"] = werte["zeigt"]
                else:
                    meldungen.append(f"Station {s['slug']}: {e['schluessel']} zeigt {werte['zeigt']!r}, erlaubt: "
                                     + ", ".join(ZEIGT))
        s["zeilen"] = anordnen(s["elemente"], raster)
        for i, a in enumerate(s["elemente"]):
            for b in s["elemente"][i + 1:]:
                if _ueberlappen(a["pos"], b["pos"]):
                    meldungen.append(f"Station {s['slug']}: {a['schluessel']} und {b['schluessel']} liegen übereinander")
    meldungen += [f"Station {k} gibt es nicht (umbenannt?): ihre Lage wirkt nicht"
                  for k in lage if k not in {s["slug"] for s in stationen}]
    return meldungen


def lage_aus(stationen: list[dict]) -> dict:
    """`lage` fürs Frontmatter: nur was gesetzt ist, nicht was okfw selbst angeordnet hat."""
    lage = {}
    for s in stationen:
        eintrag = {}
        for e in s["elemente"]:
            werte = {}
            if e.get("pos") and not e.get("auto"):
                werte["pos"] = list(e["pos"])
            if e["art"] == "karte" and e.get("zeigt", "kurz") != "kurz":
                werte["zeigt"] = e["zeigt"]
            if werte:
                eintrag[e["schluessel"]] = werte
        if eintrag:
            lage[s["slug"]] = eintrag
    return lage
