"""Bereinigung fremder Inhalte fürs Dashboard (sicherheitskritisch): SVG nach Positivliste, CSS, Links und HTML-Einträge. Nur Standardbibliothek, ohne Wiki."""

import html
import re
from html.parser import HTMLParser

from .basis import FigureError


FIGURE_MAX_BYTES = 200 * 1024
FIGURE_MAX_ELEMENTS = 5000
FIGURE_MAX_EXPANSION = 20000   # Elemente nach dem Auflösen aller <use>: verschachteltes <use> wächst sonst exponentiell
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
                "title", "desc", "defs", "linearGradient", "radialGradient", "stop", "marker", "clipPath", "use"}
SVG_TEXT_ELEMENTS = {"text", "tspan", "title", "desc"}
# Geometrie und Präsentation; kein class, kein on*, keine Animation. href nur auf use und Verläufen, nur als #…
SVG_ATTRIBUTES = set("""
    x y width height cx cy r rx ry x1 y1 x2 y2 fx fy fr points d viewBox preserveAspectRatio transform id
    offset gradientUnits gradientTransform spreadMethod clipPathUnits markerWidth markerHeight markerUnits refX refY
    orient dx dy rotate textLength lengthAdjust
    fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
    stroke-dasharray stroke-dashoffset opacity color stop-color stop-opacity clip-path clip-rule marker-start
    marker-mid marker-end font-family font-size font-weight font-style font-variant text-anchor text-decoration
    dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing writing-mode visibility display
    style role aria-label aria-hidden href
""".split())
SVG_HREF_ELEMENTS = {"use", "linearGradient", "radialGradient"}
# style nur mit dem, was eine Abbildung zeichnet: nichts, was sie aus ihrem Rahmen hebt (position, z-index, inset, width …)
SVG_STYLE_PROPERTIES = set("""
    fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
    stroke-dasharray stroke-dashoffset paint-order vector-effect opacity color stop-color stop-opacity clip-path
    clip-rule marker-start marker-mid marker-end font font-family font-size font-weight font-style font-variant
    font-stretch text-anchor text-decoration dominant-baseline alignment-baseline baseline-shift letter-spacing
    word-spacing writing-mode text-rendering shape-rendering visibility display marker
""".split())
SVG_ID_RE = re.compile(r"[A-Za-z_][\w.-]*")
SVG_URL_RE = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.I)
SVG_VIEWBOX_RE = re.compile(r"\s*-?[\d.]+(?:[\s,]+-?[\d.]+){3}\s*")
# width und height, aus denen sich ein viewBox ableiten lässt: absolute CSS-Einheiten, umgerechnet in Nutzereinheiten (px)
SVG_LENGTH_RE = re.compile(r"\s*(\d+(?:\.\d*)?|\.\d+)(px|pt|pc|mm|cm|in|q)?\s*", re.I)
SVG_UNITS = {"": 1, "px": 1, "pt": 4 / 3, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96, "q": 96 / 101.6}
# Der Kopf, den Zeichenprogramme schreiben (nur diese Paare, ohne internes Subset): wird entfernt, nicht gelesen, nichts lädt die DTD
SVG_DOCTYPES = {"-//W3C//DTD SVG 1.0//EN": "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd",
                "-//W3C//DTD SVG 20010904//EN": "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd",
                "-//W3C//DTD SVG 1.1//EN": "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd",
                "-//W3C//DTD SVG 1.1 Basic//EN": "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11-basic.dtd",
                "-//W3C//DTD SVG 1.1 Tiny//EN": "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11-tiny.dtd"}
