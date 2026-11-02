// okfw serve: Auftragstyp „Schaubild“. Die Änderung ist ein SVG, das erst beim Annehmen abgelegt wird.
(() => {
  "use strict";
  const { h } = window.okfwServe;
  window.okfwAuftraege.art("abbildung", {
    vorschau: a => {
      if (a.daten.abgelehnt) return h("div", { class: "okfw-err" }, `Die Bereinigung lehnt dieses SVG ab: ${a.daten.abgelehnt}`);
      const bild = h("div", { class: "okfw-ab-vorschau", role: "group", "aria-label": `Vorschau: ${a.daten.titel}` });
      bild.innerHTML = a.daten.vorschau;   // vom Server frisch bereinigt, dieselbe Fassung wie in der Leseansicht
      return bild;
    },
    // In der Übersicht gibt es keine Schreibmarke: Die Zeile zum Einfügen steht dann zum Kopieren da
    nachAnnehmen: r => h("div", { class: "okfw-ok" }, `Abbildung ${r.name} abgelegt. Zum Einbinden in eine Seite:`, h("pre", {}, r.einbindung)),
  });
})();
