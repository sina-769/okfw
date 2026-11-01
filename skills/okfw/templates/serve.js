// okfw serve: Bearbeiten im Dashboard. Wird nur von `okfw.py serve` eingefügt, nie in die Dashboard-Datei zum Weitergeben.
// Dockt über window.okfwEdit an die Vorlage an (reader, panel, release, ready); alles andere hängt sich selbst an.
(() => {
  "use strict";
  const HEADERS = { "Content-Type": "application/json", "X-OKFW": "1" };
  const STAND = ["error", "warn", "info"];
  let DATA = null;
  const data = () => DATA || (DATA = JSON.parse(document.getElementById("okf-data").textContent));
  const node = id => data().nodes.find(n => n.id === id);

  // ---------------------------------------------------------------- Bausteine
  const PROPS = new Set(["value", "checked", "disabled", "hidden", "selected", "readOnly"]);
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

  // Pfade relativ zur Seite: Alles liegt unter dem Präfix dieses Serverstarts.
  async function api(path, body) {
    const opt = body === undefined ? { credentials: "same-origin" }
      : { method: "POST", credentials: "same-origin", headers: HEADERS, body: JSON.stringify(body) };
    let r;
    try { r = await fetch(path, opt); }
    catch (e) { return { ok: false, status: 0, data: { fehler: "Der Server antwortet nicht. Läuft okfw serve noch?" } }; }
    let d = {};
    try { d = await r.json(); } catch (e) { d = { fehler: `Unerwartete Antwort (HTTP ${r.status})` }; }
    return { ok: r.ok, status: r.status, data: d };
  }

  let toasts = null;
  function say(text, kind) {
    if (!toasts) { toasts = h("div", { class: "okfw-toast", role: "status", "aria-live": "polite" }); document.body.append(toasts); }
    const box = h("div", { class: kind === "err" ? "err" : "" }, h("span", {}, text),
      h("button", { class: "ghost", type: "button", "aria-label": "Meldung schließen", onclick: () => box.remove() }, "×"));
    toasts.append(box);
    if (kind !== "err") setTimeout(() => box.remove(), 6000);
  }
  function fail(r) {
    const text = r.data.fehler || `Fehler (HTTP ${r.status})`;
    say(text, "err");
    if (/Kennung/.test(text) && r.status === 422) openSettings("person");
  }

  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch (e) { /* ohne Zwischenablage: Text bleibt zum Markieren stehen */ }
    const old = btn.textContent;
    btn.textContent = "Kopiert";
    setTimeout(() => { btn.textContent = old; }, 1500);
  }

  // Rückfrage in der Seite: native Dialoge unterdrücken eingebettete Browser (dann täte der Knopf still nichts).
  function frage(text, ja = "Ja") {
    return new Promise(fertig => {
      const d = h("dialog", { class: "okfw-frage", "aria-label": "Rückfrage" }, h("p", {}, text),
        h("div", { class: "okfw-bar" },
          h("button", { class: "btn-primary", type: "button", onclick: () => d.close("ja") }, ja),
          h("button", { class: "ghost", type: "button", autofocus: true, onclick: () => d.close() }, "Abbrechen")));
      d.addEventListener("close", () => { d.remove(); fertig(d.returnValue === "ja"); });
      document.body.append(d);
      d.showModal();
    });
  }

  function reloadTo(id, edit) {
    try {
      if (id) sessionStorage.setItem("okfw-open", id);
      if (edit) sessionStorage.setItem("okfw-edit", "1");
    } catch (e) { /* ohne Speicher */ }
    location.reload();
  }

  // ---------------------------------------------------------------- Ebenen (Bearbeiten, Einstellungen, Neue Karte)
  const layers = [];
  function openLayer(title, { dirty = () => false, onClose = () => {} } = {}) {
    const before = document.activeElement;
    const head = h("div", { class: "okfw-head" }, h("h2", { id: `okfw-t${layers.length}` }, title));
    const box = h("div", { class: "okfw-box" }, head);
    const el = h("div", { class: "okfw-layer", role: "dialog", "aria-modal": "true", "aria-labelledby": `okfw-t${layers.length}` }, box);
    const layer = { el, box, head, dirty, close(force) {
      if (!force && dirty()) {
        frage("Es gibt ungespeicherte Änderungen. Wirklich schließen und verwerfen?", "Verwerfen").then(ja => { if (ja) layer.close(true); });
        return false;
      }
      el.remove();
      layers.splice(layers.indexOf(layer), 1);
      if (!layers.length) document.body.classList.remove("okfw-open");
      onClose();
      if (before && before.focus && document.contains(before)) before.focus();
      return true;
    } };
    head.append(h("button", { class: "ghost", type: "button", onclick: () => layer.close(false) }, "Schließen"));
    layers.push(layer);
    document.body.classList.add("okfw-open");
    document.body.append(el);
    return layer;
  }
  document.addEventListener("keydown", ev => {
    if (document.querySelector("dialog.okfw-frage")) { ev.stopImmediatePropagation(); return; }  // Esc und Tab regelt der Dialog
    const top = layers[layers.length - 1];
    if (!top) return;
    if (ev.key === "Escape") { ev.stopImmediatePropagation(); ev.preventDefault(); top.close(false); }
    else if (ev.key === "Tab") {
      const f = [...top.el.querySelectorAll("button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])")]
        .filter(x => !x.disabled && x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (!top.el.contains(document.activeElement)) { first.focus(); ev.preventDefault(); }
      else if (ev.shiftKey && document.activeElement === first) { last.focus(); ev.preventDefault(); }
      else if (!ev.shiftKey && document.activeElement === last) { first.focus(); ev.preventDefault(); }
    }
  }, true);

  function tabs(list, start, show) {
    const bar = h("div", { class: "okfw-tabs", role: "tablist" });
    const btns = list.map(([id, label]) => h("button", { role: "tab", type: "button", id: `okfw-tab-${id}`, "aria-selected": "false", tabindex: "-1",
      onclick: () => pick(id) }, label));
    function pick(id) {
      list.forEach(([k], i) => { const on = k === id; btns[i].setAttribute("aria-selected", String(on)); btns[i].tabIndex = on ? 0 : -1; });
      show(id);
    }
    bar.addEventListener("keydown", ev => {
      const i = btns.indexOf(document.activeElement);
      if (i < 0 || !["ArrowLeft", "ArrowRight"].includes(ev.key)) return;
      const j = (i + (ev.key === "ArrowRight" ? 1 : btns.length - 1)) % btns.length;
      btns[j].focus();
      pick(list[j][0]);
    });
    bar.append(...btns);
    return { bar, pick: id => pick(id), start: () => pick(start) };
  }

  // ---------------------------------------------------------------- Zeilenvergleich
  function diffLines(a, b) {
    const x = a.split("\n"), y = b.split("\n");
    if (x.length * y.length > 4_000_000) return null;
    const L = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
    for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
      L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    while (i < x.length && j < y.length) {
      if (x[i] === y[j]) { out.push(["=", x[i]]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push(["-", x[i++]]);
      else out.push(["+", y[j++]]);
    }
    while (i < x.length) out.push(["-", x[i++]]);
    while (j < y.length) out.push(["+", y[j++]]);
    return out;
  }
  function diffView(a, b) {
    const ops = diffLines(a, b);
    const box = h("div", { class: "okfw-diff", role: "group", "aria-label": "Zeilenvergleich alt und neu" });
    if (!ops) { box.append(h("div", {}, "Die Texte sind für einen Zeilenvergleich zu groß. Unten steht der neue Text.")); return box; }
    if (!ops.some(o => o[0] !== "=")) { box.append(h("div", { class: "gap" }, "Keine Unterschiede")); return box; }
    for (let k = 0; k < ops.length; k++) {
      const [t, s] = ops[k];
      if (t === "=") {
        let e = k;
        while (e < ops.length && ops[e][0] === "=") e++;
        const n = e - k;
        if (n > 6 && (k > 0 || e < ops.length)) {
          const head = k > 0 ? 2 : 0, tail = e < ops.length ? 2 : 0;
          for (let m = k; m < k + head; m++) box.append(h("div", {}, "  " + ops[m][1]));
          box.append(h("div", { class: "gap" }, `… ${n - head - tail} unveränderte Zeilen …`));
          for (let m = e - tail; m < e; m++) box.append(h("div", {}, "  " + ops[m][1]));
          k = e - 1;
        } else { for (let m = k; m < e; m++) box.append(h("div", {}, "  " + ops[m][1])); k = e - 1; }
      } else box.append(h("div", { class: t === "+" ? "add" : "del" }, (t === "+" ? "+ " : "- ") + s));
    }
    return box;
  }

  // ---------------------------------------------------------------- Vorschau (vereinfacht)
  function mdPreview(src) {
    const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const inl = s => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>").replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, "<span class=\"okfw-img\">[Abbildung: $1]</span>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, "<span class=\"okfw-lnk\">$1</span>").replace(/\[\^([^\]\s]+)\]/g, "<sup>$1</sup>");
    const lines = src.replace(/\r\n/g, "\n").split("\n"), out = [];
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (/^```/.test(l)) {
        const code = [];
        for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
        out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      } else if (/^#{1,6}\s/.test(l)) { const n = l.match(/^#+/)[0].length; out.push(`<h${n}>${inl(l.replace(/^#+\s*/, ""))}</h${n}>`); }
      else if (/^\s*[-*+]\s/.test(l) || /^\s*\d+\.\s/.test(l)) {
        const tag = /^\s*\d+\./.test(l) ? "ol" : "ul", items = [];
        for (; i < lines.length && /^\s*([-*+]|\d+\.)\s/.test(lines[i]); i++) items.push(`<li>${inl(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""))}</li>`);
        i--; out.push(`<${tag}>${items.join("")}</${tag}>`);
      } else if (/^>\s?/.test(l)) {
        const q = [];
        for (; i < lines.length && /^>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^>\s?/, ""));
        i--; out.push(`<blockquote>${inl(q.join(" "))}</blockquote>`);
      } else if (/^\[\^[^\]\s]+\]:/.test(l)) out.push(`<p class="okfw-fn">${inl(l)}</p>`);
      else if (l.trim()) {
        const p = [];
        for (; i < lines.length && lines[i].trim() && !/^(#|```|>|\s*([-*+]|\d+\.)\s|\[\^[^\]\s]+\]:)/.test(lines[i]); i++) p.push(lines[i]);
        i--; out.push(`<p>${inl(p.join(" "))}</p>`);
      }
    }
    return out.join("\n");
  }

  // ---------------------------------------------------------------- Frontmatter als Felder
  // Zerlegt den Text in Felder (oberste Schlüssel). Alles, was der Mensch nicht anfasst, bleibt Zeichen für Zeichen.
  function segmente(raw, keys) {
    const lines = raw.match(/[^\n]*\n|[^\n]+$/g) || [];
    const segs = [];
    let pending = "", cur = null;
    for (const line of lines) {
      const m = line.match(/^([A-Za-z0-9_][^:\n]*?|"[^"\n]*"|'[^'\n]*'):(?=\s|$)/);
      if (m && !/^[\s#-]/.test(line)) { cur = { pre: pending, key: m[1].replace(/^["']|["']$/g, ""), head: m[0], text: line }; segs.push(cur); pending = ""; }
      else if (/^#/.test(line)) pending += line;
      else if (cur) cur.text += line;
      else pending += line;
    }
    if (pending) { if (cur) cur.text += pending; else return null; }
    const ok = segs.map(s => s.pre + s.text).join("") === raw && segs.length === keys.length && segs.every(s => keys.includes(s.key));
    return ok ? segs : null;
  }
  const zusammen = segs => segs.map(s => s.pre + s.text).join("");
  function yamlSkalar(s, orig) {
    if (typeof orig === "number" && /^-?\d+(\.\d+)?$/.test(s)) return s;
    if (typeof orig === "boolean" && /^(true|false)$/.test(s)) return s;
    if (s === "") return "\"\"";
    const schlicht = /^[A-Za-zÀ-ÿ][^:#\n"'\[\]{}&*!|>%@`,]*$/.test(s) && !/^(true|false|yes|no|on|off|null|y|n)$/i.test(s)
      && !/\s$/.test(s) && !/\s#/.test(s);
    return schlicht ? s : JSON.stringify(s);
  }
  const feldSkalar = (k, s, orig) => k === "order" ? yamlSkalar(s.trim(), 0) : yamlSkalar(s, orig);
  function quelleEintragen(raw, id, resource) {
    const lines = raw.match(/[^\n]*\n|[^\n]+$/g) || [];
    const eintrag = (einzug) => [`${einzug}- id: ${id}\n`, `${einzug}  resource: ${resource}\n`];
    const i = lines.findIndex(l => /^sources\s*:/.test(l));
    if (i < 0) return (raw && !raw.endsWith("\n") ? raw + "\n" : raw) + "sources:\n" + eintrag("").join("");
    if (/^sources\s*:\s*\[\s*\]\s*$/.test(lines[i].trimEnd())) { lines.splice(i, 1, "sources:\n", ...eintrag("")); return lines.join(""); }
    let end = i + 1;
    while (end < lines.length && (/^[ \t-]/.test(lines[end]) || !lines[end].trim())) end++;
    while (end > i + 1 && !lines[end - 1].trim()) end--;
    const first = lines.slice(i + 1, end).find(l => /^\s*-/.test(l));
    lines.splice(end, 0, ...eintrag(first ? first.match(/^\s*/)[0] : ""));
    return lines.join("");
  }
  // Konflikt, „Meinen Text behalten“: Unveränderte Eigenschaften folgen dem aktuellen Stand, sonst überschriebe sie das Speichern
  // mit dem alten. Rückgabe: Die Person hat sie selbst geändert, sie überschreiben also etwas.
  function behalten(st, aktuell) {
    const eigene = st.fm !== st.fm0;
    const neu = aktuell.frontmatter || "";
    Object.assign(st, { hash: aktuell.hash, body0: aktuell.body, fm0: neu, status: aktuell.status });
    if (!eigene) Object.assign(st, { fm: neu, felder: aktuell.felder });
    return eigene;
  }
  const quellenIds = felder => ((felder && felder.sources) || []).filter(e => e && e.id).map(e => String(e.id));

  // ---------------------------------------------------------------- Bearbeiten
  async function openEditor(id) {
    const r = await api(`api/seite?id=${encodeURIComponent(id)}`);
    if (!r.ok) return fail(r);
    const st = { ...r.data, body0: r.data.body, fm0: r.data.frontmatter || "", body: r.data.body, fm: r.data.frontmatter || "", vorschlag: false };
    const geschuetzt = new Set(st.geschuetzt);
    let reload = false, fmMode = "felder";
    const dirty = () => st.body !== st.body0 || st.fm !== st.fm0;
    const layer = openLayer(`Bearbeiten: ${st.titel}`, { dirty, onClose: () => { if (reload) reloadTo(st.id); } });
    const ta = h("textarea", { class: "okfw-text", "aria-label": "Inhalt der Seite als Markdown", spellcheck: "true", value: st.body,
      oninput: () => { st.body = ta.value; if (!prev.hidden) prev.innerHTML = mdPreview(st.body); } });
    const prev = h("div", { class: "okfw-prev", hidden: true, "aria-label": "Vorschau" });
    const msg = h("div", { "aria-live": "polite" });
    const save = h("button", { class: "btn-primary", type: "button", onclick: speichern }, "Speichern");

    // Inhalt
    const toggle = h("button", { class: "ghost", type: "button", "aria-pressed": "false", onclick: () => {
      const on = prev.hidden;
      prev.hidden = !on; ta.hidden = on;
      toggle.setAttribute("aria-pressed", String(on)); toggle.textContent = on ? "Zurück zum Text" : "Vorschau";
      if (on) prev.innerHTML = mdPreview(st.body);
    } }, "Vorschau");
    const einfuegen = text => {
      ta.hidden = false; prev.hidden = true; toggle.textContent = "Vorschau";
      const a = ta.selectionStart ?? ta.value.length, b = ta.selectionEnd ?? a;
      ta.setRangeText(text, a, b, "end");
      st.body = ta.value; ta.focus();
    };
    const fussnote = () => {
      const ids = quellenIds(st.felder);
      const dlg = openLayer("Fußnote einfügen");
      const sel = h("select", { id: "okfw-fn-id" }, ids.map(i => h("option", { value: i }, i)),
        h("option", { value: "" }, "andere Quelle …"));
      const alle = data().nodes.filter(n => n.type === "source");
      const andere = h("select", { id: "okfw-fn-other", hidden: !!ids.length }, alle.map(n => h("option", { value: n.id }, `${n.title} (${n.id.split("/").pop()})`)));
      sel.addEventListener("change", () => { andere.hidden = !!sel.value; });
      const stelle = h("input", { type: "text", id: "okfw-fn-stelle", placeholder: "z. B. S. 4 oder Abschn. 2.1" });
      const ok = h("button", { class: "btn-primary", type: "button", onclick: () => {
        let fid = sel.value;
        if (!fid) {
          const q = andere.value;
          if (!q) return;
          fid = q.split("/").pop();
          if (!quellenIds(st.felder).includes(fid)) {
            st.fm = quelleEintragen(st.fm, fid, `/${q}.md`);
            st.felder = { ...st.felder, sources: [...((st.felder && st.felder.sources) || []), { id: fid, resource: `/${q}.md` }] };
            say(`Die Quelle ${fid} steht jetzt in den Eigenschaften (sources). Speichern übernimmt beides.`);
          }
        }
        const q = (alle.find(n => n.id.endsWith("/" + fid)) || {}).title || fid;
        const resource = ((st.felder && st.felder.sources) || []).find(e => e && String(e.id) === fid);
        const def = `[^${fid}]: [${q}](${resource ? resource.resource : `/sources/${fid}.md`})${stelle.value.trim() ? ", " + stelle.value.trim() : ""}`;
        einfuegen(`[^${fid}]`);
        if (!new RegExp(`^\\[\\^${fid.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\]:`, "m").test(st.body)) {
          ta.value = ta.value.replace(/\s*$/, "") + `\n\n${def}\n`; st.body = ta.value;
        }
        dlg.close(true);
        renderProps();
      } }, "Einfügen");
      dlg.box.append(h("p", { class: "okfw-note" }, "Setzt [^id] an die Schreibmarke und die Fußnote ans Ende. Die Quelle gehört in sources der Eigenschaften."),
        h("label", {}, "Quelle", sel), andere, h("label", {}, "Fundstelle", stelle), h("div", { class: "okfw-bar" }, ok));
      sel.focus();
    };
    const abbildung = () => {
      const dlg = openLayer("Abbildung einfügen");
      const name = h("input", { type: "text", id: "okfw-ab-name", placeholder: "z. B. schichtenmodell" });
      const titel = h("input", { type: "text", id: "okfw-ab-titel", placeholder: "Titel (Alternativtext)" });
      const unter = h("input", { type: "text", id: "okfw-ab-unter", placeholder: "Was die Abbildung zeigt" });
      const ids = quellenIds(st.felder);
      const beleg = h("select", {}, h("option", { value: "" }, "eigene Darstellung ohne Fußnote"), ids.map(i => h("option", { value: i }, `nach [^${i}]`)));
      const svg = h("textarea", { "aria-label": "SVG-Quelltext", rows: "10", placeholder: "<svg …>…</svg>" });
      const datei = h("input", { type: "file", accept: ".svg,image/svg+xml", "aria-label": "SVG-Datei wählen", onchange: async () => {
        const f = datei.files[0];
        if (!f) return;
        svg.value = await f.text();
        if (!name.value) name.value = f.name.replace(/\.svg$/i, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      } });
      const meldung = h("div", { "aria-live": "polite" });
      const ok = h("button", { class: "btn-primary", type: "button", onclick: async () => {
        const res = await api("api/abbildung", { name: name.value, svg: svg.value });
        if (!res.ok) { meldung.replaceChildren(h("div", { class: "okfw-err" }, res.data.fehler || "Fehler")); return; }
        const t = titel.value.trim() || res.data.name;
        einfuegen(`\n![${t}](${res.data.pfad})\n*Abb.: ${unter.value.trim() || t}${beleg.value ? `. Eigene Darstellung nach Müller[^${beleg.value}]` : ""}.*\n`);
        dlg.close(true);
        say(`Abbildung ${res.data.name} abgelegt. Ersetze in der Unterschrift den Platzhalter und prüfe die Fußnote.`);
      } }, "Ablegen und einfügen");
      dlg.box.append(h("p", { class: "okfw-note" }, "Das SVG wird vom Kern geprüft und bereinigt (keine Skripte, keine fremden Adressen) und unter abbildungen/ abgelegt."),
        h("label", {}, "Name der Datei", name), h("label", {}, "Titel", titel), h("label", {}, "Unterschrift", unter),
        h("label", {}, "Beleg", beleg), h("label", {}, "SVG-Datei", datei), h("label", {}, "oder SVG-Text", svg), meldung,
        h("div", { class: "okfw-bar" }, ok));
      name.focus();
    };

    // Vorschlag eines Modells
    const anw = h("textarea", { "aria-label": "Änderung beschreiben", rows: "3", placeholder: "Was soll sich an der Seite ändern? Zum Beispiel: Kürze den zweiten Abschnitt und nenne die Kernaussage zuerst." });
    const vorschlagBox = h("div", { "aria-live": "polite" });
    const holen = h("button", { class: "ghost", type: "button", onclick: async () => {
      if (!anw.value.trim()) { say("Beschreibe zuerst die Änderung.", "err"); anw.focus(); return; }
      const s = await api("api/einstellungen");
      if (!s.ok) return fail(s);
      const rolle = s.data.rollen.bearbeiten;
      if (rolle.gesperrt) { say(`Bearbeiten per Prompt ist nicht möglich: ${rolle.gesperrt}`, "err"); return; }
      const an = (s.data.anbieter[rolle.anbieter] || {}).name || rolle.anbieter;
      if (!await frage(`Der Text dieser Seite (${st.body.length} Zeichen, kein Volltext einer Quelle) geht an ${an}, Modell ${rolle.modell || "der Sitzung"}, über ${rolle.harness}. Datenklasse des Wikis: ${s.data.daten}.`, "Senden")) return;
      holen.disabled = true; holen.textContent = "Wird geholt …";
      vorschlagBox.replaceChildren(h("p", { class: "okfw-note" }, "Das Modell arbeitet. Das kann eine Weile dauern."));
      const res = await api("api/vorschlag", { id: st.id, anweisung: anw.value, body: st.body });
      holen.disabled = false; holen.textContent = "Vorschlag holen";
      if (!res.ok) { vorschlagBox.replaceChildren(h("div", { class: "okfw-err" }, res.data.fehler || "Fehler")); return; }
      const v = res.data;
      vorschlagBox.replaceChildren(
        h("p", { class: "okfw-note" }, `Vorschlag von ${v.rolle.modell} (${v.rolle.anbieter}, ${v.rolle.harness}). Gesendet wurde nur diese Seite, ${v.gesendet.zeichen} Zeichen. Gespeichert ist noch nichts.`),
        ...(v.warnungen.length ? [h("div", { class: "okfw-warn" }, h("strong", {}, "Prüfen"), h("ul", {}, v.warnungen.map(w => h("li", {}, w))))] : []),
        diffView(st.body, v.vorschlag),
        h("div", { class: "okfw-bar" },
          h("button", { class: "btn-primary", type: "button", onclick: () => { ta.value = st.body = v.vorschlag; st.vorschlag = true; ta.hidden = false; prev.hidden = true; toggle.textContent = "Vorschau"; vorschlagBox.replaceChildren(h("div", { class: "okfw-ok" }, "Übernommen ins Textfeld. Speichern ist ein eigener Schritt.")); } }, "Annehmen"),
          h("button", { class: "ghost", type: "button", onclick: () => vorschlagBox.replaceChildren() }, "Verwerfen")));
    } }, "Vorschlag holen");

    const inhalt = h("div", {},
      h("div", { class: "okfw-bar" }, toggle,
        h("button", { class: "ghost", type: "button", onclick: fussnote }, "Fußnote einfügen"),
        h("button", { class: "ghost", type: "button", onclick: abbildung }, "Abbildung einfügen")),
      ta, prev,
      h("h3", {}, "Änderung beschreiben"),
      h("p", { class: "okfw-note" }, "Ein Modell schlägt eine neue Fassung vor. Du siehst den Vergleich und entscheidest; es wird nur diese Seite gesendet."),
      anw, h("div", { class: "okfw-bar" }, holen), vorschlagBox);

    // Eigenschaften
    const props = h("div", {});
    function renderProps() {
      const keys = st.felder ? Object.keys(st.felder) : [];
      const segs = st.felder && fmMode === "felder" ? segmente(st.fm, keys) : null;
      if (fmMode === "felder" && !segs) fmMode = "text";
      const modeBtn = h("button", { class: "ghost", type: "button", onclick: () => { fmMode = fmMode === "felder" ? "text" : "felder"; renderProps(); } },
        fmMode === "felder" ? "Ganzes Frontmatter als Text" : "Zurück zu den Feldern");
      const kopf = [h("div", { class: "okfw-bar" }, modeBtn),
        h("p", { class: "okfw-note" }, "Geschützte Felder (status, verified, generated, darin der Prüfwert) ändert nur der Kern: Freigabe über „Freigeben“, generated beim Speichern.")];
      if (st.fehler) kopf.push(h("div", { class: "okfw-err" }, `Das Frontmatter ist nicht lesbar: ${st.fehler}. Bitte von Hand beheben.`));
      if (fmMode === "text") {
        const t = h("textarea", { class: "okfw-text", "aria-label": "Frontmatter als YAML-Text", spellcheck: "false", value: st.fm, readOnly: !!st.fehler,
          oninput: () => { st.fm = t.value; } });
        props.replaceChildren(...kopf, t);
        return;
      }
      const grid = h("div", { class: "okfw-props" });
      for (const s of segs) {
        const wert = st.felder[s.key];
        const gesperrt = geschuetzt.has(s.key);
        grid.append(h("div", { class: "k" + (gesperrt ? " locked" : "") }, s.key));
        const einzeilig = !gesperrt && ["string", "number", "boolean"].includes(typeof wert) && s.text.replace(/\n+$/, "").split("\n").length === 1;
        const set = text => { s.text = text; st.fm = zusammen(segs); };
        if (gesperrt) {
          grid.append(h("div", {}, h("textarea", { readOnly: true, rows: String(Math.min(6, s.text.split("\n").length)), "aria-label": `${s.key} (geschützt)`, value: s.text.slice(s.head.length).replace(/^\s+/, "") }),
            h("div", { class: "okfw-note" }, s.key === "status" ? "Setzt nur die Freigabe (und das Bearbeiten: stable fällt auf draft)." : s.key === "verified" ? "Prüfvermerke mit Prüfwert; schreibt nur der Kern." : "Zieht der Kern beim Speichern nach.")));
        } else if (einzeilig) {
          grid.append(h("input", { type: "text", "aria-label": s.key, value: String(wert), onchange: ev => set(`${s.head} ${feldSkalar(s.key, ev.target.value, wert)}\n`) }));
        } else {
          grid.append(h("textarea", { class: "okfw-prop", "aria-label": `${s.key} als YAML`, spellcheck: "false", value: s.text.replace(/\n+$/, ""),
            rows: String(Math.min(14, s.text.split("\n").length + 1)), onchange: ev => set(ev.target.value.replace(/\n*$/, "\n")) }));
        }
      }
      const nk = h("input", { type: "text", "aria-label": "Name des neuen Feldes", placeholder: "neues Feld" });
      const nv = h("input", { type: "text", "aria-label": "Wert des neuen Feldes", placeholder: "Wert" });
      const add = h("button", { class: "ghost", type: "button", onclick: () => {
        const k = nk.value.trim();
        if (!/^[A-Za-z_][\w-]*$/.test(k) || keys.includes(k)) { say("Feldname: Buchstaben, Ziffern, _ und -, noch nicht vorhanden.", "err"); return; }
        st.fm = (st.fm && !st.fm.endsWith("\n") ? st.fm + "\n" : st.fm) + `${k}: ${feldSkalar(k, nv.value, "")}\n`;
        st.felder = { ...st.felder, [k]: nv.value };
        renderProps();
      } }, "Feld hinzufügen");
      props.replaceChildren(...kopf, grid, h("h3", {}, "Neues Feld"), h("div", { class: "okfw-props" }, nk, h("div", {}, nv, h("div", { class: "okfw-bar" }, add))));
    }

    // Speichern, Befunde, Konflikt
    function befunde(d) {
      const teile = [];
      if (d.unveraendert) teile.push(h("div", { class: "okfw-ok" }, "Nichts geändert."));
      else teile.push(h("div", { class: "okfw-ok" }, "Gespeichert."));
      for (const hw of d.hinweise || []) teile.push(h("div", { class: "okfw-warn" }, hw));
      const list = (d.befunde || []).slice().sort((a, b) => STAND.indexOf(a.severity) - STAND.indexOf(b.severity));
      if (list.length) teile.push(h("div", { class: "okfw-warn" }, h("strong", {}, `Lint-Befunde zu dieser Seite (${list.length})`),
        h("ul", {}, list.map(f => h("li", { class: "okfw-find" }, h("span", { class: `sev ${f.severity}` }, f.severity), h("span", {}, `${f.code}: ${f.message}`))))));
      else teile.push(h("div", { class: "okfw-ok" }, "Keine Lint-Befunde zu dieser Seite."));
      msg.replaceChildren(...teile);
    }
    async function speichern() {
      const payload = { id: st.id, hash: st.hash };
      if (st.body !== st.body0) payload.body = st.body;
      if (st.fm !== st.fm0) payload.frontmatter = st.fm;
      if (payload.body === undefined && payload.frontmatter === undefined) { msg.replaceChildren(h("div", { class: "okfw-ok" }, "Nichts geändert.")); return; }
      if (st.vorschlag) payload.vorschlag = true;
      save.disabled = true;
      const res = await api("api/seite", payload);
      save.disabled = false;
      if (res.status === 409) return konflikt(res.data.aktuell, res.data.fehler);
      if (!res.ok) {
        msg.replaceChildren(h("div", { class: "okfw-err" }, res.data.fehler || "Fehler"));
        if (/Kennung/.test(res.data.fehler || "") && res.status === 422) openSettings("person");
        return;
      }
      const s = res.data.stand;
      Object.assign(st, { hash: s.hash, body0: s.body, body: s.body, fm0: s.frontmatter || "", fm: s.frontmatter || "", felder: s.felder, status: s.status, vorschlag: false });
      ta.value = st.body;
      reload = true;
      befunde(res.data);
      renderProps();
      statusZeigen();
    }
    function konflikt(aktuell, text) {
      const box = h("div", {});
      const meins = h("textarea", { readOnly: true, "aria-label": "Dein Stand", value: st.body, rows: "10" });
      const neu = h("textarea", { readOnly: true, "aria-label": "Aktueller Stand", value: aktuell.body, rows: "10" });
      box.append(h("div", { class: "okfw-err" }, h("strong", {}, "Die Seite wurde inzwischen geändert. "), text || ""),
        h("div", { class: "okfw-cols" }, h("label", {}, "Dein Stand", meins), h("label", {}, "Aktueller Stand", neu)),
        h("h3", {}, "Unterschied (aktuell → dein Stand)"), diffView(aktuell.body, st.body),
        h("div", { class: "okfw-bar" },
          h("button", { class: "btn-primary", type: "button", onclick: async () => {
            if (!await frage("Deine Änderungen verwerfen und den aktuellen Stand laden?", "Verwerfen")) return;
            Object.assign(st, { hash: aktuell.hash, body0: aktuell.body, body: aktuell.body, fm0: aktuell.frontmatter || "", fm: aktuell.frontmatter || "", felder: aktuell.felder, status: aktuell.status, vorschlag: false });
            ta.value = st.body; msg.replaceChildren(); renderProps(); statusZeigen(); reload = true;
          } }, "Aktuellen Stand laden"),
          h("button", { class: "ghost", type: "button", onclick: async () => {
            if (!await frage("Deinen Text über den aktuellen Stand der Seite speichern? Die zwischenzeitliche Änderung geht dabei verloren.", "Behalten")) return;
            const eigene = behalten(st, aktuell);
            renderProps();
            statusZeigen();
            msg.replaceChildren(h("div", { class: "okfw-warn" }, "Dein Text steht auf dem neuen Stand bereit. Mit Speichern überschreibst du die andere Änderung.",
              eigene ? " Auch deine geänderten Eigenschaften überschreiben dann die aktuellen; haben sich status oder generated geändert, lehnt der Server das Speichern ab, dann die Eigenschaften neu eintragen." : ""));
          } }, "Meinen Text behalten")));
      msg.replaceChildren(box);
    }

    const t = tabs([["inhalt", "Inhalt"], ["eigenschaften", "Eigenschaften"]], "inhalt", id => {
      inhalt.hidden = id !== "inhalt"; props.hidden = id !== "eigenschaften";
      if (id === "eigenschaften") renderProps();
    });
    const status = h("span", { class: "tag" });
    const statusHinweis = h("span", { class: "okfw-note" });
    function statusZeigen() {
      status.textContent = st.status || "ohne status";
      statusHinweis.textContent = st.status === "stable" ? "Diese Seite ist freigegeben: Eine Änderung am Inhalt setzt sie auf draft." : "";
    }
    statusZeigen();
    layer.head.insertBefore(status, layer.head.lastChild);
    layer.box.append(t.bar, inhalt, props, msg,
      h("div", { class: "okfw-bar" }, save, h("button", { class: "ghost", type: "button", onclick: () => layer.close(false) }, "Abbrechen"),
        h("span", { class: "sp" }), statusHinweis));
    t.start();
    ta.focus();
  }

  // ---------------------------------------------------------------- Freigabe
  async function freigeben(ids) {
    if (!ids.length) { say("Nichts markiert.", "err"); return; }
    const titel = ids.map(i => (node(i) || {}).title || i);
    if (!await frage(`Freigeben und auf stable setzen (${ids.length}):\n\n${titel.join("\n")}\n\nDamit bestätigst du, dass du den Inhalt geprüft hast. Der Kern lehnt Seiten ohne Gegenprüfung ab.`, "Freigeben")) return;
    const r = await api("api/freigabe", { ids });
    if (!r.ok) return fail(r);
    say(`${r.data.freigegeben.length} Seite(n) freigegeben.`);
    setTimeout(() => reloadTo(ids.length === 1 ? ids[0] : ""), 600);
  }

  function aktionen(id) {
    const n = node(id);
    if (!n) return null;
    return h("div", { class: "okfw-actions" },
      h("button", { class: "btn-primary", type: "button", onclick: () => openEditor(id) }, "Bearbeiten"),
      n.release === "valid" ? null : h("button", { class: "ghost", type: "button", title: "Als Mensch freigeben (status stable)", onclick: () => freigeben([id]) }, "Freigeben"));
  }

  // ---------------------------------------------------------------- Neue Karte
  const slugify = s => s.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  function neueKarte() {
    const layer = openLayer("Neue Karte");
    const typ = h("select", { id: "okfw-n-typ" }, data().types.map(t => h("option", { value: t.name }, `${t.label} (${t.name})`)));
    let eigen = false;
    const titel = h("input", { type: "text", id: "okfw-n-titel", oninput: () => { if (!eigen) slug.value = slugify(titel.value); } });
    const slug = h("input", { type: "text", id: "okfw-n-slug", oninput: () => { eigen = true; } });
    const beschr = h("textarea", { id: "okfw-n-beschr", rows: "3" });
    const aus = h("div", { "aria-live": "polite" });
    const ok = h("button", { class: "btn-primary", type: "button", onclick: async () => {
      const r = await api("api/neu", { typ: typ.value, slug: slug.value, titel: titel.value, beschreibung: beschr.value });
      if (!r.ok) { aus.replaceChildren(h("div", { class: "okfw-err" }, r.data.fehler || "Fehler")); if (/Kennung/.test(r.data.fehler || "")) openSettings("person"); return; }
      const list = r.data.befunde || [];
      aus.replaceChildren(h("div", { class: "okfw-ok" }, `Angelegt: ${r.data.id}`),
        list.length ? h("div", { class: "okfw-warn" }, h("strong", {}, "Noch offen laut Kern"), h("ul", {}, list.map(f => h("li", {}, `${f.code}: ${f.message}`)))) : null,
        h("div", { class: "okfw-bar" },
          h("button", { class: "btn-primary", type: "button", onclick: () => { layer.close(true); reloadTo(r.data.id, true); } }, "Karte bearbeiten"),
          h("button", { class: "ghost", type: "button", onclick: () => { layer.close(true); reloadTo(r.data.id, false); } }, "Karte ansehen")));
      ok.disabled = true;
    } }, "Anlegen");
    layer.box.append(h("p", { class: "okfw-note" }, "Legt die Karte wie der Befehl new an. Quellen mit DOI nimmt /recherche auf; hier entsteht eine Quellenkarte ohne Kennung."),
      h("label", {}, "Art", typ), h("label", {}, "Titel", titel), h("label", {}, "Kennung der Datei (kebab-case)", slug),
      h("label", {}, "Beschreibung", beschr), aus, h("div", { class: "okfw-bar" }, ok));
    titel.focus();
  }

  // ---------------------------------------------------------------- Einstellungen
  const KEY = { umgebung: "Key in der Umgebung", schluesselbund: "Key im Schlüsselbund", fehlt: "Key fehlt", keiner: "braucht keinen Key" };
  async function openSettings(start) {
    if (layers.some(l => l.el.dataset.okfw === "settings")) return;
    const r = await api("api/einstellungen");
    if (!r.ok) return fail(r);
    let S = r.data;
    const layer = openLayer("Einstellungen");
    layer.el.dataset.okfw = "settings";
    const body = h("div", {});
    const names = { lesen: "Lesen", gegenpruefen: "Gegenprüfen", bearbeiten: "Bearbeiten per Prompt", webabruf: "Webabruf" };
    let current = "rollen";
    const took = res => { if (!res.ok) { fail(res); return false; } S = res.data; show(current); return true; };

    function rollen() {
      const out = [h("p", {}, h("strong", {}, "Harness"), " ist der Weg, ", h("strong", {}, "Anbieter"), " die Abrechnung, ", h("strong", {}, "Modell"), " das Gehirn."),
        h("ul", { class: "okfw-note" }, Object.entries(S.harnesse_info).map(([k, v]) => h("li", {}, h("strong", {}, S.harnesse[k].name), ": ", v.kann, S.harnesse[k].zustand === "fehlt" ? ` Programm „${v.programm}“ nicht gefunden.` : ""))),
        h("p", { class: "okfw-note" }, `Datenklasse dieses Wikis: ${S.daten}${S.sperre.length ? "; gesperrt: " + S.sperre.join(", ") : ""}. Anbieter, die diese Klasse nicht verarbeiten dürfen, sind ausgegraut. Eine Claude-Code-Sitzung läuft von hier aus über: ${S.sitzung}.`)];
      if (S.warnungen.length) out.push(h("div", { class: "okfw-warn" }, S.warnungen.map(w => h("div", {}, w))));
      for (const rid of Object.keys(names)) {
        const ro = S.rollen[rid], web = rid === "webabruf";
        const hs = h("select", { id: `okfw-h-${rid}`, disabled: web }, Object.entries(S.harnesse).filter(([k]) => web ? k === "container" : k !== "container").map(([k, v]) => {
          const off = v.zustand === "fehlt" || (rid === "bearbeiten" && k === "sitzung");
          return h("option", { value: k, selected: k === ro.harness, disabled: off },
            v.name + (v.zustand === "fehlt" ? ` (nicht installiert: ${v.programm})` : rid === "bearbeiten" && k === "sitzung" ? " (der Server kann sie nicht bedienen)" : ""));
        }));
        const an = h("select", { id: `okfw-a-${rid}`, disabled: web });
        const mo = h("input", { type: "text", id: `okfw-m-${rid}`, value: ro.modell, list: `okfw-ml-${rid}`, placeholder: ro.harness === "sitzung" ? "(Modell der Sitzung)" : "Modellname", autocomplete: "off" });
        const dl = h("datalist", { id: `okfw-ml-${rid}` });
        const why = h("div", { class: "why okfw-note" });
        const fillAnbieter = () => {
          const formen = S.harnesse_info[hs.value].formen;
          an.replaceChildren(...Object.entries(S.anbieter).map(([k, a]) => {
            const passt = ["sitzung", "container"].includes(hs.value) || a.formen.some(f => formen.includes(f));
            const andereSitzung = hs.value === "sitzung" && k !== S.sitzung && k !== "anthropic";   // anthropic ist die Vorgabe und folgt der Sitzung
            const grund = !passt ? "passt nicht zur Harness" : andereSitzung ? `die Sitzung läuft über ${S.sitzung}; dafür opencode oder direkt wählen` : a.gesperrt ? "für die Datenklasse dieses Wikis gesperrt" : "";
            return h("option", { value: k, selected: k === ro.anbieter, disabled: !!grund && !web }, a.name + (grund && !web ? ` (${grund})` : ""));
          }));
          const wahl = an.selectedOptions[0];
          if (!wahl || wahl.disabled) { const frei = [...an.options].find(o => !o.disabled); if (frei) an.value = frei.value; }
        };
        fillAnbieter();
        hs.addEventListener("change", fillAnbieter);
        const laden = h("button", { class: "ghost", type: "button", onclick: async () => {
          laden.disabled = true; laden.textContent = "Lädt …";
          const m = await api(`api/modelle?anbieter=${encodeURIComponent(an.value)}`);
          laden.disabled = false; laden.textContent = "Modelle laden";
          if (!m.ok) { why.textContent = m.data.fehler || "Modellliste nicht erreichbar"; return; }
          dl.replaceChildren(...m.data.modelle.map(x => h("option", { value: x })));
          why.textContent = `${m.data.modelle.length} Modelle von ${S.anbieter[an.value].name}; Freitext bleibt möglich.`;
        } }, "Modelle laden");
        const sp = h("button", { class: "btn-primary", type: "button", onclick: async () => took(await api("api/einstellungen/rolle", { rolle: rid, harness: web ? "container" : hs.value, anbieter: an.value, modell: mo.value })) && say(`${names[rid]} gespeichert.`) }, "Speichern");
        const wiki = Object.entries(ro.herkunft || {}).filter(([, v]) => v === "wiki").map(([k]) => k);
        why.textContent = (ro.gesperrt ? `Gesperrt: ${ro.gesperrt} ` : "") + (ro.hinweis ? `${ro.hinweis} ` : "") + (wiki.length ? `Die wiki.toml legt ${wiki.join(", ")} fest und geht vor. ` : "") + (web ? "Beim Webabruf zählt nur das Modell; Harness und Anbieter sind fest. " : "");
        out.push(h("div", { class: "okfw-role" }, h("div", { class: "name" }, names[rid]),
          h("label", {}, "Harness", hs), h("label", {}, "Anbieter", an), h("label", {}, "Modell", mo, dl),
          h("div", { class: "okfw-actions" }, laden, sp), why));
      }
      return out;
    }

    function anbieter() {
      const out = [h("p", { class: "okfw-note" }, "Keys gibt es hier nie: Die Seite zeigt nur, ob einer da ist. Ablegen geschieht im eigenen Terminal.")];
      for (const [k, a] of Object.entries(S.anbieter)) {
        const forms = Object.entries(S.anbieter_formen[k]).map(([f, u]) => `${f === "openai" ? "OpenAI-kompatibel" : "Anthropic-kompatibel"}: ${u}`);
        const eigen = S.eigene.includes(k);
        out.push(h("div", { class: "okfw-card" }, h("h4", {}, `${a.name} (${k})`),
          h("div", { class: "okfw-note" }, forms.join(" · ")),
          h("div", {}, `${KEY[a.schluessel] || a.schluessel} · erlaubt für: ${S.anbieter_eigen_daten[k].join(", ")}${a.gesperrt ? " · für dieses Wiki gesperrt" : ""}`),
          a.hinweis ? h("div", { class: "okfw-note" }, a.hinweis) : null,
          a.befehl ? (() => { const c = h("button", { class: "ghost", type: "button", onclick: () => copyText(a.befehl, c) }, "Befehl kopieren");
            return h("div", {}, h("div", { class: "okfw-note" }, "Im eigenen Terminal ablegen:"), h("div", { class: "okfw-cmd" }, h("code", {}, a.befehl), c)); })() : null,
          eigen ? h("div", { class: "okfw-actions" }, h("button", { class: "ghost okfw-danger", type: "button", onclick: async () => { if (await frage(`Anbieter ${k} entfernen?`, "Entfernen")) took(await api("api/einstellungen/anbieter", { id: k, entfernen: true })); } }, "Entfernen")) : null));
      }
      const f = {};
      const feld = (id, label, extra) => (f[id] = h("input", { type: "text", id: `okfw-e-${id}`, autocomplete: "off", ...(extra || {}) }), h("label", {}, label, f[id]));
      const klassen = S.datenklassen.map(d => h("label", { class: "okfw-check" }, h("input", { type: "checkbox", value: d, checked: d !== "personenbezogen" }), d));
      out.push(h("h3", {}, "Eigener Endpunkt"),
        h("p", { class: "okfw-note" }, "Für jeden Dienst mit OpenAI- oder Anthropic-kompatibler Schnittstelle. Adresse mit https (nur auf diesem Rechner auch http)."),
        feld("name", "Name"), feld("openai", "OpenAI-kompatible Adresse (endet meist auf /v1)", { placeholder: "https://…/v1" }),
        feld("anthropic", "Anthropic-kompatible Adresse", { placeholder: "https://…" }),
        feld("schluessel", "Name des Schlüsselbund-Eintrags (nicht der Key)"), feld("schluessel_env", "Name der Umgebungsvariable (nicht der Key)", { placeholder: "MEIN_DIENST_KEY" }),
        h("fieldset", {}, h("legend", { class: "okfw-note" }, "Erlaubte Datenklassen"), klassen),
        h("div", { class: "okfw-actions" }, h("button", { class: "btn-primary", type: "button", onclick: async () => {
          const id = slugify(f.name.value);
          const res = await api("api/einstellungen/anbieter", { id, name: f.name.value, openai: f.openai.value, anthropic: f.anthropic.value,
            schluessel: f.schluessel.value, schluessel_env: f.schluessel_env.value, daten: klassen.map(l => l.firstChild).filter(c => c.checked).map(c => c.value) });
          if (took(res)) say(`Anbieter ${id} gespeichert. Den Key legst du im Terminal ab.`);
        } }, "Anbieter speichern")));
      return out;
    }

    function person() {
      const i = h("input", { type: "text", id: "okfw-p", value: S.person, autocomplete: "off" });
      return [h("p", {}, "Die Kennung erscheint als ", h("code", {}, "human:<kennung>"), " bei jeder Änderung und Freigabe in diesem Dashboard (Ereignisse, generated, verified)."),
        h("label", {}, "Kennung (Buchstaben, Ziffern, Punkt, Bindestrich, Unterstrich)", i),
        h("div", { class: "okfw-actions" }, h("button", { class: "btn-primary", type: "button", onclick: async () => took(await api("api/einstellungen/person", { kennung: i.value })) && say("Kennung gespeichert.") }, "Speichern")),
        h("p", { class: "okfw-note" }, `Gespeichert in ${S.datei}`)];
    }

    function design() {
      const box = h("div", {}, h("p", { class: "okfw-note" }, "Lädt …"));
      api("api/design").then(res => {
        if (!res.ok) { box.replaceChildren(h("div", { class: "okfw-err" }, res.data.fehler)); return; }
        const d = res.data;
        const css = h("textarea", { class: "okfw-text", "aria-label": `Inhalt von ${d.datei}`, spellcheck: "false", value: d.css || "", rows: "14",
          placeholder: ":root {\n  --accent: #2a78d6;\n}\n:root[data-theme=\"dark\"] {\n  --accent: #3987e5;\n}" });
        const einsetzen = (selector, name, wert) => {
          const re = selector === ":root" ? /(^|\n):root\s*\{([^}]*)\}/ : /:root\[data-theme="dark"\]\s*\{([^}]*)\}/;
          const zeile = `  ${name}: ${wert};\n`;
          const m = css.value.match(re);
          if (m) { const end = m.index + m[0].length - 1; css.value = css.value.slice(0, end).replace(/\s*$/, "\n") + zeile + css.value.slice(end); }
          else css.value = (css.value ? css.value.replace(/\s*$/, "\n\n") : "") + `${selector} {\n${zeile}}\n`;
        };
        const farbe = v => /^(#[0-9a-f]{3,8}|rgba?\()/i.test(v);
        const rows = d.variablen.map(v => h("tr", {}, h("td", {}, h("code", {}, v.name)), h("td", {}, v.bedeutung),
          h("td", { class: "sw" }, farbe(v.hell) ? h("span", { style: `background:${v.hell}`, title: v.hell }) : null, " ", h("span", { class: "okfw-note" }, farbe(v.hell) ? "" : v.hell.slice(0, 24))),
          h("td", {}, h("button", { class: "ghost", type: "button", onclick: () => einsetzen(":root", v.name, v.hell) }, "hell"), " ",
            h("button", { class: "ghost", type: "button", onclick: () => einsetzen(":root[data-theme=\"dark\"]", v.name, v.dunkel) }, "dunkel"))));
        const meld = h("div", { "aria-live": "polite" });
        box.replaceChildren(
          h("p", {}, `Eigene Gestaltung: ${d.datei}${d.vorhanden ? "" : " (gibt es noch nicht)"}. Nur Variablen und Regeln; @import und url() auf Fremdes lehnt der Server ab (wie der Lint design_unsafe).`),
          css, meld,
          h("div", { class: "okfw-actions" }, h("button", { class: "btn-primary", type: "button", onclick: async () => {
            const s = await api("api/design", { css: css.value });
            if (!s.ok) { meld.replaceChildren(h("div", { class: "okfw-err" }, s.data.fehler)); return; }
            location.reload();
          } }, "Speichern und neu laden")),
          h("h3", {}, "Variablen der Vorlage"),
          h("div", { style: "overflow-x:auto" }, h("table", { class: "okfw-table okfw-vars" }, h("thead", {}, h("tr", {}, ["Name", "Bedeutung", "Hell", "Einsetzen"].map(x => h("th", { scope: "col" }, x)))), h("tbody", {}, rows))));
      });
      return [box];
    }

    const reiter = [["rollen", "Rollen"], ["anbieter", "Anbieter"], ["person", "Person"], ["design", "Design"]];
    const views = { rollen, anbieter, person, design };
    const inhalt = h("div", {});
    function show(id) { current = id; inhalt.replaceChildren(...views[id]()); }
    const t = tabs(reiter, reiter.some(([k]) => k === start) ? start : "rollen", show);
    layer.box.append(t.bar, inhalt);
    t.start();
  }

  // ---------------------------------------------------------------- Andockpunkte der Vorlage
  function sicher(fn) { return (...a) => { try { fn(...a); } catch (e) { console.error("okfw serve:", e); } }; }
  window.okfwEdit = {
    reader: sicher((card, id) => {
      if (!id) return;
      const a = aktionen(id), head = card.querySelector(".r-head");
      if (a && head) head.append(a);
    }),
    panel: sicher((panel, id) => { const a = aktionen(id); if (a) panel.append(a); }),
    release: sicher((body, ids) => {
      if (!ids.length) return;
      const marked = () => [...body.querySelectorAll("li[data-page]")].filter(li => li.querySelector("input[type=checkbox]:checked")).map(li => li.dataset.page);
      const bar = h("div", { class: "okfw-actions" },
        h("button", { class: "btn-primary", type: "button", onclick: () => freigeben(marked()) }, "Markierte freigeben"),
        h("button", { class: "ghost", type: "button", onclick: () => freigeben(ids) }, `Alle freigeben (${ids.length})`));
      body.insertBefore(bar, body.children[1] || null);
      for (const li of body.querySelectorAll("li[data-page]")) {
        const head = li.querySelector(".rel-head");
        if (head) head.append(h("button", { class: "ghost", type: "button", onclick: () => freigeben([li.dataset.page]) }, "Freigeben"));
      }
    }),
    ready: sicher(kern => {
      let id = "", edit = "";
      try { id = sessionStorage.getItem("okfw-open") || ""; edit = sessionStorage.getItem("okfw-edit") || ""; sessionStorage.removeItem("okfw-open"); sessionStorage.removeItem("okfw-edit"); } catch (e) { /* ohne Speicher */ }
      if (id && kern.open(id) && edit) openEditor(id);
    }),
  };

  function werkzeuge() {
    const top = document.querySelector("header.top > div");
    if (!top || document.querySelector(".okfw-tools")) return;
    top.append(h("div", { class: "okfw-tools" },
      h("span", { class: "okfw-badge", title: "Dieses Dashboard läuft mit okfw serve: Karten sind bearbeitbar. Die Dashboard-Datei zum Weitergeben bleibt eine Leseansicht." }, "Bearbeiten"),
      h("button", { class: "btn-primary", type: "button", onclick: neueKarte }, "Neue Karte"),
      h("button", { class: "ghost", type: "button", onclick: () => openSettings("rollen") }, "Einstellungen")));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", werkzeuge); else werkzeuge();
})();