SVG_PROLOG_RE = re.compile(r"\ufeff?\s*(?:<\?xml\s(?:(?!\?>).)*\?>\s*)?(?:<!--(?:(?!-->).)*-->\s*)*", re.S)
SVG_DOCTYPE_RE = re.compile(r"""<!DOCTYPE\s+svg\s+PUBLIC\s+(["'])([^"']*)\1\s+(["'])([^"']*)\3\s*>""")
# <style>-Regeln, die sich auf einzelne Elemente übertragen lassen: Element, .klasse, Element.klasse, #id, *
SVG_SELECTOR_RE = re.compile(r"([A-Za-z][\w-]*|\*)?(\.[\w-]+|#[\w.-]+)?")
# Was beim Zeichnen nichts ändert (Angaben der Werkzeuge). Alles andere meldet der Dialog als sichtbaren Verlust.
SVG_ATTRIBUTES_OHNE_WIRKUNG = set("""
    version baseProfile pointer-events content enable-background contentScriptType contentStyleType zoomAndPan
    tabindex focusable overflow color-interpolation color-rendering image-rendering
    requiredFeatures requiredExtensions systemLanguage
""".split())
SVG_STYLE_OHNE_WIRKUNG = set("""
    enable-background isolation mix-blend-mode overflow line-height text-align text-indent text-transform
    block-progression solid-color solid-opacity color-interpolation color-interpolation-filters color-rendering
    image-rendering shape-padding font-variation-settings font-feature-settings font-variant-ligatures
    font-variant-position font-variant-caps font-variant-numeric font-variant-alternates font-variant-east-asian
    text-orientation text-decoration-line text-decoration-style text-decoration-color white-space pointer-events
""".split())
# CSS-Funktionen, die Fremdes laden können, auch ohne url(
CSS_LOADER_RE = re.compile(r"image-set\(|(?<![\w-])(?:src|image|cross-fade|element|paint)\(", re.I)
# SVG kommt nur durch clean_svg auf die Seite, nicht als data: im Design
CSS_DATA_RE = re.compile(r"data:(?:font/(?:woff2?|ttf|otf|sfnt|collection)"
                         r"|application/(?:x-)?font-(?:woff2?|ttf|otf|sfnt)"
                         r"|image/(?:png|jpeg|gif|webp|avif))[;,]", re.I)


def svg_value_ok(value: str, style: bool = False) -> bool:
    """Ein Attributwert, der nichts nachlädt und nichts ausführt: url(…) nur auf #id, keine Skript-Adressen, keine
    CSS-Maskierung und kein CSS-Kommentar, die eine Adresse verstecken könnten."""
    lower = value.lower()
    if "\\" in value or "/*" in value or CSS_LOADER_RE.search(value):
        return False
    if any(not m.group(2).strip().startswith("#") for m in SVG_URL_RE.finditer(value)):
        return False
    if lower.count("url(") != len(SVG_URL_RE.findall(value)):
        return False
    if re.search(r"javascript:|(?<!\w)data:|vbscript:|expression\(|behavior\s*:|binding\s*:|[<>]", lower):
        return False
    return not (style and "@" in value)


def svg_style(value: str) -> tuple[str, list[str]]:
    """style auf die erlaubten Eigenschaften gekürzt: (neuer Wert, weggefallene Eigenschaften)."""
    keep, dropped = [], []
    for declaration in value.split(";"):
        name, colon, val = declaration.partition(":")
        name = name.strip().lower()
        if colon and name in SVG_STYLE_PROPERTIES:
            keep.append(f"{name}:{val.strip()}")
        elif declaration.strip():
            dropped.append(name)
    return ";".join(keep), dropped


