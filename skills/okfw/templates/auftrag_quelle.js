// okfw serve: Auftragstyp „Quellen hineinkippen“. PDF oder Markdown ablegen oder eine DOI nennen, Rechte klären, dann Vorschlag oder Karte ohne Modell.
// Was erlaubt ist, entscheidet der Server; hier stehen nur seine Antworten. Text aus Datei und Modell kommt nur als Textknoten in die Seite.
(() => {
  "use strict";
  const { h, api, say, fail, openLayer, anbauten, neueKarte, openSettings, reloadTo, slugify } = window.okfwServe;
  const ENDUNGEN = { pdf: "application/pdf", md: "text/markdown", markdown: "text/markdown", txt: "text/plain" };
  const setze = (el, ...kinder) => el.replaceChildren(...kinder.filter(k => k !== null));   // Das native replaceChildren schriebe „null“ als Text

  async function hochladen(datei) {
    const typ = ENDUNGEN[(datei.name.split(".").pop() || "").toLowerCase()];
    if (!typ) return { ok: false, status: 415, data: { fehler: "Nur PDF oder Markdown (.pdf, .md, .markdown, .txt)" } };
    try {
      const r = await fetch("api/quelle/datei", { method: "POST", credentials: "same-origin", body: datei,
        headers: { "Content-Type": typ, "X-OKFW": "1", "X-OKFW-Name": encodeURIComponent(datei.name) } });
      let d = {};
      try { d = await r.json(); } catch (e) { d = { fehler: `Unerwartete Antwort (HTTP ${r.status})` }; }
      return { ok: r.ok, status: r.status, data: d };
    } catch (e) { return { ok: false, status: 0, data: { fehler: "Der Server antwortet nicht. Läuft okfw serve noch?" } }; }
  }

  // ---------------------------------------------------------------- Rechte klären und entscheiden
  function klaerung(layer, start) {
    const aus = h("div", { "aria-live": "polite" });
    const titel = h("input", { type: "text", value: start.titel || "", oninput: () => { if (!eigen) slug.value = slugify(titel.value); } });
    let eigen = false;
    const slug = h("input", { type: "text", value: slugify(start.titel || ""), oninput: () => { eigen = true; } });
    const beschr = h("input", { type: "text" });
    const doi = h("input", { type: "text", value: start.kennung || "", placeholder: "10.1234/beispiel oder https://…" });
    const lizName = h("input", { type: "text", placeholder: "zum Beispiel CC BY 4.0" });
    const lizUrl = h("input", { type: "text", placeholder: "https://…" });
    const belege = { ki_auswertung: h("textarea", { rows: "2" }), volltext_ablegen: h("textarea", { rows: "2" }) };
    const rechteBox = h("div", {}), rolleBox = h("div", {}), kopf = h("div", {}), knoepfe = h("div", { class: "okfw-bar" });
    const trotz = h("input", { type: "checkbox" });
    let stand = null, lizenzGeaendert = false;

    const daten = () => ({ eingang: start.eingang || "", kennung: doi.value.trim(), name: start.name || "", titel: titel.value, slug: slug.value, beschreibung: beschr.value,
      lizenz: lizenzGeaendert ? { name: lizName.value, url: lizUrl.value } : undefined,   // Ohne eigene Angabe gilt, was der Server aus den APIs hat
      belege: { ki_auswertung: belege.ki_auswertung.value, volltext_ablegen: belege.volltext_ablegen.value }, trotz_rueckzug: trotz.checked });

    const rechtZeile = (r, feld) => h("div", { class: "okfw-recht " + (r.wert === "ja" ? "okfw-ok" : r.wert === "nein" ? "okfw-err" : "okfw-warn") },
      h("strong", {}, `${r.aktion}: ${r.wort}`), r.hinweis ? ` (${r.hinweis})` : "",
      r.wert === "bedingt" || r.wert === "fragen" ? h("label", {}, "Beleg, warum das hier erlaubt ist (kommt auf die Karte)", feld) : null);

    function knoepfeZeigen() {
      const r = stand.rechte.ki_auswertung, link = stand.art === "link" && !start.eingang;
      const hemmnis = stand.rolle_grund || (start.fehler ? "Der Text ließ sich nicht auslesen." : "") || (start.eingang && !start.zeichen ? "In der Datei steht kein lesbarer Text." : "")
        || (start.zu_lang ? "Der Text ist für einen Modellaufruf zu lang." : "") || (link ? "Zu einem Link holt okfw keinen Text: Webseiten gehen nur im Container und mit Freigabe. Lade die Datei herunter und ziehe sie hierher." : "")
        || (stand.rechte.volltext_ablegen.wert === "nein" && start.eingang ? "Das Ablegen ist nicht erlaubt, also bleibt die Karte ohne Modell." : "") || (r.wert === "nein" ? "Die KI-Auswertung ist nicht erlaubt." : "")
        || (!belege.ki_auswertung.value.trim() && r.wert !== "ja" ? "Ohne Beleg geht die Quelle an kein Modell." : "");
      setze(knoepfe,
        h("button", { class: "btn-primary", type: "button", disabled: Boolean(hemmnis), title: hemmnis, onclick: vorschlag }, "Vorschlag holen"),
        h("button", { class: "ghost", type: "button", onclick: karte }, "Nur Karte ohne Modell"),
        hemmnis ? h("span", { class: "okfw-note" }, hemmnis) : null,
        stand.rolle_grund ? h("button", { class: "ghost", type: "button", onclick: () => openSettings("rollen") }, "Einstellungen") : null);
    }

    async function klaeren() {
      const r = await api("api/quelle/klaeren", daten());
      if (!r.ok) { aus.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); return false; }
      stand = r.data;
      aus.replaceChildren();
      if (stand.titel && !titel.value) { titel.value = stand.titel; if (!eigen) slug.value = slugify(stand.titel); }
      if (!lizenzGeaendert) { lizName.value = stand.lizenz.name === "unknown" ? "" : stand.lizenz.name; lizUrl.value = stand.lizenz.url || ""; }
      setze(rechteBox,
        h("p", { class: "okfw-note" }, `Lizenzklasse: ${stand.rechte.klasse}.`, stand.lizenz_via && !lizenzGeaendert ? ` Lizenz laut ${stand.lizenz_via}.` : ""),
        rechtZeile(stand.rechte.ki_auswertung, belege.ki_auswertung), rechtZeile(stand.rechte.volltext_ablegen, belege.volltext_ablegen),
        stand.dublette ? h("div", { class: "okfw-err" }, `Diese Quelle gibt es schon: ${stand.dublette}.`) : null,
        stand.rueckzug.length ? h("div", { class: "okfw-err" }, h("strong", {}, "Rückzug oder Bedenken gemeldet"), h("ul", {}, stand.rueckzug.map(t => h("li", {}, t))),
          h("label", {}, trotz, " Trotzdem aufnehmen, mit Vermerk auf der Karte")) : null);
      const m = stand.rolle;
      rolleBox.replaceChildren(h("p", { class: "okfw-note" }, `Datenklasse dieses Wikis: ${stand.datenklasse}. Rolle ${stand.rolle_titel}: ${m.modell || "kein Modell"} (${m.anbieter}, ${m.harness}). `,
        "Ein Vorschlag schickt den ausgelesenen Text und die Titel der Konzeptkarten an dieses Modell. Text aus der Quelle kann Anweisungen enthalten; das Modell hat keine Werkzeuge, und geschrieben wird nur, was du annimmst."));
      knoepfeZeigen();
      return true;
    }

    async function vorschlag() {
      const r = await api("api/auftrag", { typ: "quelle", ...daten() });
      if (!r.ok) { aus.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); if (/Kennung/.test(r.data.fehler || "")) openSettings("person"); return; }
      const box = h("div", {});
      let angenommen = "";
      const fertig = h("button", { class: "btn-primary", type: "button", hidden: true, onclick: () => { layer.close(true); reloadTo(angenommen, false); } }, "Zur Karte");
      layer.box.replaceChildren(layer.head, h("h3", {}, titel.value), box, h("div", { class: "okfw-bar" }, fertig));
      await window.okfwAuftraege.abfragen();
      window.okfwAuftraege.zeigen(r.data.id, box, { angenommen: x => { angenommen = angenommen || x.id; fertig.hidden = false; } });
    }

    async function karte() {
      const r = await api("api/quelle/karte", daten());
      if (!r.ok) { aus.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); if (/Kennung/.test(r.data.fehler || "")) openSettings("person"); return; }
      layer.close(true);
      reloadTo(r.data.id, false);
    }

    const wort = start.eingang
      ? [`${start.name}, ${Math.round(start.bytes / 1024)} KB`, start.seiten ? `, ${start.seiten} Seiten` : "", start.zeichen ? `, ${start.zeichen} Zeichen` : "", start.abschottung ? `. Ausgelesen in der ${start.abschottung}, ohne Netz und ohne Zugriff auf deine Dateien.` : "."].join("")
      : "Quelle über eine Kennung oder einen Link.";
    setze(kopf, h("p", { class: "okfw-note" }, wort),
      start.fehler ? h("div", { class: "okfw-warn" }, start.fehler) : null,
      start.ohne_text?.length ? h("div", { class: "okfw-warn" }, `Ohne Text, vielleicht Bild oder Scan: Seite ${start.ohne_text.join(", ")}.`) : null,
      start.lizenz_im_text ? h("div", { class: "okfw-warn" }, `Im Text steht als Lizenz: ${start.lizenz_im_text}. Das sagt die Datei über sich selbst; prüfe es, bevor du es übernimmst. `,
        h("button", { class: "ghost", type: "button", onclick: () => { lizName.value = start.lizenz_im_text; lizenzGeaendert = true; klaeren(); } }, "Übernehmen")) : null);
    const metaKnopf = h("button", { class: "ghost", type: "button", onclick: () => { lizenzGeaendert = false; klaeren(); } }, "Metadaten holen");
    for (const feld of [lizName, lizUrl]) feld.addEventListener("change", () => { lizenzGeaendert = true; klaeren(); });
    for (const feld of Object.values(belege)) feld.addEventListener("input", () => { if (stand) knoepfeZeigen(); });
    layer.box.replaceChildren(layer.head, kopf,
      h("label", {}, "DOI oder Link", doi), h("div", { class: "okfw-bar" }, metaKnopf, start.doi && start.doi === doi.value ? h("span", { class: "okfw-note" }, "Diese DOI steht in der Datei; prüfe, ob sie zur Datei gehört.") : null),
      h("label", {}, "Titel", titel), h("label", {}, "Kennung der Datei (kebab-case)", slug), h("label", {}, "Beschreibung (ein Satz; fehlt sie, schlägt das Modell eine vor)", beschr),
      h("label", {}, "Lizenz", lizName), h("label", {}, "Adresse der Lizenz", lizUrl), rechteBox, rolleBox, aus, knoepfe);
    titel.focus();
    klaeren();
  }

  async function dateiAufnehmen(datei, layer) {
    const dlg = layer || openLayer("Neue Karte");
    dlg.box.replaceChildren(dlg.head, h("p", { class: "okfw-note", "aria-live": "polite" }, `${datei.name} wird abgelegt und ausgelesen.`));
    const r = await hochladen(datei);
    if (!r.ok) {
      dlg.box.replaceChildren(dlg.head, h("div", { class: "okfw-err" }, r.data.fehler || `Fehler (HTTP ${r.status})`));
      return;
    }
    klaerung(dlg, { ...r.data, kennung: r.data.doi || "" });
  }

  // ---------------------------------------------------------------- Teil des Dialogs „Neue Karte“
  function dateiWahl(layer) {
    const wahl = h("input", { type: "file", accept: ".pdf,.md,.markdown,.txt", hidden: true, onchange: () => { if (wahl.files[0]) dateiAufnehmen(wahl.files[0], layer); } });
    const zone = h("div", { class: "okfw-ablage" }, h("p", {}, "PDF oder Markdown hierher ziehen"), wahl,
      h("button", { class: "ghost", type: "button", onclick: () => wahl.click() }, "Datei wählen"));
    zielMachen(zone, datei => dateiAufnehmen(datei, layer));
    const kennung = h("input", { type: "text", placeholder: "10.1234/beispiel oder https://…" });
    const weiter = () => { if (kennung.value.trim()) klaerung(layer, { kennung: kennung.value.trim() }); };
    kennung.addEventListener("keydown", e => { if (e.key === "Enter") weiter(); });
    layer.box.append(h("div", { class: "okfw-card" }, h("h4", {}, "Eine Quelle aufnehmen"), zone,
      h("label", {}, "Oder DOI oder Link", kennung), h("div", { class: "okfw-bar" }, h("button", { class: "ghost", type: "button", onclick: weiter }, "Weiter"))));
  }
  anbauten.neueKarte.push(dateiWahl);

  function zielMachen(el, nimm) {
    el.addEventListener("dragover", e => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); el.classList.add("ueber"); } });
    el.addEventListener("dragleave", () => el.classList.remove("ueber"));
    el.addEventListener("drop", e => {
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault();
      el.classList.remove("ueber");
      if (e.dataTransfer.files.length > 1) say("Bitte eine Datei nach der anderen ablegen. Genommen wurde die erste.");
      nimm(e.dataTransfer.files[0]);
    });
  }

  function anmelden() {
    const knopf = [...document.querySelectorAll(".okfw-tools button")].find(b => b.textContent === "Neue Karte");
    if (knopf) zielMachen(knopf, datei => dateiAufnehmen(datei));
    // Daneben fallen gelassen ginge sonst die Seite zugunsten der Datei verloren, samt ungespeichertem Text
    window.addEventListener("dragover", e => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
    window.addEventListener("drop", e => {
      if (e.defaultPrevented || !e.dataTransfer?.files?.length) return;
      e.preventDefault();
      say("Die Datei wurde nicht abgelegt: Ziehe sie auf „Neue Karte“.");
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", anmelden); else anmelden();

  // ---------------------------------------------------------------- Änderungen im Vorschlag
  const liste = (titel, aussagen) => aussagen?.length ? [h("h4", {}, titel), h("ul", { class: "okfw-aussagen" }, aussagen.map(a =>
    h("li", {}, a.aussage, " ", h("span", { class: "okfw-note" }, `${a.fundstelle}, Anker am Original geprüft: „${a.anker}“`))))] : [];

  window.okfwAuftraege.art("quelle", {
    vorschau: a => {
      const d = a.daten;
      return h("div", { class: "okfw-card" }, h("h4", {}, `Quellenkarte: ${d.titel}`),
        h("p", { class: "okfw-note" }, `${a.ziel}, Lizenz ${d.lizenz?.name || "unbekannt"}. ${d.ablegen && d.modus !== "abstract" ? `Das Original wird als Rohquelle abgelegt: ${d.name}.` : "Das Original wird nicht abgelegt."} Landet als Entwurf.`),
        d.beschreibung ? h("p", {}, d.beschreibung) : null, d.kurzreferat ? h("p", {}, d.kurzreferat) : null,
        d.design ? h("p", { class: "okfw-note" }, `Design laut Text: ${d.design}`) : null,
        ...liste("Kernaussagen", d.kernaussagen), ...liste("Zahlen", d.zahlen), ...liste("Einschränkungen", d.einschraenkungen),
        d.verwandt?.length ? h("p", { class: "okfw-note" }, `Verknüpft mit: ${d.verwandt.join(", ")}`) : null);
    },
  });
  window.okfwAuftraege.art("seite", {
    vorschau: a => {
      const d = a.daten;
      return h("div", { class: "okfw-card" }, h("h4", {}, `Neue Karte: ${d.titel}`),
        h("p", { class: "okfw-note" }, `${a.ziel}, verknüpft mit ${d.quelle.ziel}${d.verwandt?.length ? ` und ${d.verwandt.join(", ")}` : ""}. Nimmt die Quellenkarte mit, falls sie noch offen ist. Landet als Entwurf.`),
        d.beschreibung ? h("p", {}, d.beschreibung) : null, d.erklaerung ? h("p", {}, d.erklaerung) : null,
        ...liste("Aussagen", d.aussagen));
    },
  });
})();
