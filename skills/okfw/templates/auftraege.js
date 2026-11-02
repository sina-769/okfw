// okfw serve: Aufträge und Vorschläge. Fortschritt, Abbrechen und die Vorschlagsliste gibt es nur hier, für alle Auftragstypen.
// Wird nur von `okfw.py serve` eingefügt, nie in die Dashboard-Datei zum Weitergeben. Eine Art meldet sich mit art() an.
(() => {
  "use strict";
  const { h, api, say, fail, frage, openLayer } = window.okfwServe;
  const AKTIV = new Set(["wartet", "laeuft"]);
  const ZUSTAND = { wartet: "wartet", laeuft: "läuft", fertig: "fertig", gescheitert: "gescheitert", abgebrochen: "abgebrochen" };
  const arten = {};
  let liste = [], zaehler = null, faden = null;
  const beobachter = new Map();   // Kennung des Auftrags → Rückruf, sobald er nicht mehr läuft oder sich sein Fortschritt ändert

  // ---------------------------------------------------------------- Abfragen, solange etwas läuft
  async function abfragen() {
    const r = await api("api/auftraege");
    if (!r.ok) return;
    const vorher = new Map(liste.map(d => [d.id, d]));
    liste = r.data.auftraege;
    for (const d of liste) {
      const alt = vorher.get(d.id);
      if (beobachter.has(d.id)) beobachter.get(d.id)(d);
      else if (alt && AKTIV.has(alt.zustand) && !AKTIV.has(d.zustand)) {
        if (d.zustand === "fertig") say(`Der Vorschlag für „${d.titel || d.ziel}“ liegt bereit. Er steht unter „Aufträge“.`);
        else if (d.zustand === "gescheitert") say(`Der Auftrag für „${d.titel || d.ziel}“ ist gescheitert: ${d.fehler}`, "err");
      }
    }
    zaehlerZeigen();
    if (liste.some(d => AKTIV.has(d.zustand))) { if (!faden) faden = setTimeout(() => { faden = null; abfragen(); }, 1000); }
  }

  function zaehlerZeigen() {
    if (!zaehler) return;
    zaehler.hidden = !liste.length;
    zaehler.textContent = `Aufträge (${liste.length})`;
  }

  async function starten(typ, daten) {
    const res = await api("api/auftrag", { typ, ...daten });
    if (res.ok) { await abfragen(); }
    return res;
  }

  async function offenFuer(ziel) {
    await abfragen();
    const d = liste.find(x => x.ziel === ziel);
    return d ? d.id : null;
  }

  // ---------------------------------------------------------------- Ein Auftrag in einem Kasten
  function abbrechenKnopf(kennung) {
    return h("button", { class: "ghost", type: "button", onclick: async e => {
      e.target.disabled = true;
      const r = await api("api/auftrag/abbrechen", { auftrag: kennung });
      if (!r.ok && r.status !== 409) { e.target.disabled = false; fail(r); }
      abfragen();
    } }, "Auftrag abbrechen");
  }

  function zeigen(kennung, box, ctx = {}) {
    ctx.laeuft?.(true);
    box.replaceChildren(h("p", { class: "okfw-note" }, "Der Auftrag wird abgefragt."));
    const fertig = async () => {
      beobachter.delete(kennung);
      ctx.laeuft?.(false);
      if (!box.isConnected) return;
      const r = await api(`api/auftrag?id=${encodeURIComponent(kennung)}`);
      if (!r.ok) { box.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); return; }
      ergebnis(r.data, box, ctx);
    };
    let gezeigt = null;   // Nur bei neuem Text zeichnen: sonst verschwindet der Knopf unter dem Zeiger, und Vorleser melden jede Abfrage
    beobachter.set(kennung, d => {
      if (!box.isConnected) { beobachter.delete(kennung); ctx.laeuft?.(false); return; }
      if (AKTIV.has(d.zustand)) {
        if (gezeigt === d.fortschritt) return;
        gezeigt = d.fortschritt;
        box.replaceChildren(h("p", { class: "okfw-note" }, d.fortschritt || "Der Auftrag läuft.", " Du kannst die Seite schließen, er läuft im Server weiter."),
          h("div", { class: "okfw-bar" }, abbrechenKnopf(kennung)));
      } else fertig();
    });
    const jetzt = liste.find(d => d.id === kennung);
    if (jetzt) beobachter.get(kennung)(jetzt); else fertig();   // schon erledigt: Dann steht er nicht mehr in der Liste
  }

  function ergebnis(d, box, ctx) {
    if (d.zustand !== "fertig") {
      box.replaceChildren(h("div", {}, h("div", { class: d.zustand === "abgebrochen" ? "okfw-warn" : "okfw-err" }, d.fehler || ZUSTAND[d.zustand]),
        d.gesehen ? null : h("div", { class: "okfw-bar" }, h("button", { class: "ghost", type: "button", onclick: async () => {
          const r = await api("api/vorschlag/verwerfen", { auftrag: d.id });
          if (!r.ok) return fail(r);
          box.replaceChildren();
          abfragen();
        } }, "Ausblenden"))));
      return;
    }
    const offen = d.aenderungen.filter(a => a.status === "offen");
    const gespeichert = h("span", {}, "Gespeichert ist noch nichts.");
    const eigen = { ...ctx, angenommen: x => { gespeichert.textContent = "Angenommenes ist gespeichert."; ctx.angenommen?.(x); } };
    const zeilen = new Map(offen.map(a => [a.id, aenderung(d, a, box, eigen, () => zeilen)]));
    // h() lässt null weg; das native replaceChildren schriebe es als Text
    box.replaceChildren(h("div", {},
      h("p", { class: "okfw-note" }, `Vorschlag von ${d.rolle.modell} (${d.rolle.anbieter}, ${d.rolle.harness}).${d.gesendet?.hinweis ? ` ${d.gesendet.hinweis}${d.gesendet.zeichen ? `, ${d.gesendet.zeichen} Zeichen` : ""}.` : ""} `, gespeichert),
      d.warnungen.length ? h("div", { class: "okfw-warn" }, h("strong", {}, "Prüfen"), h("ul", {}, d.warnungen.map(w => h("li", {}, w)))) : null,
      offen.length > 1 ? h("div", { class: "okfw-bar" }, h("button", { class: "ghost", type: "button", onclick: async e => {
        e.target.disabled = true;
        for (const z of zeilen.values()) if (!await z.annehmen()) break;
        e.target.remove();
      } }, "Alle annehmen")) : null,
      [...zeilen.values()]));
  }

  function aenderung(d, a, box, ctx, alle) {
    const art = arten[a.art];
    const zeile = h("div", { class: "okfw-aenderung" });
    const entschieden = text => {
      zeile.replaceChildren(h("div", { class: "okfw-ok" }, text));
      abfragen();
    };
    const annehmen = async () => {
      if (zeile.entschieden) return true;
      if (ctx.vorAnnehmen && !await ctx.vorAnnehmen(a)) return false;
      const r = await api("api/vorschlag/annehmen", { auftrag: d.id, aenderung: a.id });
      if (!r.ok) { fail(r); return false; }
      ctx.angenommen?.(r.data);
      if (art?.nachAnnehmen) { zeile.entschieden = true; zeile.replaceChildren(art.nachAnnehmen(r.data)); abfragen(); return true; }
      // Was die Änderung gebraucht hat, wurde mit angenommen
      for (const kennung of r.data.angenommen || [a.id]) alle().get(kennung)?.erledigt("Angenommen und gespeichert.");
      return true;
    };
    const verwerfen = async () => {
      const r = await api("api/vorschlag/verwerfen", { auftrag: d.id, aenderung: a.id });
      if (!r.ok) return fail(r);
      zeile.entschieden = true;
      zeile.replaceChildren();
      abfragen();
    };
    zeile.annehmen = annehmen;
    zeile.erledigt = text => { zeile.entschieden = true; entschieden(text); };
    zeile.append(art ? art.vorschau(a) : h("p", {}, `${a.art}: ${a.ziel}`),
      h("div", { class: "okfw-bar" },
        h("button", { class: "btn-primary", type: "button", onclick: annehmen }, "Annehmen"),
        art?.zusatz ? art.zusatz(d, a, ctx, entschieden) : null,
        h("button", { class: "ghost", type: "button", onclick: verwerfen }, "Verwerfen")));
    return zeile;
  }

  // ---------------------------------------------------------------- Alle Aufträge
  function uebersicht() {
    let neuLaden = false;
    const dlg = openLayer("Aufträge", { onClose: () => { if (neuLaden) location.reload(); } });
    const inhalt = h("div", {});
    const zeichnen = () => {
      inhalt.replaceChildren(liste.length ? "" : h("p", { class: "okfw-note" }, "Es gibt keine offenen Aufträge."));
      for (const d of liste) {
        const box = h("div", {});
        inhalt.append(h("div", { class: "okfw-card okfw-auftrag" },
          h("div", { class: "okfw-auftrag-kopf" }, h("strong", {}, d.titel || d.ziel), h("span", { class: "okfw-note" }, ZUSTAND[d.zustand] || d.zustand)),
          box));
        zeigen(d.id, box, { angenommen: () => { neuLaden = true; } });
      }
    };
    dlg.box.append(h("p", { class: "okfw-note" }, "Aufträge laufen im Server, auch wenn du die Seite schließt. Was ein Modell vorschlägt, gilt erst, wenn du es annimmst."), inhalt);
    abfragen().then(zeichnen);
  }

  function werkzeug() {
    const leiste = document.querySelector(".okfw-tools");
    if (!leiste) return;
    zaehler = h("button", { class: "ghost", type: "button", hidden: true, onclick: uebersicht }, "Aufträge");
    leiste.append(zaehler);
    abfragen();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", werkzeug); else werkzeug();

  window.okfwAuftraege = { art: (name, def) => { arten[name] = def; }, starten, zeigen, offenFuer, abfragen };
})();