def svg_style_rules(css: str) -> tuple[dict, set[str]]:
    """<style>-Regeln als {(Element oder *, .klasse oder #id oder ""): [(Gewicht, Reihenfolge, Deklarationen)]} und was
    entfiel. Aus dem Block kommen nur Deklarationen auf die Seite, jede einzeln durch svg_value_ok und svg_style;
    Selektoren dienen allein dem Nachschlagen. Was anders gelesen wird als im Browser, sieht darum höchstens anders aus."""
    css = re.sub(r"/\*.*?(?:\*/|\Z)", "", css, flags=re.S)   # ohne Lücke: `ur/**/l(` fällt so in svg_value_ok auf
    rules: dict = {}
    dropped: set[str] = set()
    skipped: list[str] = []
    pos, order = 0, 0
    while m := re.compile(r"[;{}]").search(css, pos):
        prelude = " ".join(css[pos:m.start()].split())
        pos = m.end()
        if m.group() != "{":
            if prelude:
                skipped.append(prelude)
            continue
        depth = 1
        while depth and (brace := re.compile(r"[{}]").search(css, pos)):
            depth += 1 if brace.group() == "{" else -1
            pos = brace.end()
        body = css[m.end():pos - 1 if not depth else len(css)]
        if depth:
            pos = len(css)
        selectors = []
        for selector in prelude.split(","):
            found = SVG_SELECTOR_RE.fullmatch(selector.strip())
            if selector.strip() and found:
                selectors.append((found.group(1) or "*", found.group(2) or ""))
            else:
                skipped.append(selector.strip() or prelude)
        if not selectors:
            continue
        declarations = []
        for declaration in body.split(";"):
            if svg_value_ok(declaration, style=True):
                keep, gone = svg_style(declaration)
            else:
                keep, gone = "", [declaration.partition(":")[0].strip().lower()]
            if keep:
                declarations.append(keep)
            dropped.update(f"Stil {g[:40]}" for g in gone)
        for tag, qualifier in selectors if declarations else ():
            weight = (qualifier.startswith("#"), qualifier.startswith("."), tag != "*")
            order += 1
            rules.setdefault((tag, qualifier), []).append((weight, order, ";".join(declarations)))
    if skipped:
        beispiele = ", ".join(x[:40] for x in skipped[:3])
        dropped.add(f"Stilregeln nicht übertragen ({len(skipped)}): {beispiele}")
    return rules, dropped


def svg_prefixed(value: str, prefix: str) -> str:
    return SVG_URL_RE.sub(lambda m: f"url(#{prefix}{m.group(2).strip()[1:]})", value)


def svg_expansion_check(root) -> None:
    """Zählt die Elemente der Abbildung nach dem Auflösen aller <use> über den Referenzgraphen (je Teilbaum einmal
    berechnet, jedes <use> zählt den ganzen Zielbaum mit) und lehnt Zyklen sowie mehr als FIGURE_MAX_EXPANSION ab."""
    ids: dict[str, object] = {}
    for el in root.iter():
        if el.get("id") and el.get("id") not in ids:
            ids[el.get("id")] = el   # bei doppelter id gilt die erste, wie im Browser
    sizes: dict[int, int] = {}
    stack: set[int] = set()

    def size(el) -> int:
        key = id(el)
        if key in sizes:
            return sizes[key]
        if key in stack:
            raise FigureError("use verweist auf sich selbst (Zyklus)")
        stack.add(key)
        n = 1 + sum(size(child) for child in el)
        href = el.get("href") if el.tag.rpartition("}")[2] == "use" else None
        target = ids.get(href[1:]) if href and href.startswith("#") else None
        if target is not None:
            n += size(target)
        stack.discard(key)
        if n > FIGURE_MAX_EXPANSION:
            raise FigureError(f"mehr als {FIGURE_MAX_EXPANSION} Elemente nach dem Auflösen von use")
        sizes[key] = n
        return n

    try:
        size(root)
    except RecursionError as exc:
        raise FigureError("use zu tief verschachtelt") from exc


def svg_ohne_kopf(text: str) -> str:
    """Der Text ohne den Standardkopf `<!DOCTYPE svg PUBLIC …>` aus SVG_DOCTYPES, und nur dort, wo ein Kopf stehen kann:
    vor dem Wurzelelement, nach XML-Angabe und Kommentaren. Alles andere bleibt stehen und wird von clean_svg abgelehnt."""
    start = SVG_PROLOG_RE.match(text).end()
    m = SVG_DOCTYPE_RE.match(text, start)
    if m and SVG_DOCTYPES.get(m.group(2)) == m.group(4):
        return text[:start] + text[m.end():]
    return text


