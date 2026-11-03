"""Bilder erzeugen (Befehl `bild`): Wünsche in Worten → geprüfte API-Felder, Prompt-Hinweise, Erklärtexte. Ohne Netz;
den Aufruf macht rollen.bild_direkt. Getestet an einem vLLM-Omni-Backend (OpenAI-Form, Z Image Turbo); Felder außerhalb
von FELDER gehen nie hinaus, weil ein solches Backend unbekannte Namen stumm annimmt."""

import re
import secrets

# Formate: (Breite, Höhe), Vielfache von 16. getestet: am Backend ausprobiert
FORMATE = {"quadrat": (1024, 1024), "quer": (1536, 864), "hoch": (864, 1536), "gross": (2048, 1152),
           "4:3": (1280, 960), "3:2": (1536, 1024)}
GETESTET = {"quadrat", "quer", "hoch", "gross"}
TEMPI = {"entwurf": 8, "fein": None}   # num_inference_steps; None: Vorgabe des Modells
SEKUNDEN = {"entwurf": 7, "fein": 24}  # gemessen bei 1536x864; wächst mit der Fläche
GUIDANCE_OHNE = 4                      # ohne guidance_scale wirkt negative_prompt kaum; mit verkürzten Schritten (8, 16)
                                       # ergab sie im Test Rauschen oder eine leere Fläche, darum dann immer Tempo fein
SEITE_MIN, SEITE_MAX = 256, 2048
VARIANTEN_MAX = 4
SEED_MAX = 2 ** 31 - VARIANTEN_MAX   # Varianten zählen den Seed hoch und bleiben so im int32-Bereich
STIL_MAX = 600                     # Zeichen einer Stilvorlage, auch aus einer fremden wiki.toml
OHNE_TEXT = "no text, no letters, no logos"
FELDER = ("model", "prompt", "size", "seed", "n", "response_format", "output_format", "num_inference_steps",
          "guidance_scale", "negative_prompt")
# Stil-Knöpfe: Wörter, die am Modell verlässlich wirkten
STILE = {"foto": "photograph, natural light", "tilt-shift": "tilt-shift miniature photograph",
         "linolschnitt": "linocut print", "risografie": "risograph print", "stickerei": "embroidery on linen",
         "oelgemaelde": "oil painting", "film-noir": "film noir, black and white", "isometrisch": "isometric 3D illustration",
         "pop-art": "pop art", "flach": "minimal flat editorial illustration"}

WUNSCH_FORMAT = [(r"quadrat|1:1", "quadrat"), (r"\bbreit|folie|querformat|16:9|\bquer\b", "quer"),
                 (r"hochkant|hochformat|plakat|handy|9:16", "hoch"), (r"\bgroß|\bgross|druck", "gross"),
                 (r"\b4:3\b", "4:3"), (r"\b3:2\b", "3:2")]
WUNSCH_TEMPO = [(r"entwurf|skizze|schnell", "entwurf"), (r"\bfein|final|endgültig", "fein")]
# „ohne …“ aus deutschen Worten: Der Negativ-Prompt wirkt auf Englisch; Unbekanntes bleibt stehen, mit Hinweis
OHNE_WORT = {"menschen": "people", "personen": "people", "leute": "people", "gesichter": "faces", "hände": "hands",
             "tiere": "animals", "autos": "cars", "fahrzeuge": "vehicles", "kabel": "cables", "pflanzen": "plants",
             "bäume": "trees", "gebäude": "buildings", "logos": "logos", "wasserzeichen": "watermark", "rahmen": "border",
             "schatten": "shadows", "himmel": "sky", "unschärfe": "blur", "rauschen": "noise", "farbe": "color",
             "hintergrund": "background", "möbel": "furniture", "bildschirme": "screens", "flaggen": "flags"}
