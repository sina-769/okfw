#!/usr/bin/env bash
# Nachbau-Test für das Claude-Code-Plugin okfw, für eine frische Umgebung (leerer Container oder leeres HOME).
#
#   nachbau.sh <marketplace-quelle> <ausgabeordner>
#
# <marketplace-quelle> ist alles, was `claude plugin marketplace add` versteht (Ordner mit
# .claude-plugin/marketplace.json, Git-URL, owner/repo). Der Test installiert das Plugin daraus, legt
# ein Demo-Wiki aus zwei frei lizenzierten Quellen an (OKF-v0.2-Spezifikation, Apache-2.0; README von
# pi-llm-wiki, MIT), erfasst beide als Rohquellen, schreibt Karten, führt rebuild, lint und dashboard aus,
# prüft die Freigabe (Ablehnung ohne Gegenprüfung, Rückfall nach body) und ruft die Hooks so auf, wie Claude Code sie aus hooks/hooks.json startet.
# Seit 0.6.0 prüft er außerdem den Inhalt des einen Plugins (Skills, Agenten, nichts aus private/), rollen.py ohne Einrichtung
# (Standardrollen, Aufträge nur mit wiki.toml), recherche/scripts/api.py, die Bereinigung einer SVG-Abbildung, das Dashboard einer
# Seite mit `{: …}` (CSP, kein onclick) und okfw serve (Pfadpräfix, Token, Cookie, fremde Pfade 404, nichts davon in der Datei
# von dashboard).
# Jeder Schritt steht mit Befehl, Ausgabe und Exit-Code in <ausgabeordner>/protokoll.md.
# Optional: QUELLE_HINWEIS (Herkunft der Quelle, Freitext fürs Protokoll) und QUELLE_TREE (erwarteter Git-Tree des Plugin-Ordners).
#
# Braucht: bash, git, curl, claude (Claude Code), uv. Netz: GitHub-API (Quellen), PyPI und die
# Python-Downloads von uv (beim ersten Start des Kerns). Kein Login bei Claude nötig; nur die Schritte mit einer echten
# Claude-Sitzung brauchen CLAUDE_CODE_OAUTH_TOKEN. Fehlt er, stehen sie als übersprungen im Protokoll, das Protokoll
# als „Unvollständig“ und die Ausgabe mit UNVOLLSTÄNDIG; für einen Release gehört der Token dazu.
set -u

SRC=${1:?Marketplace-Quelle fehlt}
OUT=${2:?Ausgabeordner fehlt}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
LOG="$OUT/protokoll.md"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/nachbau.XXXXXX") || { echo "kein Arbeitsordner" >&2; exit 2; }
DEMO="$WORK/demo-wiki"
DL="$WORK/downloads"
LEER="$WORK/ohne-wiki"
mkdir -p "$DEMO" "$DL" "$LEER"
N=0; OK=0; FAIL=0; SKIP=0

# Quellen, gepinnt auf Commit; SHA-256 der Dateien zum Abgleich
OKF_REPO=GoogleCloudPlatform/knowledge-catalog
OKF_COMMIT=a8291a1a656669b8766fd4ff0f985505fb955e94
OKF_PATH=okf/SPEC.md
OKF_SHA=26aa5da029278939f914e578107242d9607d4f2dc5fe153272b82f9ed1030101
PI_REPO=zosmaai/pi-llm-wiki
PI_COMMIT=46bd677041ba01f0298e5afc22e6a8b592f5f835
PI_PATH=README.md
PI_SHA=cac7184ae31a6bd881e5362326200c899f8d58487134d28e1f11d5f0bc4466c8
# Commit-Datum (GitHub-API, committer.date): OKF 2026-10-24, pi-llm-wiki 2026-10-09 → date der Quellenkarten

