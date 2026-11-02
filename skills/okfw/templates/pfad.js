// okfw Pfad: Lesen und Vortrag. Dieselbe Datei läuft im Dashboard und in der Datei aus `okfw.py pfad export`.
window.okfwPfad = (() => {
  "use strict";
  const PROPS = new Set(["value", "checked", "disabled", "hidden"]);
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (PROPS.has(k)) e[k] = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    const add = c => { if (Array.isArray(c)) c.forEach(add); else if (c !== null && c !== undefined && c !== false) e.append(c); };
    kids.forEach(add);
    return e;
  }
  const inhalt = (html, klasse) => { const d = h("div", { class: klasse || "prose-content" }); d.innerHTML = html; return d; }; // im Kern aus Markdown erzeugt, rohes HTML ist dort maskiert

  function abzeichen(e) {
    if (!(e.stand || []).length) return null;
    return h("div", { class: "pf-stand" }, e.stand.map(z => h("span", { class: `pf-ab pf-ab-${z.art}` }, z.text)));
  }

  function karteEl(e, karten, opt) {
    if (e.fehlt) {
      return h("article", { class: "pf-karte pf-fehlt", style: lage(e) },
        h("div", { class: "pf-kopf" }, "Karte fehlt"), h("h3", {}, e.titel),
        h("p", {}, "Diese Karte gibt es nicht mehr. Der Pfad verweist noch auf sie."));
    }
    const k = karten[e.id] || {};
    const zeigt = e.zeigt || "kurz";
    const koerper = h("div", { class: "pf-koerper" });
    const voll = () => {
      koerper.replaceChildren(inhalt(k.html || ""));
      if ((k.refs || []).length) {
        koerper.append(h("ul", { class: "pf-lit" }, k.refs.map(r => { const li = h("li", {}); li.innerHTML = r; return li; }))); // im Kern bereinigt
      }
    };
    if (zeigt === "ganz") voll();
    else if (zeigt === "absatz" && k.excerpt) koerper.append(h("p", {}, k.excerpt));
    const karte = h("article", { class: `pf-karte pf-zeigt-${zeigt}`, style: lage(e) },
      h("div", { class: "pf-kopf" }, e.typ),
      opt.titel === e.titel && (e.beschreibung || zeigt !== "kurz") ? null : h("h3", {}, e.titel), // steht schon als Titel der Station darüber
      e.beschreibung ? h("p", { class: "pf-besch" }, e.beschreibung) : null,
      koerper, abzeichen(e));
    if (opt.aufklappen && zeigt !== "ganz") {
      const knopf = h("button", { class: "ghost pf-auf", type: "button", "aria-expanded": "false", onclick: () => {
        const auf = knopf.getAttribute("aria-expanded") !== "true";
        knopf.setAttribute("aria-expanded", String(auf));
        knopf.textContent = auf ? "Zuklappen" : "Aufklappen";
        if (auf) voll();
        else { koerper.replaceChildren(); if (zeigt === "absatz" && k.excerpt) koerper.append(h("p", {}, k.excerpt)); }
        karte.classList.toggle("pf-offen", auf);
      } }, "Aufklappen");
      karte.append(knopf);
    }
    return karte;
  }

  const lage = e => `grid-column:${e.pos[0] + 1}/span ${e.pos[2]};grid-row:${e.pos[1] + 1}/span ${e.pos[3]}`;

  function textEl(e) {
    return h("div", { class: "pf-text", style: lage(e) }, inhalt(e.html), h("div", { class: "pf-vermerk" }, "eigener Text"));
  }

  function flaeche(s, pfad, karten, opt) {
    const el = h("div", { class: "pf-flaeche", style: `--spalten:${pfad.raster};--zeilen:${s.zeilen}`, "data-layout": s.layout || "raster" },
      s.elemente.map(e => e.art === "text" ? textEl(e) : karteEl(e, karten, { ...opt, titel: s.titel })));
    el.addEventListener("click", ev => {
      const a = ev.target.closest && ev.target.closest("a[data-page]");
      if (!a) return;
      ev.preventDefault();
      opt.oeffne?.(a.dataset.page);
    });
    return el;
  }

  function schicht(klasse, beschriftung) {
    const vorher = document.activeElement;
    const el = h("div", { class: `pf-schicht ${klasse}`, role: "dialog", "aria-modal": "true", "aria-label": beschriftung, tabindex: "-1" });
    document.body.append(el);
    document.body.classList.add("pf-offen-body");
    const zu = () => {
      el.remove();
      if (!document.querySelector(".pf-schicht")) document.body.classList.remove("pf-offen-body");
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      vorher?.focus?.();
    };
    return { el, zu };
  }

  // ---------------------------------------------------------------- Lesen
  function lesen(pfad, karten, opt = {}) {
    const { el, zu } = schicht("pf-lesen", `Pfad lesen: ${pfad.titel}`);
    el.addEventListener("keydown", ev => { if (ev.key === "Escape") zu(); });
    const nachLesen = id => { zu(); opt.oeffne?.(id); };
    el.append(h("div", { class: "pf-leiste" },
      h("strong", { class: "pf-titel" }, pfad.titel),
      h("span", { class: "pf-sp" }),
      h("button", { class: "ghost", type: "button", onclick: () => { zu(); starten(pfad, karten, opt); } }, "Vortrag"),
      h("button", { class: "ghost", type: "button", onclick: zu }, "Schließen")),
      h("div", { class: "pf-spalte" },
        pfad.beschreibung ? h("p", { class: "pf-besch" }, pfad.beschreibung) : null,
        pfad.stationen.map((s, i) => h("section", { class: "pf-station", "aria-label": `Station ${i + 1} von ${pfad.stationen.length}: ${s.titel}` },
          h("div", { class: "pf-nr" }, `Station ${i + 1} von ${pfad.stationen.length}`),
          h("h2", {}, s.titel),
          flaeche(s, pfad, karten, { aufklappen: true, oeffne: opt.oeffne ? nachLesen : null })))));
    el.focus();
  }

  // ---------------------------------------------------------------- Vortrag
  function starten(pfad, karten, opt = {}) {
    const befunde = (pfad.befunde || []).filter(b => b.code !== "pfad_text_ohne_beleg");
    if (!befunde.length) return vortrag(pfad, karten, opt);
    const { el, zu } = schicht("pf-befunde", "Vor dem Vortrag");
    el.addEventListener("keydown", ev => { if (ev.key === "Escape") zu(); });
    el.append(h("div", { class: "pf-kasten" },
      h("h2", {}, "Vor dem Vortrag"),
      h("p", {}, "Das Publikum sieht diese Hinweise als Abzeichen auf den Karten. Du entscheidest."),
      h("ul", {}, befunde.map(b => h("li", {}, b.text))),
      h("div", { class: "pf-leiste" },
        h("button", { class: "btn-primary", type: "button", onclick: () => { zu(); vortrag(pfad, karten, opt); } }, "Trotzdem starten"),
        h("button", { class: "ghost", type: "button", onclick: zu }, "Abbrechen"))));
    el.focus();
  }

  function buehne(s, i, pfad, karten) {
    return h("div", { class: "pf-buehne" },
      h("h2", {}, s.titel),
      flaeche(s, pfad, karten, {}),
      h("div", { class: "pf-fuss" }, `Station ${i + 1} von ${pfad.stationen.length}`, opt_stand(pfad)));
  }
  const opt_stand = pfad => pfad.stand ? ` · Stand ${pfad.stand}` : "";

  async function vortrag(pfad, karten, opt = {}) {
    const { el, zu: schichtZu } = schicht("pf-vortrag", `Vortrag: ${pfad.titel}`);
    const stationen = pfad.stationen;
    let nr = 0, notizAn = false, fenster = null;
    let notizen = Object.fromEntries(stationen.map(s => [s.slug, s.notizen || []]));
    const buehneOrt = h("div", { class: "pf-ort" });
    const notizOrt = h("div", { class: "pf-notiz", hidden: true, "aria-label": "Sprechernotizen" });
    const hoerer = h("div", { class: "pf-sr", role: "status", "aria-live": "polite" });
    const hatNotizen = () => Object.values(notizen).some(n => n.length);

    function fensterSchreiben() {
      if (!fenster || fenster.closed) { fenster = null; return; }
      const d = fenster.document, s = stationen[nr], naechste = stationen[nr + 1];
      d.title = `Notizen: ${pfad.titel}`;
      d.body.style.cssText = "font:18px/1.5 system-ui,sans-serif;margin:24px";
      d.body.textContent = "";
      const titel = d.createElement("h2"); titel.textContent = `${nr + 1} von ${stationen.length}: ${s.titel}`;
      d.body.append(titel);
      for (const n of (notizen[s.slug] || [])) { const p = d.createElement("p"); p.textContent = n; p.style.whiteSpace = "pre-wrap"; d.body.append(p); }
      if (!(notizen[s.slug] || []).length) { const p = d.createElement("p"); p.textContent = "Keine Notiz zu dieser Station."; d.body.append(p); }
      if (naechste) { const p = d.createElement("p"); p.textContent = `Danach: ${naechste.titel}`; p.style.opacity = ".7"; d.body.append(p); }
    }
    function notizZeichnen() {
      if (!stationen.length) return;
      const liste = notizen[stationen[nr].slug] || [];
      notizOrt.replaceChildren(...(liste.length ? liste.map(n => h("p", {}, n)) : [h("p", {}, "Keine Notiz zu dieser Station.")]));
      notizOrt.hidden = !notizAn;
      el.classList.toggle("pf-mit-notiz", notizAn);
      fensterSchreiben();
    }
    function zeigen(i) {
      if (!stationen.length) {
        buehneOrt.replaceChildren(h("div", { class: "pf-buehne" }, h("h2", {}, pfad.titel), h("p", {}, "Dieser Pfad hat keine Station.")));
        vor.disabled = zurueck.disabled = true;
        return;
      }
      nr = Math.max(0, Math.min(stationen.length - 1, i));
      buehneOrt.replaceChildren(buehne(stationen[nr], nr, pfad, karten));
      hoerer.textContent = `Station ${nr + 1} von ${stationen.length}: ${stationen[nr].titel}`;
      vor.disabled = nr === stationen.length - 1;
      zurueck.disabled = nr === 0;
      notizZeichnen();
    }
    const vollbild = () => {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else el.requestFullscreen?.().catch(() => {});
    };
    const zu = () => { if (fenster && !fenster.closed) fenster.close(); schichtZu(); };
    function notizFenster() {
      fenster = window.open("", "okfw-notizen", "width=560,height=460");
      if (!fenster) {   // Pop-ups gesperrt: die Notizen stehen dann unter der Bühne
        notizAn = true;
        hoerer.textContent = "Das zweite Fenster ließ sich nicht öffnen. Die Notizen stehen unter der Bühne.";
        notizZeichnen();
        return;
      }
      fenster.addEventListener("keydown", tasten);
      fensterSchreiben();
    }
    function drucken() {
      const alt = document.querySelector(".pf-druck");
      if (alt) alt.remove();
      const druck = h("div", { class: "pf-druck" }, stationen.map((s, i) => h("div", { class: "pf-seite" }, buehne(s, i, pfad, karten))));
      document.body.append(druck);
      document.body.classList.add("pf-drucken");
      const fertig = () => { druck.remove(); document.body.classList.remove("pf-drucken"); window.removeEventListener("afterprint", fertig); };
      window.addEventListener("afterprint", fertig);
      window.print();
    }
    function tasten(ev) {
      if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
      const k = ev.key;
      if (k === " " && ev.target.tagName === "BUTTON") return;   // die Leertaste drückt den Knopf, sie blättert nicht
      ev.stopPropagation();
      if (k === "ArrowRight" || k === "ArrowDown" || k === "PageDown" || k === " ") zeigen(nr + 1);
      else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") zeigen(nr - 1);
      else if (k === "Home") zeigen(0);
      else if (k === "End") zeigen(stationen.length - 1);
      else if (k === "f" || k === "F") vollbild();
      else if ((k === "n" || k === "N") && hatNotizen()) { notizAn = !notizAn; notizZeichnen(); }
      else if (k === "p" || k === "P") drucken();
      else if (k === "Escape" && !document.fullscreenElement) zu();
      else return;
      ev.preventDefault();
    }
    const zurueck = h("button", { class: "ghost", type: "button", "aria-label": "Vorige Station", onclick: () => zeigen(nr - 1) }, "←");
    const vor = h("button", { class: "ghost", type: "button", "aria-label": "Nächste Station", onclick: () => zeigen(nr + 1) }, "→");
    const notizKnoepfe = h("span", { class: "pf-notizknoepfe", hidden: !hatNotizen() },
      h("button", { class: "ghost", type: "button", title: "Taste N", onclick: () => { notizAn = !notizAn; notizZeichnen(); } }, "Notizen"),
      h("button", { class: "ghost", type: "button", onclick: notizFenster }, "Notizen im eigenen Fenster"));
    el.append(buehneOrt, h("div", { class: "pf-leiste" },
      zurueck, vor,
      h("button", { class: "ghost", type: "button", title: "Taste F", onclick: vollbild }, "Vollbild"),
      notizKnoepfe,
      h("button", { class: "ghost", type: "button", title: "Taste P: je Station eine Seite", onclick: drucken }, "Als PDF"),
      h("span", { class: "pf-sp" }),
      h("span", { class: "pf-hinweis" }, "← → blättern · F Vollbild · P Ausgabe als PDF"),
      h("button", { class: "ghost", type: "button", onclick: zu }, "Schließen")),
      notizOrt, hoerer);
    el.addEventListener("keydown", tasten);
    zeigen(0);
    el.focus();
    if (opt.notizen) {   // okfw serve holt sie vom Server; in der Dashboard-Datei stehen keine
      const geladen = await opt.notizen(pfad.id).catch(() => null);
      if (geladen) { notizen = geladen; notizKnoepfe.hidden = !hatNotizen(); notizZeichnen(); }
    }
  }

  return { lesen, starten };
})();
