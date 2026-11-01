// Netz-Sperre für den web-zugriff-Agenten: HTTPS-Proxy mit Domain-Freigabeliste.
//
// Läuft in einem eigenen Container, der als einziger sowohl im internen Lauf-Netz (ohne Ausgang)
// als auch im Bridge-Netz hängt. Der Agent-Container erreicht das Internet nur über diesen Proxy.
//
//   ERLAUBT="api.anthropic.com,example.org,*.example.net" node proxy.mjs
//
// Regeln (bewusst eng):
// - nur CONNECT auf Port 443 (HTTPS); einfache HTTP-Anfragen werden abgelehnt
// - "example.org" erlaubt example.org und www.example.org; "*.example.org" zusätzlich alle Subdomains
// - keine IP-Adressen als Ziel; Namen, die auf private, lokale oder reservierte Adressen zeigen,
//   werden abgelehnt (kein Zugriff auf Host, OrbStack oder das LAN)
// - Verbindung geht an die geprüfte IP, nicht an eine zweite DNS-Antwort (kein DNS-Rebinding)
// - jede Entscheidung als JSON-Zeile auf stdout (der Wrapper übernimmt sie ins Protokoll)
// Der Proxy sieht nur Hostnamen (TLS bleibt Ende-zu-Ende), keine Pfade und keine Inhalte.

import dns from "node:dns/promises";
import http from "node:http";
import net from "node:net";

const PORT = 3128;
const MAX_VERBINDUNGEN = 32;
const LEERLAUF_MS = 120_000;

const ERLAUBT = (process.env.ERLAUBT || "")
  .split(",").map((d) => d.trim().toLowerCase()).filter(Boolean);
if (!ERLAUBT.length) {
  console.error("ERLAUBT ist leer");
  process.exit(2);
}

const GESPERRT = new net.BlockList();
for (const [netz, bits, typ] of [
  ["0.0.0.0", 8, "ipv4"], ["10.0.0.0", 8, "ipv4"], ["100.64.0.0", 10, "ipv4"],
  ["127.0.0.0", 8, "ipv4"], ["169.254.0.0", 16, "ipv4"], ["172.16.0.0", 12, "ipv4"],
  ["192.0.0.0", 24, "ipv4"], ["192.168.0.0", 16, "ipv4"], ["198.18.0.0", 15, "ipv4"],
  ["224.0.0.0", 3, "ipv4"],
  ["::", 128, "ipv6"], ["::1", 128, "ipv6"], ["64:ff9b::", 96, "ipv6"],
  ["fc00::", 7, "ipv6"], ["fe80::", 10, "ipv6"], ["ff00::", 8, "ipv6"],
]) GESPERRT.addSubnet(netz, bits, typ);
// ::ffff:a.b.c.d prüft BlockList selbst gegen die IPv4-Regeln; eine Regel ::ffff:0:0/96 würde
// dagegen jede IPv4-Adresse sperren.

function freigegeben(host) {
  return ERLAUBT.some((eintrag) => {
    if (eintrag.startsWith("*.")) {
      const basis = eintrag.slice(2);
      return host === basis || host.endsWith("." + basis);
    }
    return host === eintrag || host === "www." + eintrag;
  });
}

function log(eintrag) {
  process.stdout.write(JSON.stringify({ zeit: new Date().toISOString(), ...eintrag }) + "\n");
}

let offen = 0;
const server = http.createServer((req, res) => {
  log({ art: "http", ziel: String(req.url).slice(0, 200), entscheidung: "abgelehnt", grund: "nur HTTPS (CONNECT)" });
  res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("web-zugriff-proxy: nur HTTPS über CONNECT\n");
});

server.on("connect", async (req, client, kopf) => {
  const ziel = String(req.url || "");
  const ablehnen = (grund, code = 403) => {
    log({ art: "connect", ziel: ziel.slice(0, 200), entscheidung: "abgelehnt", grund });
    client.end(`HTTP/1.1 ${code} Forbidden\r\nContent-Type: text/plain\r\n\r\nweb-zugriff-proxy: ${grund}\r\n`);
  };
  client.on("error", () => {});

  const treffer = /^([^:\s]+):(\d+)$/.exec(ziel);
  if (!treffer) return ablehnen("ungültiges Ziel");
  const host = treffer[1].toLowerCase().replace(/\.$/, "");
  const port = Number(treffer[2]);
  if (port !== 443) return ablehnen("nur Port 443");
  if (net.isIP(host) || host.startsWith("[")) return ablehnen("IP-Adresse statt Name");
  if (host.length > 253 || !/^[a-z0-9.-]+$/.test(host)) return ablehnen("ungültiger Hostname");
  if (!freigegeben(host)) return ablehnen("Domain nicht freigegeben");
  if (offen >= MAX_VERBINDUNGEN) return ablehnen("zu viele Verbindungen", 429);

  let adressen;
  try {
    adressen = await dns.lookup(host, { all: true });
  } catch {
    return ablehnen("DNS-Fehler", 502);
  }
  const gesperrt = adressen.find((a) => GESPERRT.check(a.address, a.family === 6 ? "ipv6" : "ipv4"));
  if (!adressen.length) return ablehnen("DNS ohne Antwort", 502);
  if (gesperrt) return ablehnen(`Name zeigt auf private oder reservierte Adresse (${gesperrt.address})`);
  const ip = (adressen.find((a) => a.family === 4) || adressen[0]).address;

  offen += 1;
  let hoch = 0, runter = 0;
  const upstream = net.connect({ host: ip, port });
  const schluss = (() => {
    let fertig = false;
    return (grund) => {
      if (fertig) return;
      fertig = true;
      offen -= 1;
      log({ art: "connect", ziel: `${host}:${port}`, ip, entscheidung: "erlaubt", bytes_hoch: hoch, bytes_runter: runter, ende: grund });
      client.destroy();
      upstream.destroy();
    };
  })();
  upstream.setTimeout(LEERLAUF_MS, () => schluss("Leerlauf"));
  client.setTimeout(LEERLAUF_MS, () => schluss("Leerlauf"));
  upstream.on("error", () => schluss("Fehler Ziel"));
  client.on("error", () => schluss("Fehler Client"));
  upstream.on("close", () => schluss("geschlossen"));
  client.on("close", () => schluss("geschlossen"));
  upstream.on("connect", () => {
    client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    if (kopf && kopf.length) upstream.write(kopf);
    client.on("data", (d) => { hoch += d.length; });
    upstream.on("data", (d) => { runter += d.length; });
    client.pipe(upstream);
    upstream.pipe(client);
  });
});

server.listen(PORT, "0.0.0.0", () => log({ art: "start", erlaubt: ERLAUBT }));
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => process.exit(0));