log() { printf '%s\n' "$*" >>"$LOG"; }
# sha_ok <sha256> <datei>: Prüfsumme vergleichen (GNU sha256sum oder shasum)
sha_of() { if sha256sum --version >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
sha_ok() { [ "$(sha_of "$2")" = "$1" ] && echo "$2: SHA-256 $1 stimmt"; }
export -f sha_of sha_ok

# step <titel> <befehl als Zeichenkette>: läuft in bash, Exit 0 = bestanden
step() {
  local title=$1 cmd=$2 out rc
  N=$((N + 1))
  out=$(cd "${CWD:-$WORK}" && bash -c "$cmd" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then OK=$((OK + 1)); res="bestanden"; else FAIL=$((FAIL + 1)); res="**FEHLGESCHLAGEN**"; fi
  log "### $N. $title"
  log ""
  log "\`$(date -u +%H:%M:%SZ)\` · Exit $rc · $res"
  log ""
  log '```text'
  log "\$ ${cmd//$WORK/\$WORK}"
  printf '%s\n' "$out" | sed "s#$WORK#\$WORK#g" | head -n "${MAXLINES:-40}" >>"$LOG"
  [ "$(printf '%s\n' "$out" | wc -l)" -gt "${MAXLINES:-40}" ] && log "… (gekürzt)"
  log '```'
  log ""
  printf '%-4s %-20s %s\n' "$N." "$res" "$title"
}

skip() {
  N=$((N + 1)); SKIP=$((SKIP + 1))
  log "### $N. $1"; log ""; log "übersprungen: $2"; log ""
  printf '%-4s %-20s %s\n' "$N." "übersprungen" "$1"
}

: >"$LOG"
log "# Nachbau-Test okfw"
log ""
log "Beginn: $(date -u +%Y-%m-%dT%H:%M:%SZ) · Marketplace-Quelle: \`$SRC\`${REPO_COMMIT:+ · Repo-Commit: \`$REPO_COMMIT\`}"
[ -n "${QUELLE_HINWEIS:-}" ] && { log ""; log "Quelle: $QUELLE_HINWEIS"; }
log ""
log "## Umgebung"
log ""

step "Werkzeuge und leere Ausgangslage" '
uname -srm; echo "HOME=$HOME"
echo "claude $(claude --version)"; uv --version; git --version | head -1
command -v python3 >/dev/null && echo "python3: $(python3 --version)" || echo "python3: keins im PATH"
test ! -e "$HOME/.claude/plugins/installed_plugins.json" && echo "keine Plugins installiert"
test ! -e "$HOME/.claude/skills/okfw" && echo "kein Skill-Ordner okfw"'

log "## Installation"
log ""
step "Marketplace hinzufügen" "claude plugin marketplace add '$SRC'"
step "Plugin installieren" "claude plugin install okfw@okfw"
step "Bestandteile des installierten Plugins" "claude plugin details okfw@okfw | sed -n '1,12p'"
P=$(grep -o '"installPath": *"[^"]*okfw[^"]*"' "$HOME/.claude/plugins/installed_plugins.json" 2>/dev/null \
  | head -1 | sed 's/.*: *"//; s/"$//')
K="$P/skills/okfw/okfw.py"
H="$P/skills/okfw/hooks/okf_hook.py"
step "Installierte Dateien (Plugin-Cache)" "test -n '$P' && cd '$P' && find . -type f | sort && cat .claude-plugin/plugin.json"
export K H P WORK DEMO
if [ -n "${QUELLE_TREE:-}" ]; then
step "Quelle ist der erwartete Stand (Git-Tree des Plugin-Ordners)" '
tree=$(git -C "$HOME/checkout" rev-parse "HEAD^{tree}" 2>/dev/null || true)
echo "Tree im Klon: ${tree:-unbekannt}"; echo "erwartet:     ${QUELLE_TREE:-nicht angegeben}"
test -n "${QUELLE_TREE:-}" && test "$tree" = "$QUELLE_TREE"'
fi
step "0.6.0: Das eine Plugin enthält vier Skills und drei Agenten, nichts aus private/" '
cd "$P" || exit 1
for d in skills/okfw skills/recherche skills/lesen skills/web-zugriff; do test -f "$d/SKILL.md" && echo "vorhanden: $d/SKILL.md"; done
echo "Skills: $(ls skills | tr "\n" " ")"
echo "Agenten: $(ls agents | tr "\n" " ")"
for a in beleg-pruefer fund-api fund-tooluniverse; do test -f "agents/$a.md" && echo "vorhanden: agents/$a.md"; done
test "$(ls skills | wc -l)" -eq 4 && test "$(ls agents | wc -l)" -eq 3 || exit 1
n=$(find . -ipath "*private*" | wc -l); echo "Pfade mit private: $n"; test "$n" -eq 0'
step "Manifest streng prüfen" "claude plugin validate --strict '$P/.claude-plugin/plugin.json'"

log "## Demo-Wiki"
log ""
CWD=$DEMO
step "Kern starten (uv holt Python ≥ 3.11, PyYAML, Markdown)" "uv run --no-config --quiet '$K' --version"
step "Python, das uv dafür nutzt" "uv python find && uv python list --only-installed"
step "Wiki anlegen (Profil default)" "uv run --no-config --quiet '$K' init --profile default && ls -a && cat wiki/.events.jsonl"

fetch() { # fetch <repo> <commit> <pfad> <ziel> <sha256>
  echo "curl -sf -H 'Accept: application/vnd.github.raw' 'https://api.github.com/repos/$1/contents/$3?ref=$2' -o '$4' && sha_ok $5 '$4'"
}
step "OKF-v0.2-Spezifikation über die GitHub-API holen (Apache-2.0)" "$(fetch $OKF_REPO $OKF_COMMIT $OKF_PATH "$DL/SPEC.md" $OKF_SHA)"
step "README von pi-llm-wiki über die GitHub-API holen (MIT)" "$(fetch $PI_REPO $PI_COMMIT $PI_PATH "$DL/README.md" $PI_SHA)"
step "Quelle erfassen: okf-v0-2-spec" "uv run --no-config --quiet '$K' capture okf-v0-2-spec \
  --url 'https://github.com/$OKF_REPO/blob/$OKF_COMMIT/$OKF_PATH' --file '$DL/SPEC.md' --commit $OKF_COMMIT \
  --license Apache-2.0 --license-url https://www.apache.org/licenses/LICENSE-2.0 --note 'OKF v0.2, Spezifikation' \
  && cat raw/okf-v0-2-spec/manifest.json"
step "Quelle erfassen: pi-llm-wiki" "uv run --no-config --quiet '$K' capture pi-llm-wiki \
  --url 'https://github.com/$PI_REPO/blob/$PI_COMMIT/$PI_PATH' --file '$DL/README.md' --commit $PI_COMMIT \
  --license MIT --license-url 'https://github.com/$PI_REPO/blob/$PI_COMMIT/LICENSE' --note 'pi-llm-wiki, README' \
  && ls -l raw/pi-llm-wiki/original"

step "Quellenkarte anlegen: okf-v0-2-spec" "uv run --no-config --quiet '$K' new source okf-v0-2-spec \
  --title 'Open Knowledge Format (OKF), Version 0.2' \
  --description 'Spezifikation eines Formats für Wissensbündel aus Markdown-Dateien mit YAML-Frontmatter.' \
  --access fulltext --resource 'https://github.com/$OKF_REPO/blob/$OKF_COMMIT/$OKF_PATH' \
  --set raw_packet=raw/okf-v0-2-spec --set format=specification --set date=2026-10-24 --fundweg person \
  --set 'license={name: Apache-2.0, url: \"https://www.apache.org/licenses/LICENSE-2.0\"}'"
step "Quellenkarte anlegen: pi-llm-wiki" "uv run --no-config --quiet '$K' new source pi-llm-wiki \
  --title 'pi-llm-wiki (README)' \
  --description 'Erweiterung und MCP-Server für ein selbstgepflegtes LLM-Wiki nach dem Muster von Andrej Karpathy.' \
  --access fulltext --resource 'https://github.com/$PI_REPO/blob/$PI_COMMIT/$PI_PATH' \
  --set raw_packet=raw/pi-llm-wiki --set format=documentation --set date=2026-10-09 --fundweg person \
  --set 'license={name: MIT, url: \"https://github.com/$PI_REPO/blob/$PI_COMMIT/LICENSE\"}'"
step "Konzeptkarte anlegen, die beide Quellen nutzt" "uv run --no-config --quiet '$K' new concept llm-wiki-im-okf \
  --title 'LLM-Wiki im Open Knowledge Format' \
  --description 'Wie das LLM-Wiki-Muster und das Open Knowledge Format zusammenpassen.' --sources okf-v0-2-spec,pi-llm-wiki \
  --set domain=wissensorganisation --set 'aliases=[LLM Wiki, Open Knowledge Format Wiki]'"

cat >"$WORK/okf.md" <<'EOF'
# Open Knowledge Format (OKF), Version 0.2

## Kurzreferat

Die Spezifikation beschreibt Bündel aus Markdown-Dokumenten mit YAML-Frontmatter.

## Kernaussagen

- `type` ist der einzige stets verlangte Schlüssel im Frontmatter (§ 4, Z. 187).
- Verbraucher müssen kaputte Links tolerieren (§ 6, Z. 464).

## Relevanz

Format des Demo-Wikis.

## Verbindungen

- [LLM-Wiki im Open Knowledge Format](/concepts/llm-wiki-im-okf.md)

## Zugang

Volltext als Rohquelle `raw/okf-v0-2-spec`, Lizenz Apache-2.0.
EOF
cat >"$WORK/pi.md" <<'EOF'
# pi-llm-wiki (README)

## Kurzreferat

Das README beschreibt ein Wiki, das ein Sprachmodell selbst pflegt.

## Kernaussagen

- Das Paket folgt dem LLM-Wiki-Muster von Andrej Karpathy (Z. 23).
- Quellen werden unveränderlich erfasst (Z. 119).

## Relevanz

Muster, nach dem okfw arbeitet.

## Verbindungen

- [LLM-Wiki im Open Knowledge Format](/concepts/llm-wiki-im-okf.md)

## Zugang

Volltext als Rohquelle `raw/pi-llm-wiki`, Lizenz MIT.
EOF
cat >"$WORK/konzept.md" <<'EOF'
# LLM-Wiki im Open Knowledge Format

pi-llm-wiki folgt dem LLM-Wiki-Muster von Andrej Karpathy und erfasst Quellen unveränderlich.[^pi-llm-wiki]
Das Open Knowledge Format verlangt im Frontmatter nur `type`, alles Weitere ist Profil.[^okf-v0-2-spec]

Quellen: [OKF v0.2](/sources/okf-v0-2-spec.md) und [pi-llm-wiki](/sources/pi-llm-wiki.md).

[^okf-v0-2-spec]: [Open Knowledge Format (OKF), Version 0.2](/sources/okf-v0-2-spec.md)
[^pi-llm-wiki]: [pi-llm-wiki (README)](/sources/pi-llm-wiki.md)
EOF
step "Seiteninhalte setzen (body)" "uv run --no-config --quiet '$K' body sources/okf-v0-2-spec '$WORK/okf.md' \
  && uv run --no-config --quiet '$K' body sources/pi-llm-wiki '$WORK/pi.md' \
  && uv run --no-config --quiet '$K' body concepts/llm-wiki-im-okf '$WORK/konzept.md' && cat wiki/concepts/llm-wiki-im-okf.md"

step "rebuild" "uv run --no-config --quiet '$K' rebuild && cat wiki/index.md"
step "rebuild --check (nichts mehr zu tun)" "uv run --no-config --quiet '$K' rebuild --check"
step "lint (0 Fehler verlangt)" "uv run --no-config --quiet '$K' lint; rc=\$?; uv run --no-config --quiet '$K' lint --json | grep -c '\"severity\": \"error\"' | sed 's/^/Fehler: /'; exit \$rc"
step "status" "uv run --no-config --quiet '$K' status"
step "dashboard" "uv run --no-config --quiet '$K' dashboard && ls -l build/wiki-dashboard.html \
  && echo \"Seiten im Dashboard: \$(grep -o 'llm-wiki-im-okf' build/wiki-dashboard.html | wc -l) Treffer für llm-wiki-im-okf\" \
  && ! grep -Eo '<(script|img|iframe)[^>]+src=.https?://|<link[^>]+href=.https?://|url\\(.?https?://' build/wiki-dashboard.html \
  && echo 'lädt nichts aus dem Netz nach (kein script/img/iframe/link/url() auf http)'"
step "Rohquelle bleibt unverändert (SHA-256)" "sha_ok $OKF_SHA raw/okf-v0-2-spec/original/SPEC.md \
  && sha_ok $PI_SHA raw/pi-llm-wiki/original/README.md"

log "## Freigabe"
log ""
KARTE=wiki/concepts/llm-wiki-im-okf.md
step "Freigabe ohne Gegenprüfung wird abgelehnt" "out=\$(uv run --no-config --quiet '$K' verify concepts/llm-wiki-im-okf \
  --by human:nachbau --status stable 2>&1); rc=\$?; echo \"\$out\"; echo \"Exit \$rc\"; test \$rc -ne 0 \
  && grep -q 'keine Gegenprüfung' <<< \"\$out\" && grep '^status:' $KARTE && grep -q '^status: draft' $KARTE"
step "Freigabe nach Gegenprüfung, Rückfall nach body (stable → draft)" "uv run --no-config --quiet '$K' verify concepts/llm-wiki-im-okf \
  --by claude-code/nachbau --note 'Gegenprüfung im Nachbau' \
  && uv run --no-config --quiet '$K' verify concepts/llm-wiki-im-okf --by human:nachbau --status stable --note 'Charge Nachbau' \
  && grep -q '^status: stable' $KARTE && grep -c 'body_sha256' $KARTE | sed 's/^/Vermerke mit Prüfwert: /' \
  && { cat '$WORK/konzept.md'; echo; echo 'Ein Satz nach der Freigabe.'; } | uv run --no-config --quiet '$K' body concepts/llm-wiki-im-okf - \
  && grep '^status:' $KARTE && grep -q '^status: draft' $KARTE \
  && uv run --no-config --quiet '$K' lint --json | grep -o '\"code\": \"verified_outdated\"' \
  && uv run --no-config --quiet '$K' status | grep -E '^(Vertrauen|Freigaben):'"

log "## Hooks, wie Claude Code sie aus dem Plugin startet"
log ""
CWD=$WORK
cmd_of() { grep -o '"command": *".*'"$1"'.*"' "$P/hooks/hooks.json" | head -1 | sed 's/^"command": *"//; s/"$//; s/\\"/"/g'; }
hook() { # hook <unterbefehl> <projekt> <json>
  printf 'CLAUDE_PLUGIN_ROOT=%q CLAUDE_PROJECT_DIR=%q bash -c %q <<< %q' "$P" "$2" "$(cmd_of "$1")" "$3"
}
step "hooks.json: Befehle" "grep -o '\"command\": *\".*\"' '$P/hooks/hooks.json' | sed 's/^\"command\": *//'"
step "SessionStart im Demo-Wiki: Status als Kontext" "out=\$($(hook session-start "$DEMO" '{"hook_event_name":"SessionStart","source":"startup"}')); echo \"\$out\"; grep -q 'OKF-v0.2-Wiki unter' <<< \"\$out\""
step "SessionStart ohne wiki.toml: keine Ausgabe" "out=\$($(hook session-start "$LEER" '{"hook_event_name":"SessionStart","source":"startup"}')); test -z \"\$out\" && echo 'leer'"
step "PreToolUse: Write auf Rohquelle wird abgelehnt" "out=\$($(hook guard "$DEMO" "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Write\",\"cwd\":\"$DEMO\",\"tool_input\":{\"file_path\":\"$DEMO/raw/okf-v0-2-spec/original/SPEC.md\",\"content\":\"x\"}}")); echo \"\$out\"; grep -q '\"permissionDecision\": \"deny\"' <<< \"\$out\""
step "PreToolUse: Write auf wiki/index.md wird abgelehnt" "out=\$($(hook guard "$DEMO" "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Write\",\"cwd\":\"$DEMO\",\"tool_input\":{\"file_path\":\"$DEMO/wiki/index.md\",\"content\":\"x\"}}")); echo \"\$out\"; grep -q '\"permissionDecision\": \"deny\"' <<< \"\$out\""
step "PreToolUse: Edit einer Karte ist erlaubt" "out=\$($(hook guard "$DEMO" "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Edit\",\"cwd\":\"$DEMO\",\"tool_input\":{\"file_path\":\"$DEMO/wiki/concepts/llm-wiki-im-okf.md\"}}")); test -z \"\$out\" && echo 'keine Einwände'"
step "PostToolUse: Edit einer Karte landet im Ereignisstrom" "vor=\$(wc -l < '$DEMO/wiki/.events.jsonl'); $(hook post-edit "$DEMO" "{\"hook_event_name\":\"PostToolUse\",\"tool_name\":\"Edit\",\"cwd\":\"$DEMO\",\"tool_input\":{\"file_path\":\"$DEMO/wiki/concepts/llm-wiki-im-okf.md\"}}"); nach=\$(wc -l < '$DEMO/wiki/.events.jsonl'); echo \"Ereignisse \$vor -> \$nach\"; tail -1 '$DEMO/wiki/.events.jsonl'; test \$nach -eq \$((vor + 1))"
step "Stop: Rebuild läuft, danach ist rebuild --check leer" "$(hook stop "$DEMO" '{"hook_event_name":"Stop"}'); cd '$DEMO' && uv run --no-config --quiet '$K' rebuild --check && tail -3 wiki/log.md"

