// PDF als Text für `web-recherche --pdf`: kein Modell, kein Token, keine Shell, nur dieses Programm.
//
//   HTTPS_PROXY=http://proxy:3128 MAX_BYTES=204800 node pdf-text.mjs https://example.org/a.pdf …
//
// Regeln (bewusst eng):
// - einziger Weg nach draußen ist der Proxy; welche Domain erreichbar ist, entscheidet nur er, auch bei
//   Weiterleitungen. TLS bleibt Ende-zu-Ende, die Zertifikate prüft dieses Programm.
// - nur https; Größe des PDFs, Seitenzahl und Länge des Texts sind begrenzt
// - pdf.js parst in JavaScript ohne nativen Code, führt keine Skripte aus und lädt nichts nach
// - /ausgabe/ergebnis.md entsteht erst, wenn alle PDFs gelesen sind; sonst bleibt der Ordner leer
// - jedes # aus dem PDF wird zu \#, damit ein Dokument keine Seitenmarke nachbauen kann
// - Bericht als JSON-Zeilen auf stdout: Zahlen, Prüfsumme, URLs und feste Fehlertexte, nie Text aus dem PDF

import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import tls from "node:tls";
import { fileURLToPath } from "node:url";

const PDFJS = "/usr/local/lib/node_modules/pdfjs-dist";
const MAX_PDF_BYTES = 64 * 1024 * 1024;
const MAX_SEITEN = 5000;
const MAX_WEITERLEITUNGEN = 5;
const LEERLAUF_MS = 60_000;
const AUSGABE = "/ausgabe/ergebnis.md";
const KEIN_TEXT = "[Kein Text auf dieser Seite, vielleicht Bild oder Scan]";

/** Fehler mit festem, von uns formuliertem Text. Alles andere wird nur mit dem Typnamen gemeldet. */
class Fehlschlag extends Error {}

function tunnel(host) {
  const proxy = new URL(process.env.HTTPS_PROXY);
  return new Promise((ok, nein) => {
    const req = http.request({ host: proxy.hostname, port: proxy.port, method: "CONNECT", path: `${host}:443` });
    req.setTimeout(LEERLAUF_MS, () => req.destroy(new Fehlschlag("Zeitüberschreitung am Proxy")));
    req.on("connect", (res, socket) => {
      if (res.statusCode === 200) return ok(socket);
      socket.destroy();
      nein(new Fehlschlag(`Proxy lehnt ${host} ab (HTTP ${res.statusCode})`));
    });
    req.on("error", () => nein(new Fehlschlag("Proxy nicht erreichbar")));
    req.end();
  });
}

export async function holen(url, tiefe = 0) {
  const ziel = new URL(url);
  if (ziel.protocol !== "https:" || ziel.username || ziel.password || (ziel.port && ziel.port !== "443")) {
    throw new Fehlschlag("Nur https auf Port 443 ohne Zugangsdaten");
  }
  const socket = await tunnel(ziel.hostname);
  return new Promise((ok, nein_roh) => {
    const nein = (e) => nein_roh(e instanceof Fehlschlag ? e : new Fehlschlag(`Abruf gescheitert (${e?.code || e?.name})`));
    const req = https.get(ziel, {
      createConnection: () => tls.connect({ socket, servername: ziel.hostname }),
      headers: { "User-Agent": "web-zugriff-pdf", Accept: "application/pdf,*/*;q=0.1", "Accept-Encoding": "identity" },
    }, (res) => {
      const status = res.statusCode;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location) {
        res.destroy();
        if (tiefe >= MAX_WEITERLEITUNGEN) return nein(new Fehlschlag("Zu viele Weiterleitungen"));
        return ok(holen(new URL(res.headers.location, ziel).href, tiefe + 1));
      }
      if (status !== 200) {
        res.destroy();
        return nein(new Fehlschlag(`Server antwortet mit HTTP ${status}`));
      }
      const teile = [];
      let groesse = 0;
      res.on("data", (d) => {
        groesse += d.length;
        if (groesse > MAX_PDF_BYTES) return res.destroy(new Fehlschlag("PDF größer als 64 MB"));
        teile.push(d);
      });
      res.on("end", () => res.complete ? ok({ url: ziel.href, daten: Buffer.concat(teile) })
        : nein(new Fehlschlag("Übertragung abgebrochen")));
      res.on("error", nein);
    });
    req.setTimeout(LEERLAUF_MS, () => req.destroy(new Fehlschlag("Zeitüberschreitung beim Abruf")));
    req.on("error", nein);
  });
}