ZAHLWORT = {"zwei": 2, "drei": 3, "vier": 4, "2": 2, "3": 3, "4": 4}
# Farbnamen, die das Modell als Gegenstand liest („lemon-yellow“ ergab Zitronen)
DINGFARBEN = re.compile(r"\b(lemon|zitronen?\w*|lime|limetten?\w*|salmon|lachs\w*|cherry|kirsch\w*|olive|oliven?\w*|"
                        r"banana|bananen?\w*|mint\w*|ros[ée]|peach|pfirsich\w*)", re.I)
VIELE = re.compile(r"\b(vier|fünf|sechs|sieben|acht|neun|zehn|four|five|six|seven|eight|nine|ten|[4-9]|1\d)\s+\w", re.I)


class BildFehler(ValueError):
    pass


def groesse(wert: str) -> tuple[tuple[int, int], str]:
    """Formatname oder BxH → (Breite, Höhe) als Vielfache von 16, dazu ein Hinweis, wenn gerundet oder ungetestet."""
    wert = wert.strip().lower().replace("×", "x").replace("groß", "gross")
    if wert in FORMATE:
        return FORMATE[wert], "" if wert in GETESTET else f"{wert} ist am Modell noch nicht ausprobiert."
    m = re.fullmatch(r"(\d{2,5})\s*x\s*(\d{2,5})", wert)
    if not m:
        raise BildFehler(f"Format {wert!r}: eins von {', '.join(FORMATE)} oder BREITExHÖHE")
    roh = (int(m.group(1)), int(m.group(2)))
    maß = tuple(min(SEITE_MAX, max(SEITE_MIN, int(v / 16 + 0.5) * 16)) for v in roh)
    return maß, "" if maß == roh else (f"{roh[0]}x{roh[1]} auf {maß[0]}x{maß[1]} gerundet: Das Modell nimmt nur Vielfache "
                                       f"von 16 zwischen {SEITE_MIN} und {SEITE_MAX}.")


def wunsch(text: str) -> dict:
    """Freie Worte → Einstellungen. Nur, was erkannt wird, steht im Ergebnis; ausdrückliche Schalter gehen vor."""
    t = text.lower()
    out: dict = {}
    for muster, name in WUNSCH_FORMAT:
        if re.search(muster, t):
            out["format"] = name
            break
    for muster, name in WUNSCH_TEMPO:
        if re.search(muster, t):
            out["tempo"] = name
            break
    ohne = [s.strip() for g in re.findall(r"\b(?:ohne|keine?n?)\s+([^,.;]+)", t) for s in re.split(r"\s+(?:und|oder)\s+", g)]
    ohne = [OHNE_WORT.get(s, s) for s in ohne if not re.fullmatch(r"text|schrift", s)]
    if ohne:
        out["ohne"] = ohne
    if re.search(r"\bmit (?:text|schrift)\b", t):
        out["mit_text"] = True
    if re.search(r"\b(?:ohne|keine?n?) (?:text|schrift)\b", t):
        out["mit_text"] = False
    m = re.search(r"\b(zwei|drei|vier|[234])\s+varianten\b", t)
    if m:
        out["varianten"] = ZAHLWORT[m.group(1)]
    if re.search(r"ganz anders|neue variante|neuer seed", t):
        out["neu"] = True
    if re.search(r"(?:in )?meinem design|mein design", t):
        out["stil"] = "design"
    else:
        for name in STILE:
            if re.search(rf"\b{name.replace('-', '[- ]?').replace('oe', '(?:oe|ö)').replace('ae', '(?:ae|ä)')}", t):
                out["stil"] = name
                break
    return out