STOPWIKI=$WORK/stop-wiki
step "Stop: Lint-Fehler gehen an die Sitzung zurück, die Kette endet" "cp -R '$DEMO' '$STOPWIKI' && (cd '$STOPWIKI' && printf 'Aussage.[^f]\n\n[^f]: irgendwo\n' | uv run --no-config --quiet '$K' body concepts/llm-wiki-im-okf -) && a=\$($(hook stop "$STOPWIKI" '{"hook_event_name":"Stop","session_id":"nachbau","stop_hook_active":false}')); echo \"\$a\"; grep -q '\"decision\": \"block\"' <<< \"\$a\" && b=\$($(hook stop "$STOPWIKI" '{"hook_event_name":"Stop","session_id":"nachbau","stop_hook_active":true}')); echo \"\$b\"; ! grep -q decision <<< \"\$b\""

log "## Neu in 0.6.0"
log ""
CWD=$WORK
W6=$WORK/wiki-060; export W6
cat >"$WORK/schaubild.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60" width="240" height="120" onload="alert('NACHBAU-ONLOAD')">
  <script>alert('NACHBAU-SKRIPT')</script>
  <title>Testkreis</title>
  <circle id="nachbau-kreis" cx="30" cy="30" r="20" fill="#33aa77"/>
  <rect x="60" y="10" width="40" height="40" fill="none" stroke="#333333"/>
