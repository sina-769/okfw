// okfw serve: Auftragstyp „Bild“ und der Dialog „Bild erzeugen“. Worte werden zu Schaltern, die man ändern kann; vor dem
// Senden steht da, was gesendet wird. Abgelegt wird erst die Variante, die die Person annimmt.
(() => {
  "use strict";
  const { h, api, say, openLayer } = window.okfwServe;

  window.okfwAuftraege.art("bild", {
    vorschau: a => {
      if (a.daten.abgelehnt) return h("div", { class: "okfw-err" }, `Die Bereinigung lehnt dieses Bild ab: ${a.daten.abgelehnt}`);
      const n = a.daten.nachweis || {}, seed = (n.anfrage || {}).seed;
      // data: vom Server frisch bereinigt; die Seite lädt sonst kein Bild
      return h("figure", { class: "okfw-bild-vorschau" }, h("img", { src: a.daten.vorschau, alt: `Vorschau: ${a.daten.titel}` }),
        h("figcaption", { class: "okfw-note" }, `${a.daten.name}.webp · ${a.daten.groesse} · Seed ${seed}${n.dauer_s ? ` · ${n.dauer_s} s` : ""}`));
    },
    nachAnnehmen: r => h("div", { class: "okfw-ok" }, `Bild ${r.name} abgelegt. Zum Einbinden in eine Seite:`, h("pre", {}, r.einbindung)),
  });

  const TEMPO = { fein: "fein (Vorgabe des Modells, rund 24 s)", entwurf: "Entwurf (8 Schritte, Sekunden)" };
  const STIL = { foto: "Foto", "tilt-shift": "Tilt-Shift", linolschnitt: "Linolschnitt", risografie: "Risografie", stickerei: "Stickerei",
    oelgemaelde: "Ölgemälde", "film-noir": "Film noir", isometrisch: "isometrisches 3D", "pop-art": "Pop-Art", flach: "flache Illustration" };

  // einbinden: setzt die Zeilen an die Schreibmarke der Seite, die gerade bearbeitet wird
  function oeffnen(einbinden) {
    const dlg = openLayer("Bild erzeugen");
    const feld = (attr) => h("input", { type: "text", autocomplete: "off", ...attr });
    const name = feld({ id: "okfw-bild-name", placeholder: "z. B. werkbank" });
    const titel = feld({ id: "okfw-bild-titel", placeholder: "Titel (Alternativtext und Unterschrift)" });
    const prompt = h("textarea", { id: "okfw-bild-prompt", rows: "4", "aria-label": "Was soll zu sehen sein?",
      placeholder: "Was ist zu sehen, wo (links, rechts, Mitte), aus welchem Material, in welchem Licht? Englisch ist genauer." });
    const wunsch = feld({ id: "okfw-bild-wunsch", placeholder: "z. B. breit für eine Folie, schneller Entwurf, ohne Menschen, drei Varianten" });
    const format = h("select", { id: "okfw-bild-format" });
    const tempo = h("select", { id: "okfw-bild-tempo" }, Object.entries(TEMPO).map(([k, v]) => h("option", { value: k }, v)));
    const ohne = feld({ id: "okfw-bild-ohne", placeholder: "durch Komma getrennt, z. B. people, cables" });
    const varianten = h("select", { id: "okfw-bild-varianten" }, [1, 2, 3, 4].map(n => h("option", { value: String(n) }, String(n))));
    const text = h("input", { type: "checkbox", id: "okfw-bild-text" });
    const stil = h("select", { id: "okfw-bild-stil" }, h("option", { value: "" }, "ohne"));
    const seed = h("input", { type: "number", id: "okfw-bild-seed", min: "0", step: "1", inputmode: "numeric" });
    const neuerSeed = h("button", { class: "ghost", type: "button", onclick: () => { seed.value = ""; aktualisieren(); } }, "Neuer Seed");
    const erklaerung = h("div", { class: "okfw-note", "aria-live": "polite" });
    const hinweise = h("div", { "aria-live": "polite" });
    const json = h("pre", { class: "okfw-bild-json" });
    const ergebnis = h("div", { "aria-live": "polite" });

    const einstellungen = () => ({ name: name.value.trim(), titel: titel.value, prompt: prompt.value, format: format.value, tempo: tempo.value,
      ohne: ohne.value, varianten: Number(varianten.value), text: text.checked, stil: stil.value, seed: seed.value === "" ? null : Number(seed.value) });

    let stand = 0, gesperrt = "";
    async function aktualisieren(mitWorten = false) {
      const nr = ++stand;
      const r = await api("api/bild/vorschau", mitWorten ? { prompt: prompt.value, wunsch: wunsch.value, name: name.value.trim() } : einstellungen());
      if (nr !== stand) return;   // eine spätere Eingabe hat schon neu gefragt
      if (!r.ok) { hinweise.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); senden.disabled = true; return; }
      const d = r.data, e = d.einstellungen;
      if (!format.options.length) format.append(...Object.entries(d.formate).map(([k, v]) => h("option", { value: k }, `${k} ${v}`)));
      if (stil.options.length === 1) {
        if (d.stile.eigene.length) stil.append(h("optgroup", { label: "aus der wiki.toml" }, d.stile.eigene.map(s => h("option", { value: s }, s))));
        stil.append(h("optgroup", { label: "eingebaut" }, d.stile.eingebaut.map(s => h("option", { value: s }, STIL[s] || s))));
      }
      // Was gilt, steht in den Schaltern; ein krummes Format bleibt als Text sichtbar
      if (![...format.options].some(o => o.value === e.format)) format.append(h("option", { value: e.format }, e.format));
      format.value = e.format; tempo.value = e.tempo; varianten.value = String(e.varianten); text.checked = e.mit_text;
      stil.value = e.stil; seed.value = String(e.seed);
      if (mitWorten) ohne.value = e.ohne.join(", ");
      gesperrt = d.rolle.gesperrt;
      erklaerung.replaceChildren(
        h("p", {}, `Modell ${d.rolle.modell || "–"} bei ${d.rolle.anbieter}. Datenklasse des Wikis: ${d.datenklasse}. `,
          "Nicht in den Prompt: Personenbezogenes, reale Personen."),
        h("ul", {}, d.erklaerung.map(t => h("li", {}, t))));
      // replaceChildren schriebe null als Text
      hinweise.replaceChildren(...[
        gesperrt && h("div", { class: "okfw-err" }, `Die Rolle „Bilder erzeugen“ ist nicht bereit: ${gesperrt} Einrichten unter Einstellungen.`),
        d.belegt.length && h("div", { class: "okfw-err" }, `Schon vergeben: ${d.belegt.join(", ")}. Wähle einen anderen Namen.`),
        d.hinweise.length && h("div", { class: "okfw-warn" }, h("strong", {}, "Zum Prompt"), h("ul", {}, d.hinweise.map(t => h("li", {}, t)))),
      ].filter(Boolean));
      json.textContent = JSON.stringify(d.gesendet, null, 1);
      senden.textContent = `An ${d.rolle.anbieter} senden${e.varianten > 1 ? ` (${e.varianten} Bilder)` : ""}`;
      senden.disabled = !!gesperrt || !!d.belegt.length;
    }
    let faden = null;
    const spaeter = () => { clearTimeout(faden); faden = setTimeout(() => aktualisieren(), 400); };
    for (const el of [name, prompt, ohne]) el.addEventListener("input", spaeter);
    for (const el of [format, tempo, varianten, text, stil, seed]) el.addEventListener("change", () => aktualisieren());
    const uebernehmen = h("button", { class: "ghost", type: "button", onclick: () => aktualisieren(true) }, "In Schalter übernehmen");
    wunsch.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); aktualisieren(true); } });

    const senden = h("button", { class: "btn-primary", type: "button", onclick: async () => {
      if (!prompt.value.trim()) { say("Beschreibe zuerst, was zu sehen sein soll.", "err"); prompt.focus(); return; }
      if (!name.value.trim()) name.value = window.okfwServe.slugify(titel.value || prompt.value.split(/\s+/).slice(0, 4).join(" "));
      const res = await window.okfwAuftraege.starten("bild", einstellungen());
      if (!res.ok) { ergebnis.replaceChildren(h("div", { class: "okfw-err" }, res.data.fehler || "Fehler")); return; }
      window.okfwAuftraege.zeigen(res.data.id, ergebnis, {
        laeuft: an => { senden.disabled = an; },
        angenommen: r => {
          if (!einbinden) return;
          einbinden(r.einbindung);
          dlg.close(true);
          say(`Bild ${r.name} abgelegt und eingefügt. Prüfe die Unterschrift.`);
        },
      });
    } }, "Senden");

    const zeile = (...kinder) => h("div", { class: "okfw-bild-zeile" }, ...kinder);
    dlg.box.append(
      h("p", { class: "okfw-note" }, "Ein Bildmodell erzeugt ein WebP. Du siehst vorher, was gesendet wird, und danach die Bilder; abgelegt wird erst, was du annimmst. Das Bild ist eine Illustration, kein Beleg."),
      zeile(h("label", {}, "Name der Datei", name), h("label", {}, "Titel", titel)),
      h("label", {}, "Was soll zu sehen sein?", prompt),
      h("label", {}, "Wunsch in Worten", wunsch), h("div", { class: "okfw-bar" }, uebernehmen),
      zeile(h("label", {}, "Format", format), h("label", {}, "Tempo", tempo), h("label", {}, "Varianten", varianten)),
      zeile(h("label", {}, "Stil", stil), h("label", {}, "Ohne", ohne)),
      zeile(h("label", {}, "Seed", seed, neuerSeed), h("label", { class: "okfw-check" }, text, " Schrift im Bild zulassen")),
      erklaerung, hinweise,
      h("details", {}, h("summary", {}, "Was gesendet wird (ohne Schlüssel)"), json),
      h("div", { class: "okfw-bar" }, senden), ergebnis);
    aktualisieren();
    prompt.focus();
  }

  window.okfwBild = { oeffnen };
})();