def prompt_hinweise(prompt: str, mit_text: bool) -> list[str]:
    """Was die Erkundung über dieses Modell gezeigt hat, als Hinweis zum Prompt."""
    out = []
    if len(prompt.split()) < 12:
        out.append("Kurzer Prompt: Was soll zu sehen sein, wo (links, rechts, Mitte), aus welchem Material, in welchem Licht "
                   "und Stil? Fünf Wörter ergaben eine leere Schachtel, siebzig mit gleichem Seed das gewünschte Bild.")
    zitate = re.findall(r"[\"„“”']([^\"„“”']+)[\"„“”']", prompt)
    if mit_text and any(len(z.split()) >= 3 for z in zitate):
        out.append("Text im Bild: Kurze Wörter in Anführungszeichen gelingen, ab drei Wörtern kommen echt aussehende "
                   "Fehler. Den Satz lieber in die Bildunterschrift setzen.")
    elif mit_text and not zitate:
        out.append("Text im Bild: Den gewünschten Wortlaut in Anführungszeichen setzen, sonst erfindet das Modell Wörter.")
    m = DINGFARBEN.search(prompt)
    if m:
        out.append(f"„{m.group(0)}“ liest das Modell als Gegenstand. Neutralen Farbnamen mit Hex-Code nehmen, "
                   "etwa „bright yellow (#FCE53A)“.")
    if VIELE.search(prompt):
        out.append("Bis drei Elemente stimmen verlässlich, ab vier oft nicht mehr (aus fünf Feldern wurden acht).")
    return out


def stil_text(name: str, eigene: dict) -> str:
    if name in eigene:
        vorlage = str(eigene[name])
        if len(vorlage) > STIL_MAX:
            raise BildFehler(f"Stil {name!r}: höchstens {STIL_MAX} Zeichen ([bild.stile] in der wiki.toml)")
        return vorlage
    if name in STILE:
        return STILE[name]
    raise BildFehler(f"Stil {name!r}: eins von {', '.join([*eigene, *STILE])} ([bild.stile] in der wiki.toml)")


def anfrage(prompt: str, *, format: str = "quadrat", tempo: str = "fein", ohne: list[str] | None = None,
            seed: int | None = None, mit_text: bool = False, stil: str = "", stile: dict | None = None) -> dict:
    """Die geprüfte Anfrage ohne Modell und Key: {"nutzlast", "hinweise", "erklaerung", "sekunden"}."""
    prompt = prompt.strip()
    if not prompt:
        raise BildFehler("Prompt fehlt")
    if tempo not in TEMPI:
        raise BildFehler(f"Tempo {tempo!r}: {' oder '.join(TEMPI)}")
    (b, h), format_hinweis = groesse(format)
    ohne = [o.strip() for o in ohne or [] if o.strip()]
    if ohne and tempo == "entwurf":
        tempo = "fein"
        format_hinweis = " ".join(filter(None, [format_hinweis, "„Ohne …“ braucht Guidance, und Guidance mit verkürzten "
                                                "Schritten ergab im Test leere oder verrauschte Bilder: Tempo fein statt Entwurf."]))
    text = prompt
    if stil:
        vorlage = stil_text(stil, stile or {})
        text = vorlage.replace("{motiv}", prompt) if "{motiv}" in vorlage else f"{prompt}. {vorlage}"
    if not mit_text and "no text" not in text.lower():
        text = f"{text.rstrip('. ')}. {OHNE_TEXT}."
    if seed is None:
        seed = secrets.randbelow(SEED_MAX + 1)
    elif not 0 <= int(seed) <= SEED_MAX:
        raise BildFehler(f"Seed: 0 bis {SEED_MAX}")
    nutzlast = {"prompt": text, "size": f"{b}x{h}", "seed": int(seed), "n": 1, "response_format": "b64_json",
                "output_format": "webp"}
    if TEMPI[tempo]:
        nutzlast["num_inference_steps"] = TEMPI[tempo]
    if ohne:
        nutzlast["negative_prompt"] = ", ".join(ohne)
        nutzlast["guidance_scale"] = GUIDANCE_OHNE
    assert set(nutzlast) <= set(FELDER)
    sekunden = round(SEKUNDEN[tempo] * b * h / (1536 * 864))
    hinweise = [x for x in [format_hinweis, *prompt_hinweise(prompt, mit_text)] if x]
    erklaerung = [
        f"Format {b}x{h}. Möglich: {', '.join(f'{k} {v[0]}x{v[1]}' for k, v in FORMATE.items())}; frei nur Vielfache von 16.",
        f"Seed {seed}: Gleiche Zahl, gleiche Einstellungen, gleiches Bild. Mit anderem Format oder Tempo entsteht ein anderes Bild.",
        f"Tempo {tempo}: " + ("8 Schritte, ein Entwurf in Sekunden." if tempo == "entwurf" else "Vorgabe des Modells, feiner, dauert länger."),
        ("Ohne: " + ", ".join(ohne) + f". Wirkt nur mit Guidance; okfw setzt sie dafür auf {GUIDANCE_OHNE}.") if ohne
        else "Nichts ausgeschlossen („ohne …“ setzt einen Negativ-Prompt).",
        "Text im Bild: an." if mit_text else f"Text im Bild: aus, okfw hängt „{OHNE_TEXT}“ an.",
        f"Erwartete Dauer: etwa {sekunden} s, unter Last mehr.",
    ]
    return {"nutzlast": nutzlast, "hinweise": hinweise, "erklaerung": erklaerung, "sekunden": sekunden}