def svg_length(value: str | None) -> str | None:
    """Eine Länge mit Zahl und absoluter Einheit als Text in Nutzereinheiten; None, wenn sie nicht positiv oder relativ ist."""
    m = SVG_LENGTH_RE.fullmatch(value or "")
    if not m:
        return None
    px = float(m.group(1)) * SVG_UNITS[(m.group(2) or "").lower()]
    return f"{px:.3f}".rstrip("0").rstrip(".") if 0 < px < 1e9 else None


def svg_verlust(removed: list[str]) -> dict:
    """Was clean_svg entfernt hat, für Menschen: `sichtbar` kann die Abbildung ändern, `ohne_wirkung` sind Angaben der
    Werkzeuge. Unbekanntes zählt als sichtbar. `hinweise` sagt in Sätzen, was das für die Abbildung heißt."""
    def harmlos(item: str) -> bool:
        kind, _, what = item.partition(" ")
        what = what.removesuffix(" (fremder Namensraum)")
        return (item.endswith("(fremder Namensraum)") or item == "Element metadata"
                or (kind == "Attribut" and (what in SVG_ATTRIBUTES_OHNE_WIRKUNG or what.startswith(("data-", "aria-"))))
                or (kind == "Stil" and (what in SVG_STYLE_OHNE_WIRKUNG or what.startswith("-"))))
    sichtbar = [item for item in removed if not harmlos(item)]
    hinweise = []
    if "Element foreignObject" in sichtbar:
        hinweise.append("HTML-Beschriftungen (foreignObject, etwa aus draw.io) sind entfernt; zu sehen ist nur ein einfacher "
                        "Ersatztext, wenn die Datei einen hat. In draw.io vor dem Export „Formatierter Text“ abwählen.")
    if "Element image" in sichtbar:
        hinweise.append("Eingebettete Bilder (image) sind entfernt: Eine Abbildung besteht nur aus Vektoren.")
    if any(item.startswith("Stilregeln nicht") for item in sichtbar):
        hinweise.append("Einfache style-Regeln (Element, .klasse, #id) sind übernommen. Die genannten lassen sich nicht auf "
                        "einzelne Elemente übertragen; Farben und Linien können darum abweichen.")
    if "Element a" in sichtbar:
        hinweise.append("Links (a) sind samt ihrem Inhalt entfernt.")
    if sichtbar and not hinweise:
        hinweise.append("Die Abbildung kann darum anders aussehen als im Zeichenprogramm.")
    return {"sichtbar": sichtbar, "ohne_wirkung": [item for item in removed if harmlos(item)], "hinweise": hinweise}