</svg>
EOF
step "rollen.py status --json läuft ohne Einrichtung: Rollen sitzung, claude, container, Anthropic, keine Warnung" '
test ! -e "$HOME/.config/okfw" && echo "keine Einstellungsdatei unter ~/.config/okfw"
uv run --no-config --quiet "$P/skills/okfw/rollen.py" status --json >"$WORK/rollen.json"; rc=$?; echo "rollen.py Exit $rc"; test $rc -eq 0 || exit 1
uv run --no-config --quiet --no-project python - "$WORK/rollen.json" <<"PY"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
r = d["rollen"]
for k, v in r.items():
    print("Rolle", k, "| Harness", v["harness"], "| Anbieter", v["anbieter"], "| Modell", v["modell"], "| Herkunft", v["herkunft"]["harness"])
assert {v["harness"] for v in r.values()} == {"sitzung", "claude", "container"}, "Harnesses"
assert {v["anbieter"] for v in r.values()} == {"anthropic"}, "Anbieter"
assert r["lesen"]["modell"] != r["gegenpruefen"]["modell"], "Lesen und Gegenprüfen ab Werk mit verschiedenem Modell"
assert d["warnungen"] == [], "ab Werk keine Warnung"
assert d["sitzung"] == "anthropic", "Sitzung über Anthropic"
assert set(d["anbieter"]) == {"anthropic", "openai", "openrouter", "ollama"}, "nur die mitgelieferten Anbieter"
assert not any(v["gesperrt"] for v in r.values()), "keine Rolle gesperrt"
print("Anbieter im Katalog:", ", ".join(d["anbieter"]))
print("Sitzung über:", d["sitzung"], "| Warnungen:", d["warnungen"])
PY
rc=$?; echo "Prüfung Exit $rc"; test $rc -eq 0'
step "rollen.py auftrag --trocken: ohne wiki.toml Exit 2, mit --wiki Exit 0, mit --ohne-wiki gesperrt (Exit 2), fehlende Datei Exit 2" '
cd "$WORK" || exit 1
printf "Fasse den Text in einem Satz zusammen.\n" >auftrag.md
R=(uv run --no-config --quiet "$P/skills/okfw/rollen.py")
fail=0
pruefe() { local erwartet=$1 titel=$2; shift 2; "$@" >"$WORK/auftrag.out" 2>&1; local rc=$?; if [ $rc -eq $erwartet ]; then echo "$titel: Exit $rc (erwartet $erwartet)"; else echo "ABWEICHUNG $titel: Exit $rc, erwartet $erwartet"; fail=1; fi; sed -n "1,3p" "$WORK/auftrag.out" | sed "s/^/    /"; }
pruefe 2 "ohne wiki.toml und ohne --wiki   " "${R[@]}" auftrag --rolle bearbeiten --datei auftrag.md --trocken
pruefe 0 "mit --wiki auf die Demo-wiki.toml" "${R[@]}" --wiki "$DEMO/wiki.toml" auftrag --rolle bearbeiten --datei auftrag.md --trocken
pruefe 2 "--wiki auf eine fehlende Datei    " "${R[@]}" --wiki "$WORK/fehlt/wiki.toml" auftrag --rolle bearbeiten --datei auftrag.md --trocken
pruefe 2 "--ohne-wiki (personenbezogen)     " "${R[@]}" auftrag --rolle bearbeiten --datei auftrag.md --trocken --ohne-wiki
grep -q personenbezogen "$WORK/auftrag.out" && echo "Meldung nennt die Datenklasse personenbezogen" || { echo "Meldung nennt die Datenklasse nicht"; fail=1; }
exit $fail'
step "recherche/scripts/api.py --help und dienste (über uv, ohne Netz)" '
A="$P/skills/recherche/scripts/api.py"
uv run --no-config --quiet --script "$A" --help | sed -n "1,6p"; test ${PIPESTATUS[0]} -eq 0 || exit 1
uv run --no-config --quiet --script "$A" dienste >"$WORK/dienste.txt" || exit 1
cat "$WORK/dienste.txt"
test "$(grep -c "api:" "$WORK/dienste.txt")" -ge 7 && grep -q "api:cellar" "$WORK/dienste.txt"'
step "abbildung: SVG mit <script> wird bereinigt abgelegt" '
cp -R "$DEMO" "$W6" && cd "$W6" || exit 1
uv run --no-config --quiet "$K" abbildung nachbau-schaubild --file "$WORK/schaubild.svg"; rc=$?; echo "abbildung Exit $rc"; test $rc -eq 0 || exit 1
f=$(find . -name nachbau-schaubild.svg | head -1); echo "Datei: $f"; cat "$f"
! grep -qiE "<script|NACHBAU-SKRIPT|NACHBAU-ONLOAD|onload" "$f" && echo "kein Skript, kein onload in der Ablage" && grep -q "nachbau-kreis" "$f" && echo "Zeichnung (Kreis) blieb erhalten"'
step "Abbildung einbinden, lint ohne Fehler" '
cd "$W6" || exit 1
{ cat "$WORK/konzept.md"; printf "\n![Testkreis und Rechteck](/abbildungen/nachbau-schaubild.svg)\n*Abb.: Testschaubild. Eigene Darstellung nach OKF[^okf-v0-2-spec].*\n"; } | uv run --no-config --quiet "$K" body concepts/llm-wiki-im-okf - || exit 1
uv run --no-config --quiet "$K" lint; rc=$?; echo "lint Exit $rc"; test $rc -eq 0'
step "dashboard: Abbildung bereinigt eingebettet, kein Skript aus der SVG, kein serve-Code" '
cd "$W6" || exit 1
uv run --no-config --quiet "$K" dashboard || exit 1
d=build/wiki-dashboard.html
echo "Treffer nachbau-kreis (Zeichnung eingebettet): $(grep -c nachbau-kreis "$d")"
echo "Treffer NACHBAU-SKRIPT/ONLOAD (muss 0 sein):   $(grep -ciE "NACHBAU-SKRIPT|NACHBAU-ONLOAD" "$d")"
echo "Treffer Schreibschicht (serve.js, auftraege.js, muss 0 sein): $(grep -cE "window\.okfw(Edit|Serve|Auftraege) = " "$d")"
echo "Andockpunkte okfwEdit?. in der Vorlage (dürfen stehen): $(grep -o "okfwEdit?\." "$d" | wc -l)"
grep -q nachbau-kreis "$d" && ! grep -qiE "NACHBAU-SKRIPT|NACHBAU-ONLOAD" "$d" && ! grep -qE "window\.okfw(Edit|Serve|Auftraege) = " "$d"'
step "Karte mit {: onclick=…} und javascript:-Link: im Dashboard nur Text, kein onclick, keine Skript-Adresse, CSP mit Nonce" '
cd "$W6" || exit 1
uv run --no-config --quiet "$K" new concept attributprobe --title "Attributprobe" --description "Prüft, dass Attributlisten und Skript-Links nichts bewirken." >/dev/null || exit 1
printf "# Attributprobe\n\nEin Absatz mit Attributliste.\n{: onclick=\"nachbauklick()\" }\n\n[Skript](javascript:nachbaulink()) und [normaler Link](https://example.org/).\n" | uv run --no-config --quiet "$K" body concepts/attributprobe - || exit 1
uv run --no-config --quiet "$K" dashboard || exit 1
uv run --no-config --quiet --no-project python - build/wiki-dashboard.html <<"PY"
import json, re, sys
t = open(sys.argv[1], encoding="utf-8").read()
daten = json.loads(re.search(r"<script type=\"application/json\" id=\"okf-data\">(.*?)</script>", t, re.S).group(1))
seite = next(n for n in daten["nodes"] if n["id"] == "concepts/attributprobe")["html"]
print("HTML der Karte:", seite.replace("\n", " ")[:300])
assert "{: onclick=" in seite, "die Attributliste bleibt als Text sichtbar"
assert not re.search(r"<[a-zA-Z][^>]*\sonclick\s*=", seite, re.I), "ein onclick-Attribut ist entstanden"
assert not re.search(r"href\s*=\s*[\"\x27]?\s*javascript", seite, re.I), "ein javascript:-Link ist entstanden"
assert re.search(r"<a [^>]*href=\"https://example.org/\"", seite), "normaler Link fehlt"
meta = re.search(r"<meta http-equiv=\"Content-Security-Policy\" content=\"([^\"]*)\"", t)
assert meta and "script-src \x27nonce-" in meta.group(1) and "default-src \x27none\x27" in meta.group(1), "CSP-Meta fehlt"
nonce = re.search(r"nonce-([A-Za-z0-9_-]+)", meta.group(1)).group(1)
skripte = re.findall(r"<script(?![^>]*type=\"application/json\")([^>]*)>", t)
assert skripte and all(f"nonce=\"{nonce}\"" in s for s in skripte), "ausführbare Skripte ohne Nonce"
print("kein onclick, kein javascript:-Link, CSP mit Nonce an", len(skripte), "Skript(en)")
PY'
SERVE_START='
cd "$W6" || exit 1
PORT=$(uv run --no-config --quiet --no-project python - <<"PY"
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
PY
)
echo "freier Port: $PORT"
uv run --no-config --quiet "$K" serve --port "$PORT" --no-open >"$WORK/serve.log" 2>&1 &
PID=$!
trap "kill $PID 2>/dev/null" EXIT
for i in $(seq 1 80); do grep -q "?t=" "$WORK/serve.log" && break; sleep 0.5; done
echo "--- Ausgabe von serve (Token ersetzt)"; sed "s/?t=.*/?t=<Token>/" "$WORK/serve.log"; echo "---"
URL=$(grep -o "http://127.0.0.1:[0-9]*/[A-Za-z0-9_-][A-Za-z0-9_-]*/?t=[A-Za-z0-9_-]*" "$WORK/serve.log" | head -1)
test -n "$URL" || { echo "keine Adresse der Form http://127.0.0.1:<port>/<praefix>/?t=<token> ausgegeben"; exit 1; }
WURZEL=http://127.0.0.1:$PORT
B=${URL%%\?*}; B=${B%/}
echo "Adresse: $WURZEL/<Präfix>/?t=<Token>"
fail=0
erwarte() { case " $3 " in *" $2 "*) echo "$1: HTTP $2 (erwartet $3)";; *) echo "ABWEICHUNG $1: HTTP $2, erwartet $3"; fail=1;; esac; }
code() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
'
step "serve --no-open: Adresse mit Präfix, 403 ohne Token, 200 mit Token, Cookie nur für das Präfix, Server beenden" "$SERVE_START"'
erwarte "Wurzel ohne Token           " "$(code "$B/")" "401 403"
erwarte "Wurzel mit falschem Token   " "$(code "$B/?t=falsch")" "401 403"
erwarte "API ohne Anmeldung          " "$(code "$B/api/einstellungen")" "401 403"
erwarte "Wurzel mit Token (folgt 302)" "$(curl -s -L -c "$WORK/jar" -b "$WORK/jar" -o "$WORK/served.html" -w "%{http_code}" "$URL")" "200"
erwarte "API nach Anmeldung          " "$(code -b "$WORK/jar" "$B/api/einstellungen")" "200"
erwarte "POST ohne Kopf X-OKFW       " "$(code -b "$WORK/jar" -X POST -H "Origin: $WURZEL" -H "Content-Type: application/json" -d "{}" "$B/api/seite")" "403"
name=$(awk "/okfw_sitzung_/ {print \$6}" "$WORK/jar"); pfad=$(awk "/okfw_sitzung_/ {print \$3}" "$WORK/jar")
echo "Cookie: ${name:-keins}"
case "$name" in okfw_sitzung_*) ;; *) echo "ABWEICHUNG: kein Cookie okfw_sitzung_<hex>"; fail=1;; esac
if test "$pfad" = "${B#$WURZEL}/"; then echo "Cookie gilt nur für den Pfad des Präfixes (/<Präfix>/)"; else echo "ABWEICHUNG: Cookie-Pfad ist nicht der Präfixpfad"; fail=1; fi
n=$(grep -c "window.okfwEdit = " "$WORK/served.html"); echo "serve-Seite enthält window.okfwEdit = : $n Treffer (erwartet mindestens 1)"; test "$n" -ge 1 || fail=1
m=$(grep -c "window.okfwEdit = " build/wiki-dashboard.html); echo "Datei von dashboard enthält window.okfwEdit = : $m Treffer (erwartet 0)"; test "$m" -eq 0 || fail=1
kill "$PID"; wait "$PID" 2>/dev/null; sleep 1
if curl -s --max-time 2 -o /dev/null "$B/"; then echo "Server läuft noch"; fail=1; else echo "Server beendet, Port antwortet nicht mehr"; fi
exit $fail'
step "serve lehnt fremde Pfade ab: ohne Präfix, mit fremdem Präfix und mit dem Token am falschen Ort ist alles 404" "$SERVE_START"'
TOKEN=${URL##*\?t=}
curl -s -L -c "$WORK/jar2" -b "$WORK/jar2" -o /dev/null "$URL"
erwarte "Wurzel ohne Präfix                  " "$(code "$WURZEL/")" "404"
erwarte "API ohne Präfix                     " "$(code "$WURZEL/api/einstellungen")" "404"
erwarte "Token ohne Präfix                   " "$(code "$WURZEL/?t=$TOKEN")" "404"
erwarte "fremdes Präfix                      " "$(code "$WURZEL/anderes-praefix/")" "404"
erwarte "fremdes Präfix, API                 " "$(code "$WURZEL/anderes-praefix/api/einstellungen")" "404"
erwarte "angemeldet, API ohne Präfix         " "$(code -b "$WORK/jar2" "$WURZEL/api/einstellungen")" "404"
erwarte "angemeldet, Präfix mit Unterpfad    " "$(code -b "$WORK/jar2" "$B/api/gibt-es-nicht")" "404"
erwarte "angemeldet, Präfix ohne Schrägstrich" "$(code -b "$WORK/jar2" "$B")" "404"
erwarte "andere Methode (PUT)                " "$(code -X PUT "$B/api/seite")" "501"
kill "$PID"; wait "$PID" 2>/dev/null
exit $fail'

log "## Echte Claude-Sitzung (mit CLAUDE_CODE_OAUTH_TOKEN; für einen Release nicht auslassen)"
log ""
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  CWD=$DEMO
  step "claude -p: Schreibversuch auf Rohquelle scheitert am Guard" "claude -p --permission-mode acceptEdits \
    'Überschreibe die Datei raw/okf-v0-2-spec/original/SPEC.md mit dem Text x. Nutze nur das Write-Werkzeug.' | tail -5; \
    sha_ok $OKF_SHA raw/okf-v0-2-spec/original/SPEC.md"
  # Geprüft wird der Ablauf, nicht der Wortlaut des Modells: Ohne Werkzeuge gibt es einen zweiten Modellzug nur durch die Rückgabe (num_turns = 2), Runden = 1 heißt genau ein Block, stop_hook_active hat gegriffen. Den Text der Rückgabe prüft der Schritt „Stop: Lint-Fehler gehen an die Sitzung zurück“.
  # Ohne Werkzeuge bleibt der Lint-Fehler stehen, der Zähler wird also nicht zurückgesetzt; timeout fängt einen Hänger.
  CWD=$STOPWIKI
  STOPTMP=$WORK/stop-tmp
  step "claude -p: Stop-Rückgabe erreicht die Sitzung, die Kette endet" "mkdir -p '$STOPTMP' && out=\$(TMPDIR='$STOPTMP' \$(command -v timeout >/dev/null && echo 'timeout 300') claude -p --model haiku --tools '' --output-format json \
    'Antworte nur mit dem Wort FERTIG.'); rc=\$?; echo \"\$out\"; echo \"Exit \$rc\"; test \$rc -eq 0 \
    && grep -qE '\"num_turns\": *2[,}]' <<< \"\$out\" \
    && sid=\$(grep -o '\"session_id\": *\"[^\"]*\"' <<< \"\$out\" | head -1 | cut -d'\"' -f4) \
    && runden=\$(cat '$STOPTMP'/okfw-stop-\"\$sid\") && echo \"Runden: \$runden\" && test \"\$runden\" = 1"
else
  skip "claude -p: Schreibversuch auf Rohquelle scheitert am Guard" "kein CLAUDE_CODE_OAUTH_TOKEN; die Hook-Befehle oben laufen genau so, wie Claude Code sie startet"
  skip "claude -p: Stop-Rückgabe erreicht die Sitzung, die Kette endet" "kein CLAUDE_CODE_OAUTH_TOKEN"
fi

cp -R "$DEMO" "$OUT/demo-wiki" 2>/dev/null
log "## Ergebnis"
log ""
log "Ende: $(date -u +%Y-%m-%dT%H:%M:%SZ) · $N Schritte: $OK bestanden, $FAIL fehlgeschlagen, $SKIP übersprungen."
log ""
if [ $SKIP -gt 0 ]; then
  log "**Unvollständig:** $SKIP Schritt(e) übersprungen, weil CLAUDE_CODE_OAUTH_TOKEN fehlt. Für einen Release den Lauf mit Token wiederholen."
  log ""
fi
echo "Ergebnis: $OK bestanden, $FAIL fehlgeschlagen, $SKIP übersprungen – $LOG"
[ $SKIP -gt 0 ] && echo "UNVOLLSTÄNDIG: ohne CLAUDE_CODE_OAUTH_TOKEN fehlt der Schritt mit der echten Claude-Sitzung; für einen Release wiederholen."
[ $FAIL -eq 0 ]
