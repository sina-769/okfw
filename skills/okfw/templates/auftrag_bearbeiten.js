// okfw serve: Auftragstyp „Bearbeiten per Prompt“. Die Änderung ist ein neuer Text für eine Seite.
(() => {
  "use strict";
  const { h, api, fail, diffView } = window.okfwServe;
  window.okfwAuftraege.art("koerper", {
    vorschau: a => diffView(a.daten.alt, a.daten.neu),
    // Nur im Editor: Die Person überarbeitet den Text und speichert selbst
    zusatz: (d, a, ctx, entschieden) => ctx.insTextfeld ? h("button", { class: "ghost", type: "button", onclick: async () => {
      const r = await api("api/vorschlag/annehmen", { auftrag: d.id, aenderung: a.id, ziel: "textfeld" });
      if (!r.ok) return fail(r);
      ctx.insTextfeld(r.data.neu);
      entschieden("Übernommen ins Textfeld. Speichern ist ein eigener Schritt.");
    } }, "Ins Textfeld") : null,
  });
})();