def planen(prompt: str, *, wunsch_text: str = "", stile: dict | None = None, format: str | None = None,
           tempo: str | None = None, ohne: list[str] | None = None, seed: int | None = None, varianten: int | None = None,
           mit_text: bool | None = None, stil: str | None = None) -> dict:
    """Befehl bild und Dialog in serve: Worte und ausdrückliche Einstellungen → Anfrage. Ausdrückliches geht vor.
    Zurück: erkannt (aus den Worten), einstellungen (was gilt, mit Seed), varianten und alles aus anfrage()."""
    w = wunsch(wunsch_text or "")
    stile = stile or {}
    hinweise = []
    gewaehlt = stil if stil is not None else w.get("stil", "")
    if stil is None and gewaehlt == "design" and "design" not in stile:
        hinweise.append("„in meinem Design“: Die wiki.toml hat keinen Stil [bild.stile] design; ohne Stil weiter.")
        gewaehlt = ""
    offen = [o for o in w.get("ohne", []) if o not in OHNE_WORT.values()]
    if offen:
        hinweise.append(f"Ohne: {', '.join(offen)} bleibt so stehen. Der Negativ-Prompt wirkt auf Englisch am besten; "
                        "im Feld „Ohne“ übersetzen.")
    n = w.get("varianten", 1) if varianten is None else varianten
    if not isinstance(n, int) or not 1 <= n <= VARIANTEN_MAX:
        raise BildFehler(f"Varianten: 1 bis {VARIANTEN_MAX}")
    ein = {"format": format or w.get("format", "quadrat"), "tempo": tempo or w.get("tempo", "fein"),
           "ohne": [*w.get("ohne", []), *(ohne or [])], "mit_text": w.get("mit_text", False) if mit_text is None else mit_text,
           "stil": gewaehlt}
    a = anfrage(prompt, seed=seed, stile=stile, **ein)
    ein.update(tempo="entwurf" if "num_inference_steps" in a["nutzlast"] else "fein", seed=a["nutzlast"]["seed"], varianten=n)
    if n > 1:
        a["erklaerung"].append(f"Varianten: {n}, Seeds {ein['seed']} bis {ein['seed'] + n - 1}, jede einzeln wiederholbar.")
    return {**a, "erkannt": w, "einstellungen": ein, "varianten": n, "hinweise": [*hinweise, *a["hinweise"]]}


def unterschrift(titel: str, modell: str, anbieter: str, datum: str) -> str:
    """Bildunterschrift, die Lint figure_generated erfüllt; ein Zusatz in Klammern am Anbieternamen („(Beta)“) entfällt."""
    anbieter = re.sub(r"\s*\([^()]*\)\s*$", "", anbieter) or anbieter
    return f"*Abb.: {titel}. KI-generiert mit {modell} ({anbieter}), {datum}.*"