def clean_svg(source, prefix: str = "", label: str = "") -> tuple[str, list[str]]:
    """Bereinigtes SVG als Text für die Seite (Standardbibliothek, xml.etree): (SVG, was entfernt wurde).

    Abgelehnt (FigureError) wird, was sich nicht sicher lesen lässt: größer als 200 KB, anderes als UTF-8 (auch
    UTF-16 ohne BOM, NUL-Bytes), `<!DOCTYPE` (außer dem Standardkopf, siehe svg_ohne_kopf) oder `<!ENTITY`, kein gültiges
    XML, Wurzel nicht svg, zu viele Elemente (auch nach dem Auflösen von `<use>`, Zyklen eingeschlossen), kein viewBox und
    keine umrechenbaren width und height. Alles andere wird bereinigt: Elemente und Attribute außerhalb der Allowlist samt
    Inhalt entfallen, fremde Namensräume ebenso, href nur auf #id, url(…) nur auf #id, style nur mit SVG_STYLE_PROPERTIES,
    transform nicht an der Wurzel (es höbe die Abbildung über die Seite). <style> und class kommen nie auf die Seite:
    einfache Regeln werden in das style-Attribut der passenden Elemente übernommen (svg_style_rules). <switch> wird zur
    Gruppe mit der ersten erlaubten Alternative. Mit prefix werden ids und ihre Verweise je Abbildung umbenannt, damit
    sich mehrere nicht stören.
    """
    import xml.etree.ElementTree as ET
    data = source if isinstance(source, bytes) else str(source).encode("utf-8")
    if len(data) > FIGURE_MAX_BYTES:
        raise FigureError(f"größer als {FIGURE_MAX_BYTES // 1024} KB")
    if b"\x00" in data:
        raise FigureError("nicht UTF-8 (NUL-Bytes)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FigureError("nicht UTF-8") from exc
    text = svg_ohne_kopf(text)
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise FigureError("nur der Standardkopf <!DOCTYPE svg PUBLIC …> ist erlaubt, andere DOCTYPEs und ENTITY nicht: "
                          "die Datei ohne sie neu exportieren")
    try:
        root = ET.fromstring(text)   # als Text: eine encoding-Angabe im Kopf kann die Prüfung oben nicht mehr umgehen
    except (ET.ParseError, ValueError) as exc:
        raise FigureError(f"kein gültiges XML ({exc})") from exc

    def local(tag) -> tuple[str, str]:
        if not isinstance(tag, str):   # Kommentar oder Verarbeitungsanweisung
            return "?", ""
        if tag.startswith("{"):
            ns, _, name = tag[1:].partition("}")
            return ns, name
        return "", tag

    root_ns, root_name = local(root.tag)
    if root_name != "svg" or root_ns not in ("", SVG_NS):
        raise FigureError("Wurzelelement ist kein svg")
    removed: set[str] = set()
    count = 0
    if re.search(r"<\?xml-stylesheet\b", text):
        removed.add("Verweis auf ein Stylesheet (xml-stylesheet)")
    css = []
    for el in root.iter():
        if local(el.tag) == (root_ns, "style"):
            if el.get("media", "all").strip().lower() in ("all", "screen"):
                css.append(el.text or "")
            else:
                removed.add("Stilregeln mit media")
    rules, dropped_rules = svg_style_rules("\n".join(css))
    removed |= dropped_rules
    inlined = 0

    def rule_style(el, name: str) -> str:
        """Die Deklarationen aller Regeln, die auf el passen, nach Gewicht und Reihenfolge wie im Browser."""
        nonlocal inlined
        qualifiers = [""] + [f".{c}" for c in set(el.get("class", "").split())] + ([f"#{el.get('id')}"] if el.get("id") else [])
        hits = sorted(hit for tag in ("*", name) for q in qualifiers for hit in rules.get((tag, q), ()))
        merged = ";".join(hit[2] for hit in hits)
        inlined += len(merged)
        if inlined > FIGURE_MAX_BYTES:
            raise FigureError(f"mit den übernommenen style-Regeln größer als {FIGURE_MAX_BYTES // 1024} KB")
        return merged

    def add_text(el, text: str | None) -> None:
        if text:
            if len(el):
                el[-1].tail = (el[-1].tail or "") + text
            else:
                el.text = (el.text or "") + text

    def build(el, depth: int):
        nonlocal count
        count += 1
        if count > FIGURE_MAX_ELEMENTS:
            raise FigureError(f"mehr als {FIGURE_MAX_ELEMENTS} Elemente")
        if depth > 60:
            raise FigureError("zu tief verschachtelt")
        name = local(el.tag)[1]
        out = ET.Element(f"{{{SVG_NS}}}{'g' if name == 'switch' else name}")
        for key, value in el.attrib.items():
            kns, kname = local(key)
            if kns == XLINK_NS and kname == "href":
                kns = ""
            if kns or kname not in SVG_ATTRIBUTES or kname.lower().startswith("on") \
                    or (depth == 0 and kname == "transform"):
                if kname != "class":   # Klassen wirken nur über die Regeln oben
                    removed.add(f"Attribut {kname}" + (" (fremder Namensraum)" if kns else ""))
                continue
            if kname == "href" and not (name in SVG_HREF_ELEMENTS and value.strip().startswith("#")
                                        and SVG_ID_RE.fullmatch(value.strip()[1:])):
                removed.add("Attribut href")
                continue
            if kname == "id" and not SVG_ID_RE.fullmatch(value):
                removed.add("Attribut id")
                continue
            if kname != "id" and not svg_value_ok(value, style=kname == "style"):
                removed.add(f"Attribut {kname}")
                continue
            if kname == "style":
                value, dropped = svg_style(value)
                removed.update(f"Stil {d}" for d in dropped)
                if not value:
                    continue
            if kname == "id":
                value = prefix + value
            elif kname == "href":
                value = "#" + prefix + value.strip()[1:]
            else:
                value = svg_prefixed(value, prefix)
            out.set(kname, value)
        inherited = rule_style(el, name) if rules else ""
        if inherited:
            own = out.get("style")
            out.set("style", svg_prefixed(inherited, prefix) + (f";{own}" if own else ""))
        in_text = name in SVG_TEXT_ELEMENTS
        if in_text:
            out.text = el.text
        for child in el:
            cns, cname = local(child.tag)
            if name == "switch" and len(out):
                continue   # von switch zeichnet der Browser nur die erste Alternative
            if cns == root_ns and cname == "style":   # steht schon in rules
                if in_text:
                    add_text(out, child.tail)
                continue
            if cns == root_ns and (cname in SVG_ELEMENTS or cname == "switch"):
                sub = build(child, depth + 1)
                if cname == "use" and sub.get("href") is None:
                    removed.add("Element use")
                    if in_text:
                        add_text(out, child.tail)
                    continue
                out.append(sub)
                sub.tail = child.tail if in_text else None
            else:
                removed.add(f"Element {cname or 'Kommentar'}" + ("" if cns == root_ns else " (fremder Namensraum)"))
                if in_text:
                    add_text(out, child.tail)
        return out

    clean = build(root, 0)
    svg_expansion_check(clean)
    if not clean.get("viewBox") or not SVG_VIEWBOX_RE.fullmatch(clean.get("viewBox", "")):
        w, h = svg_length(clean.get("width")), svg_length(clean.get("height"))
        if not (w and h):
            raise FigureError('viewBox fehlt, und width und height lassen sich nicht umrechnen (nur Zahlen oder px, pt, pc, '
                              'mm, cm, in, Q; kein % oder em): im svg-Element viewBox="0 0 Breite Höhe" ergänzen')
        clean.set("viewBox", f"0 0 {w} {h}")
    clean.set("role", "img")
    if label:
        clean.set("aria-label", label)
    ET.register_namespace("", SVG_NS)
    result = ET.tostring(clean, encoding="unicode")
    if inlined and len(result.encode("utf-8")) > FIGURE_MAX_BYTES:   # die abgelegte Datei muss die Grenze beim Bau wieder bestehen
        raise FigureError(f"mit den übernommenen style-Regeln größer als {FIGURE_MAX_BYTES // 1024} KB")
    return result, sorted(removed)


def css_plain(css: str) -> str:
    """CSS, wie der Browser es liest (CSS Syntax 3): ohne Kommentare, Maskierungen aufgelöst. `/*` in einer Zeichenkette
    und in url(…) ohne Anführungszeichen beginnt keinen Kommentar; eine Zeichenkette endet spätestens am Zeilenende."""
    out: list[str] = []
    i, n = 0, len(css)

    def escaped(at: int) -> tuple[str, int]:
        hex_ = re.compile(r"([0-9a-fA-F]{1,6})(?:\r\n|\s)?").match(css, at + 1)
        if hex_:
            code = int(hex_.group(1), 16)
            return (chr(code) if 0 < code < 0x110000 and not 0xD800 <= code < 0xE000 else "\ufffd"), hex_.end()
        return css[at + 1:at + 2], at + 2

    while i < n:
        c = css[i]
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "\"'":
            out.append(c)
            i += 1
            while i < n and css[i] != c and css[i] not in "\n\r\f":
                if css[i] == "\\":
                    piece, i = escaped(i)
                    out.append(piece)
                else:
                    out.append(css[i])
                    i += 1
            out.append(c)
            i += i < n and css[i] == c
        elif c == "\\":
            piece, i = escaped(i)
            out.append(piece)
        else:
            out.append(c)
            i += 1
            if c == "(" and "".join(out[-4:]).lower() == "url(":
                while i < n and css[i].isspace():
                    i += 1
                if i < n and css[i] in "\"'":   # url("…") liest der Zweig für Zeichenketten
                    continue
                while i < n and css[i] != ")":
                    if css[i] == "\\":
                        piece, i = escaped(i)
                        out.append(piece)
                    else:
                        out.append(css[i])
                        i += 1
    return "".join(out)


def css_problem(css: str) -> str | None:
    """Warum ein Design (CSS) nicht eingebettet wird: `@import`, `url(` auf etwas anderes als `#…` oder `data:`
    für Schriften und Rasterbilder, `</style>`. Kommentare zählen nicht (außer für `</style`), Zeichenketten schon;
    CSS-Maskierungen werden vorher aufgelöst (css_plain)."""
    if re.search(r"</\s*style", css, re.I):
        return "enthält </style"
    text = css_plain(css)
    if re.search(r"@\s*import", text, re.I):
        return "@import lädt Fremdes nach"
    if CSS_LOADER_RE.search(text) or re.search(r"expression\(|-moz-binding|(?<!scroll-)behavior\s*:", text, re.I):
        return "image-set, src, image, expression, binding und behavior sind nicht erlaubt"
    urls = list(re.finditer(r"url\(\s*([\"']?)(.*?)\1\s*\)", text, re.I | re.S))
    if len(urls) != len(re.findall(r"url\(", text, re.I)):
        return "url( ohne Ende"
    for m in urls:
        target = m.group(2).strip()
        if target.startswith("#") or CSS_DATA_RE.match(target):
            continue
        return f"url({target[:40]}) lädt Fremdes nach oder ist kein erlaubtes data: (erlaubt: #…, data: für Schriften und Rasterbilder)"
    return None


# Schemata, die ein Link im Dashboard tragen darf
LINK_SCHEMES = ("http", "https", "mailto")


def link_scheme(href) -> str | None:
    """Schema eines Links, wie der Browser es liest (Zeichenreferenzen aufgelöst, Tab und Zeilenumbruch entfernt,
    Steuerzeichen und Leerzeichen am Anfang übergangen), klein geschrieben; None bei relativen Adressen."""
    text = re.sub(r"[\t\n\r]", "", html.unescape(str(href or ""))).lstrip("".join(map(chr, range(0x21))))
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", text)
    return m.group(1).lower() if m else None


def safe_link(href) -> str:
    """Die Adresse, wenn sie absolut ist und ein Schema aus LINK_SCHEMES trägt, sonst ""."""
    return str(href) if link_scheme(href) in LINK_SCHEMES else ""


class _EntryCleaner(HTMLParser):
    """Literaturangabe als HTML: nur Auszeichnung, class und Links aus LINK_SCHEMES; alles andere wird zu Text."""

    TAGS = {"a", "b", "code", "div", "em", "i", "small", "span", "strong", "sub", "sup"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.TAGS:
            return
        keep = [(k, v) for k, v in attrs if v and (k == "class" and re.fullmatch(r"[\w -]+", v)
                                                   or k == "href" and tag == "a" and safe_link(v))]
        self.out.append(f"<{tag}" + "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in keep) + ">")

    def handle_endtag(self, tag):
        if tag in self.TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))


def clean_entry(fragment: str) -> str:
    """Eine Literaturangabe von pandoc so kürzen, dass sie gefahrlos als HTML in die Seite darf."""
    cleaner = _EntryCleaner()
    cleaner.feed(fragment)
    cleaner.close()
    return "".join(cleaner.out)