/** Zeilen und Absätze aus den Textstücken einer Seite, in der Reihenfolge des Dokuments. */
export function seiteZuText(stuecke) {
  let text = "", letztesY = null;
  for (const s of stuecke) {
    if (typeof s.str !== "string") continue;
    const y = s.transform[5], hoehe = s.height || Math.abs(s.transform[3]) || 1;
    if (letztesY !== null && Math.abs(y - letztesY) > hoehe * 0.6) {
      if (!text.endsWith("\n")) text += "\n";
      if (Math.abs(y - letztesY) > hoehe * 1.8) text += "\n";
    }
    text += s.str;
    if (s.hasEOL && !text.endsWith("\n")) text += "\n";
    if (s.str.trim()) letztesY = y;
  }
  return text.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

export async function seitenLesen(daten) {
  // pdf.js fragt beim Laden nach DOMMatrix; gebraucht wird es nur beim Zeichnen, das hier nie geschieht.
  globalThis.DOMMatrix ??= class {};
  const { getDocument, VerbosityLevel } = await import(`${PDFJS}/legacy/build/pdf.mjs`);
  const aufgabe = getDocument({
    data: new Uint8Array(daten), isEvalSupported: false, disableFontFace: true, useSystemFonts: false,
    enableXfa: false, verbosity: VerbosityLevel.ERRORS, cMapUrl: `${PDFJS}/cmaps/`, cMapPacked: true,
  });
  const pdf = await aufgabe.promise;
  if (pdf.numPages > MAX_SEITEN) throw new Fehlschlag(`Mehr als ${MAX_SEITEN} Seiten`);
  const seiten = [];
  for (let n = 1; n <= pdf.numPages; n += 1) {
    const seite = await pdf.getPage(n);
    seiten.push(seiteZuText((await seite.getTextContent()).items));
    seite.cleanup();
  }
  await aufgabe.destroy();
  return seiten;
}

/** Abschnitt eines Dokuments: Kopf mit URL, Seitenzahl und Prüfsumme, dann jede Seite unter ihrer Marke. */
export function alsMarkdown(nr, anzahl, url, sha256, seiten) {
  const ohneText = seiten.flatMap((t, i) => (t ? [] : [i + 1]));
  const zeilen = [`# PDF ${nr} von ${anzahl}`, "", `URL: ${url}`, `SHA-256: ${sha256}`,
    `Seiten: ${seiten.length}, ohne Text: ${ohneText.length ? ohneText.join(", ") : "keine"}`, ""];
  seiten.forEach((t, i) => zeilen.push(`## Seite ${i + 1} von ${seiten.length}`, "",
    t ? t.replaceAll("#", "\\#") : KEIN_TEXT, ""));
  return zeilen.join("\n");
}

function melden(eintrag) {
  process.stdout.write(JSON.stringify(eintrag) + "\n");
}

export async function dokumentLesen(url, nr, anzahl) {
  const { url: endgueltig, daten } = await holen(url);
  if (!daten.subarray(0, 1024).includes("%PDF-")) {
    throw new Fehlschlag("Antwort ist kein PDF (keine %PDF-Kennung am Anfang)");
  }
  const sha256 = createHash("sha256").update(daten).digest("hex");
  const seiten = await seitenLesen(daten);
  melden({ art: "pdf", url, endgueltige_url: endgueltig, sha256, bytes: daten.length, seiten: seiten.length,
    ohne_text: seiten.flatMap((t, i) => (t ? [] : [i + 1])) });
  return alsMarkdown(nr, anzahl, endgueltig, sha256, seiten);
}

async function main() {
  const urls = process.argv.slice(2);
  const maxBytes = Number(process.env.MAX_BYTES) || 200 * 1024;
  const teile = [];
  for (const [i, url] of urls.entries()) {
    try {
      teile.push(await dokumentLesen(url, i + 1, urls.length));
    } catch (e) {
      melden({ art: "fehler", url, grund: e instanceof Fehlschlag ? e.message : `PDF nicht lesbar (${e?.name || "Fehler"})` });
      process.exit(1);
    }
    if (Buffer.byteLength(teile.join("\n")) > maxBytes) {
      melden({ art: "fehler", url, grund: `Text größer als das Limit von ${maxBytes} Bytes (--max-kb erhöhen)` });
      process.exit(1);
    }
  }
  writeFileSync(AUSGABE, teile.join("\n"), { mode: 0o600 });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
