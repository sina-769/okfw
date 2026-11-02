# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Selbstreview: ein Wegwerf-Wiki über okfw selbst, mit okfw gebaut.

  uv run --script tests/selbstreview.py [--rev REV] [--neu]      # im Plugin-Ordner

Nimmt den Stand eines Commits (Standard HEAD, nie den Arbeitsbaum), legt darunter
.nachbau/selbstreview-<version>/ an (init --profile dokumentation, je Datei ein Rohquellen-Paket mit
capture --commit und eine Quellenkarte) und gewinnt die Kartenliste mechanisch aus dem Code:
je Unterbefehl (argparse im Kern), Lint-Regelcode (SEVERITY und add(...)), Konfigurationsschlüssel
(DEFAULT_CONFIG, TYPE_DEFAULTS und jeder Zugriff auf cfg im Kern und im Hook) und Hook (Handler in
okf_hook.py). Jede Karte belegt per Fußnote mit Zeile, wo Code, Doku (SKILL.md, README.md, Rundgang, Entwicklung, CHANGELOG.md,
docs/konfiguration.md) und Tests das Konzept nennen. Die Lücken (im Code, aber in keiner Doku; in der Doku,
aber nicht im Code; ohne Test; Schwere und Hooks, die zwischen Doku und Code abweichen) stehen als erste
Befundliste in befunde.md neben dem Wiki, die Rohdaten in inventar.json.

Die Treffer sind Textsuche und damit Näherung: Sie zeigen, wo zu lesen ist, nicht, dass etwas stimmt.
Was danach kommt (Karten lesen, Widersprüche vermerken, Gegenprüfung, Bericht), steht in tests/selbstreview.md.
Nur Standardbibliothek; ruft den Kern des geprüften Stands über uv auf.
"""

import argparse
import ast
import json
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/sina-769/okfw"
LICENSE_URL = f"{REPO_URL}/blob/main/LICENSE"

# Kürzel (Fußnoten-Präfix), Pfad im Plugin, Art, Beschreibung
DOKU = [
    ("skill", "skills/okfw/SKILL.md", "Arbeitsweise und Regeln für Claude"),
    ("readme", "README.md", "Einstieg, Installation"),
    ("rundgang", "docs/rundgang.md", "Funktionen mit Bildern, Modellrollen, Freigabe, Arbeit von Hand"),
    ("entwicklung", "docs/entwicklung.md", "Voraussetzungen, Tests, Nachbau, Release"),
    ("changelog", "CHANGELOG.md", "Änderungen je Version"),
    ("konf", "skills/okfw/docs/konfiguration.md", "alle Befehle, wiki.toml-Schlüssel, Lint-Codes, Hooks"),
]
CODE = [
    ("kern", "skills/okfw/okfw.py", "Kern: Eingang für CLI und Bibliothek"),
    ("hook", "skills/okfw/hooks/okf_hook.py", "Hooks für Claude Code"),
    ("hooksjson", "hooks/hooks.json", "Hook-Block des Plugins"),
    ("settings", "skills/okfw/hooks/settings.example.json", "Hook-Block für den Skill-Ordner"),
    ("prof-default", "skills/okfw/profiles/default.toml", "Grundprofil"),
    ("prof-doku", "skills/okfw/profiles/dokumentation.toml", "Profil für Doku und Code"),
]
# Testhelfer, die einen Unterbefehl aufrufen, ohne ihn als String zu nennen
TEST_HELPERS = {"lint": r"\blint_codes\(", "new": r"\bself\.quelle\(", "init": r"\bWikiTest\b"}
MAX_HITS = 4          # Fundstellen je Datei und Konzept auf der Karte
DYNAMIC = {"enums", "labels", "rights"}          # Abschnitte, deren Schlüssel Projektwerte sind
NESTED = {"export", "types"}                     # Abschnitte mit Untertabellen: Schlüssel auf drei Ebenen


# ---------------------------------------------------------------------------------------------- Stand holen

def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(PLUGIN), *args], check=True, capture_output=True, text=True).stdout


# Dateien unter tests/, die keine Abdeckung zählen: Paketdatei, dieses Skript und die Abdeckungsprüfung selbst
NOT_TESTS = ("__init__.py", "selbstreview.py", "test_abdeckung.py")


def test_sources(paths) -> list[tuple[str, str, str]]:
    """(Kürzel, Pfad im Plugin, Art) je Testdatei aus einer Liste von Pfaden wie tests/test_x.py."""
    return [(f"t-{Path(p).stem.removeprefix('test_').replace('_', '-')}", str(p), "Tests")
            for p in sorted(map(str, paths)) if p.endswith(".py") and Path(p).name not in NOT_TESTS]


def kern_code(root: Path) -> list[tuple[str, str, str]]:
    """(Kürzel, Pfad im Plugin, Art) je Modul unter okfw_kern/; der Eingang okfw.py steht in CODE."""
    return [(f"kern-{p.stem.replace('_', '-')}", str(p.relative_to(root)), "Kern: Modul")
            for p in sorted((root / "skills/okfw/okfw_kern").glob("*.py")) if p.name != "__init__.py"]


def inventory(root: Path = PLUGIN) -> dict:
    """Kartenliste und Lücken für den Plugin-Ordner root (Arbeitsbaum); dieselbe Liste wie im Selbstreview."""
    return collect(root, test_sources(p.relative_to(root) for p in (root / "tests").glob("*.py")))


def stage(rev: str, target: Path) -> tuple[str, str, list[tuple[str, str, str]]]:
    """Schreibt die Dateien des Commits nach target; liefert SHA, Commit-Datum und die Testquellen."""
    sha = git("rev-parse", f"{rev}^{{commit}}").strip()
    date = git("show", "-s", "--format=%cs", sha).strip()
    tests = test_sources(git("ls-tree", "--name-only", sha, "tests/").split())
    keep = [rel for _, rel, _ in DOKU + CODE + tests]
    keep += git("ls-tree", "-r", "--name-only", sha, "skills/okfw/templates/").split()
    keep += git("ls-tree", "-r", "--name-only", sha, "skills/okfw/okfw_kern/").split()
    for rel in keep:
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(subprocess.run(["git", "-C", str(PLUGIN), "show", f"{sha}:./{rel}"],
                                       check=True, capture_output=True).stdout)
    return sha, date, tests


# ---------------------------------------------------------------------------------------------- Inventar aus dem Code

def literal_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def subcommands(tree: ast.Module) -> list[dict]:
    """Unterbefehle aus build_parser: Name, Aliase, Hilfe, Optionen, Zeilen, Funktion."""
    build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_parser")
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def options(stmts) -> list[dict]:
        out = []
        for st in stmts:
            for call in ast.walk(st):
                if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "add_argument":
                    flags = [literal_str(a) for a in call.args if literal_str(a)]
                    kw = {k.arg: k.value for k in call.keywords}
                    out.append({"flags": flags, "line": call.lineno,
                                "required": isinstance(kw.get("required"), ast.Constant) and kw["required"].value is True,
                                "help": literal_str(kw["help"]) if "help" in kw else ""})
        return out

    helpers = {n.name: n for n in build.body if isinstance(n, ast.FunctionDef)}
    result, current = [], None
    for st in build.body:
        call = st.value if isinstance(st, (ast.Assign, ast.Expr)) else None
        if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "add_parser" and getattr(call.func.value, "id", "") == "sub":
            kw = {k.arg: k.value for k in call.keywords}
            current = {"name": literal_str(call.args[0]), "line": st.lineno, "end": st.end_lineno,
                       "aliases": [literal_str(e) for e in getattr(kw.get("aliases"), "elts", [])],
                       "help": literal_str(kw.get("help")) or "", "options": [], "func": None}
            result.append(current)
            continue
        if current is None or isinstance(st, ast.FunctionDef):
            continue
        current["end"] = st.end_lineno
        current["options"] += options([st])
        for c in ast.walk(st):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in helpers:
                current["options"] += options(helpers[c.func.id].body)
                current["helper"] = [helpers[c.func.id].lineno, helpers[c.func.id].end_lineno]
            if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "set_defaults":
                for k in c.keywords:
                    if k.arg == "func" and isinstance(k.value, ast.Name) and k.value.id in funcs:
                        f = funcs[k.value.id]
                        current["func"] = {"name": f.name, "line": f.lineno, "end": f.end_lineno}
        if isinstance(st, ast.Return):
            break
    for cmd in result:   # new-source erbt die Funktion von new über new_options
        if cmd["func"] is None and cmd.get("helper"):
            cmd["func"] = next((c["func"] for c in result if c["name"] == "new"), None)
    init = funcs.get("cmd_init")
    for cmd in result:
        if cmd["name"] == "init" and init:
            cmd["func"] = {"name": init.name, "line": init.lineno, "end": init.end_lineno}
    return result


def lint_codes(kerne: dict[str, ast.Module]) -> dict[str, dict]:
    """Codes aus SEVERITY (mit Schwere) und aus jedem add("code", …) im Kern; Fundstellen je Datei."""
    codes: dict[str, dict] = {}
    for label, tree in kerne.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SEVERITY" for t in node.targets):
                for k, v in zip(node.value.keys, node.value.values):
                    codes.setdefault(k.value, {"emitted": []})
                    codes[k.value].update(severity=v.value, severity_line=k.lineno, severity_datei=label)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "add" and node.args:
                code = literal_str(node.args[0])
                if code and re.fullmatch(r"[a-z]+(_[a-z]+)*", code):
                    codes.setdefault(code, {"emitted": []})["emitted"].append((label, node.lineno))
    return codes


def dict_keys(node: ast.Dict, prefix: tuple, out: dict, depth: int) -> None:
    for k, v in zip(node.keys, node.values):
        key = literal_str(k)
        if key is None:
            continue
        path = prefix + (key,)
        if isinstance(v, ast.Dict) and depth > 1 and v.keys:
            dict_keys(v, path, out, depth - 1)
        out.setdefault(".".join(path), {"default": [], "read": {}})["default"].append(k.lineno)


def concept_key(path: tuple) -> str | None:
    """Schlüsselpfad → Konzept: zwei Ebenen, unter export und types drei, Projektwerte als *."""
    if not path:
        return None
    if path[0] in DYNAMIC:
        return path[0]
    if path[0] in NESTED:
        return ".".join(path[:3]) if len(path) >= 3 else None
    return ".".join(path[:2]) if len(path) >= 2 else None


class CfgReads(ast.NodeVisitor):
    """Findet Lesezugriffe auf die wiki.toml: cfg[...] / .get(...)-Ketten, auch über Zwischenvariablen."""

    def __init__(self, roots: set[str]):
        self.roots, self.env, self.hits = roots, {}, []

    def path_of(self, node):
        if isinstance(node, ast.BoolOp):          # spec = self.types.get(t) or {...}
            return self.path_of(node.values[0])
        if isinstance(node, ast.Name):
            if node.id in self.roots:
                return ()
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr == "cfg":
                return ()
            if node.attr == "types":
                return ("types",)
            return None
        if isinstance(node, ast.Subscript):
            base = self.path_of(node.value)
            if base is None:
                return None
            key = literal_str(node.slice)
            if key is None:
                return base + ("*",) if base in (("types",), ("export",)) else None
            return base + (key,)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = self.path_of(node.func.value)
            if base is None:
                return None
            if node.func.attr == "get":
                key = literal_str(node.args[0]) if node.args else None
                if key is None:
                    return base + ("*",) if base in (("types",), ("export",)) else None
                return base + (key,)
            if node.func.attr in ("values", "items") and base in (("types",), ("export",)):
                return base + ("*",)
        return None

    def visit_FunctionDef(self, node):
        saved, self.env = self.env, {}
        self.generic_visit(node)
        self.env = saved

    def visit_Assign(self, node):
        path = self.path_of(node.value)
        for t in node.targets:
            if isinstance(t, ast.Name):
                if path is not None:
                    self.env[t.id] = path
                else:
                    self.env.pop(t.id, None)
        self.generic_visit(node)

    def bind_loop(self, target, it):
        path = self.path_of(it)
        if path and path[-1] == "*" and isinstance(target, ast.Tuple) and len(target.elts) == 2:
            if isinstance(target.elts[1], ast.Name):
                self.env[target.elts[1].id] = path

    def visit_For(self, node):
        self.bind_loop(node.target, node.iter)
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.bind_loop(node.target, node.iter)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        self.record(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            self.record(node)
        self.generic_visit(node)

    def record(self, node):
        path = self.path_of(node)
        if path:
            self.hits.append((path, node.lineno))


def config_keys(kerne: dict[str, ast.Module], hook: ast.Module) -> dict[str, dict]:
    keys: dict[str, dict] = {}
    datei = "kern"
    for label, tree in kerne.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                name = getattr(node.targets[0], "id", "")
                if name == "DEFAULT_CONFIG":
                    dict_keys(node.value, (), keys, 2)
                    datei = label
                elif name == "TYPE_DEFAULTS":
                    dict_keys(node.value, ("types", "*"), keys, 1)
    # DEFAULT_CONFIG nennt auch Abschnitte ohne Schlüssel (enums, rights …) und export als leere Tabelle
    for key in list(keys):
        ck = concept_key(tuple(key.split(".")))
        if ck != key:
            entry = keys.pop(key)
            if ck:
                keys.setdefault(ck, {"default": [], "read": {}})["default"] += entry["default"]
    for label, tree, roots in (*((la, tr, {"cfg"}) for la, tr in kerne.items()), ("hook", hook, {"cfg"})):
        v = CfgReads(roots)
        v.visit(tree)
        wild = []
        for path, line in v.hits:
            ck = concept_key(path)
            if ck and ck.startswith("export.*."):     # Schleife über alle [export.*]: gilt für jeden Export
                wild.append((ck.rsplit(".", 1)[1], line))
            elif ck and "*" not in ck.split(".")[-1:]:
                keys.setdefault(ck, {"default": [], "read": {}})["read"].setdefault(label, []).append(line)
        for name, line in wild:
            for key in [k for k in keys if k.startswith("export.") and k.endswith(f".{name}")]:
                keys[key]["read"].setdefault(label, []).append(line)
    for entry in keys.values():
        entry["default_datei"] = datei
        for label in entry["read"]:
            entry["read"][label] = sorted(set(entry["read"][label]))
    return keys


def hooks(hook_tree: ast.Module, files: dict[str, str]) -> list[dict]:
    """Handler aus main() in okf_hook.py, mit Ereignis, Matcher und Aufruf aus hooks.json und settings.example.json."""
    funcs = {n.name: n for n in ast.walk(hook_tree) if isinstance(n, ast.FunctionDef)}
    handlers = {}
    for node in ast.walk(hook_tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "handlers":
            for k, v in zip(node.value.keys, node.value.values):
                handlers[k.value] = (k.lineno, funcs[v.id])
    result = []
    for name, (line, func) in handlers.items():
        entry = {"name": name, "line": line, "func": {"name": func.name, "line": func.lineno, "end": func.end_lineno},
                 "events": {}}
        for label in ("hooksjson", "settings"):
            data = json.loads(files[label])
            lines = files[label].splitlines()
            for event, groups in data["hooks"].items():
                for g in groups:
                    for h in g["hooks"]:
                        cmd = h["command"]
                        hit = f"okf_hook.py\\\" {name}" in json.dumps(cmd) or f"okf_hook.py\" {name}" in cmd
                        if not hit:   # Skill-Ordner ruft session-start und stop als Kern-Befehl auf
                            core = {"session-start": "status --hook", "stop": "rebuild --hook"}.get(name)
                            hit = bool(core and core in cmd)
                        if hit:
                            no = next(i for i, t in enumerate(lines, 1) if json.dumps(cmd)[1:40] in t)
                            entry["events"].setdefault(label, []).append(
                                {"event": event, "matcher": g.get("matcher", ""), "timeout": h.get("timeout"),
                                 "command": cmd, "line": no})
        result.append(entry)
    return result


# ---------------------------------------------------------------------------------------------- Fundstellen

def find_lines(text: str, patterns: list[str]) -> list[int]:
    found = []
    for no, line in enumerate(text.splitlines(), 1):
        if any(re.search(p, line) for p in patterns):
            found.append(no)
    return found


def toml_block_lines(text: str, key: str) -> list[int]:
    """Zeilen `name = …` in ```toml-Blöcken der Doku unter der passenden Tabelle."""
    parts = key.split(".")
    found, in_block, table = [], False, ()
    for no, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_block = line.strip() == "```toml" if not in_block else False
            table = ()
            continue
        if not in_block:
            continue
        m = re.match(r"\s*\[([\w.\"\\-]+)\]", line)
        if m:
            table = tuple(re.sub(r"\"", "", m.group(1)).split("."))
            if parts[0] in NESTED and len(table) >= 2 and table[0] == "types":
                table = ("types", "*") + table[2:]
            here = concept_key(table + ("x",)) if table else None
            if here and here.rsplit(".", 1)[0] == key:   # Tabelle selbst ist der Schlüssel (lint.severity …)
                found.append(no)
            elif table and ".".join(table) == key:
                found.append(no)
            continue
        m = re.match(r"\s*([\w\"]+)\s*=", line)
        if m and table:
            name = m.group(1).strip('"')
            if concept_key(table + (name,)) == key:
                found.append(no)
    return found


def key_patterns(key: str) -> list[str]:
    parts = key.split(".")
    sec = re.escape(parts[0])
    if len(parts) == 1:
        return [rf"`\[{sec}(\.[\w<>*]+)?\]"]
    name = re.escape(parts[-1])
    if parts[0] == "types":
        return [rf"`?\[types\.[\w<>*X]+\]`?\s*`?{name}\b", rf"`\[types\.[\w<>*X]+\]\s+{name}`"]
    if parts[0] == "export":
        return [rf"\[export\.{re.escape(parts[1])}\]`?\s*`?{name}\b"]
    return [rf"\[{sec}\]`?\s*`?{name}\b", rf"`\[{sec}\.{name}\]`", rf"`{sec}\.{name}`"]


def hits(texts: dict[str, str], patterns: list[str], extra=None) -> dict[str, list[int]]:
    out = {}
    for label, text in texts.items():
        lines = find_lines(text, patterns)
        if extra:
            lines = sorted(set(lines) | set(extra(label, text)))
        if lines:
            out[label] = lines
    return out


# ---------------------------------------------------------------------------------------------- Abgleich

def collect(stand: Path, tests: list[tuple[str, str, str]]) -> dict:
    read = lambda rel: (stand / rel).read_text(encoding="utf-8")
    module = kern_code(stand)
    files = {label: read(rel) for label, rel, _ in DOKU + CODE + module + tests}
    docs = {label: files[label] for label, _, _ in DOKU}
    testtexts = {label: files[label] for label, _, _ in tests}
    kerne = {label: ast.parse(files[label]) for label in ("kern", *(m[0] for m in module))}
    hook = ast.parse(files["hook"])

    inv = {"befehle": [], "regeln": [], "schluessel": [], "hooks": [], "doku_ohne_code": [], "abweichungen": [],
           "begriffe": []}

    # Unterbefehle und Optionen
    parser = next(label for label, tree in kerne.items()
                  if any(isinstance(n, ast.FunctionDef) and n.name == "build_parser" for n in ast.walk(tree)))
    cmds = subcommands(kerne[parser])
    known_cmds = {n for c in cmds for n in [c["name"], *c["aliases"]]}
    for c in cmds:
        c["datei"] = parser
        names = [c["name"], *c["aliases"]]
        alt = "|".join(re.escape(n) for n in names)
        pats = [rf"`(?:okfw\.py )?(?:{alt})(?=[ `])", rf"okfw\.py\"? (?:--\S+ \S+ )*(?:{alt})\b",
                rf"\$OKF\"? (?:{alt})\b"]
        c["doku"] = hits(docs, pats)
        tpats = [rf"core\(\s*\"(?:{alt})\""] + ([TEST_HELPERS[c["name"]]] if c["name"] in TEST_HELPERS else [])
        c["tests"] = hits(testtexts, tpats)
        for o in c["options"]:
            ors = "|".join(re.escape(f) for f in o["flags"])
            o["doku"] = hits(docs, [rf"(?<![\w-])(?:{ors})(?![\w-])"])
            o["tests"] = hits(testtexts, [rf"\"(?:{ors})\""])
        if len(names) > 1 or any(len(o["flags"]) > 1 for o in c["options"]):
            inv["begriffe"].append({"befehl": c["name"], "aliase": c["aliases"],
                                    "optionen": [o["flags"] for o in c["options"] if len(o["flags"]) > 1]})
        inv["befehle"].append(c)
    # Doku nennt Befehle, die der Parser nicht kennt
    for label, text in docs.items():
        in_table = False
        for no, line in enumerate(text.splitlines(), 1):
            cands = re.findall(r"okfw\.py\"? (?:--\S+ \S+ )*([a-z][a-z-]+)\b", line)
            cands += re.findall(r"\$OKF\"? ([a-z][a-z-]+)\b", line)
            if label == "konf" and in_table and line.startswith("| `"):
                cands += re.findall(r"^\| `([a-z][a-z-]+)", line)
            in_table = line.startswith("| Befehl | Zweck") or (in_table and line.startswith("|"))
            for cand in cands:
                if cand not in known_cmds and cand not in ("befehl",):
                    inv["doku_ohne_code"].append({"art": "befehl", "name": cand, "datei": label, "zeile": no})

    # Lint-Regeln
    codes = lint_codes(kerne)
    doc_sev = {}
    for no, line in enumerate(docs["konf"].splitlines(), 1):
        m = re.match(r"\| (error|warn|info) \| (.*)\|$", line)
        if m:
            for code in re.findall(r"`([a-z_]+)`", m.group(2)):
                doc_sev[code] = (m.group(1), no)
    for code, entry in sorted(codes.items()):
        entry.update(name=code, doku=hits(docs, [rf"\b{code}\b"]), tests=hits(testtexts, [rf"\"{code}\""]))
        if code in doc_sev:
            entry["doku_severity"] = doc_sev[code]
            if entry.get("severity") and doc_sev[code][0] != entry["severity"]:
                inv["abweichungen"].append({"art": "schwere", "name": code, "code": entry["severity"],
                                            "doku": doc_sev[code][0], "zeile_code": entry["severity_line"],
                                            "zeile_doku": doc_sev[code][1]})
        inv["regeln"].append(entry)
    for code, (sev, no) in doc_sev.items():
        if code not in codes:
            inv["doku_ohne_code"].append({"art": "regel", "name": code, "datei": "konf", "zeile": no})

    # Konfigurationsschlüssel
    keys = config_keys(kerne, hook)
    profiles = {label: tomllib.loads(files[label]) for label in ("prof-default", "prof-doku")}

    def walk(d: dict, prefix: tuple = ()):
        for k, v in d.items():
            path = prefix + (k,)
            if path[0] == "rollen" and len(path) >= 2 and path[1] not in ("daten", "sperre"):
                continue   # [rollen.lesen] …: je Rolle freie Tabelle, die rollen.py prüft
            ck = concept_key(path)
            if isinstance(v, dict) and (ck is None or ck == ".".join(path) and path[0] in NESTED and len(path) < 3):
                yield from walk(v, path)
            elif isinstance(v, dict) and ck is None:
                yield from walk(v, path)
            else:
                yield ck if ck else ".".join(path)
    doc_keys = set()
    for label, text in docs.items():
        for block in re.findall(r"```toml\n(.*?)```", text, re.S):
            try:
                data = tomllib.loads(block)
            except tomllib.TOMLDecodeError:
                continue
            for t in ("types",):
                if t in data:
                    data[t] = {"*": {k: v for spec in data[t].values() for k, v in spec.items()}}
            doc_keys |= set(walk(data))
        for sec, name in re.findall(r"`\[([a-z_]+)\]`? `?([a-z_]+)`", text):
            if sec not in NESTED | DYNAMIC:
                doc_keys.add(f"{sec}.{name}")
    prof_keys = {}
    for label, data in profiles.items():
        if "types" in data:
            data = {**data, "types": {"*": {k: v for spec in data["types"].values() for k, v in spec.items()}}}
        for k in walk(data):
            prof_keys.setdefault(k, []).append(label)
    unique = {}
    for key in set(keys) | doc_keys | set(prof_keys):
        unique[key.split(".")[-1]] = unique.get(key.split(".")[-1], 0) + 1
    for key in sorted(set(keys) | doc_keys | set(prof_keys)):
        entry = keys.get(key, {"default": [], "read": {}})
        entry["name"] = key
        entry["doku"] = hits(docs, key_patterns(key), extra=lambda label, text, k=key: toml_block_lines(text, k))
        entry["profile"] = {label: find_lines(files[label], [rf"^\s*{re.escape(key.split('.')[-1])}\s*="]
                                              if "." in key else [rf"^\[{re.escape(key)}"])
                            for label in prof_keys.get(key, [])}
        sec, name = key.split(".")[0], key.split(".")[-1]
        # Schlüssel als Zeile `name = …`, Tabellen wie [lint.severity] auch als Abschnittskopf
        tpats = [rf"\[{re.escape(sec)}[\].]"] if "." not in key else [rf"(?:^|\\n|\s|[\"']){re.escape(name)}\s*=",
                                                                     rf"\[{re.escape(key)}\]"]
        tested = {}
        for label, text in testtexts.items():
            # ein Name, den nur ein Abschnitt kennt, zählt auch ohne die Tabellenzeile im selben Test
            if re.search(rf"\[{re.escape(sec)}[\].]", text) or sec == "types" or unique.get(name) == 1:
                lines = find_lines(text, tpats)
                if lines:
                    tested[label] = lines
        entry["tests"] = tested
        entry["im_code"] = bool(entry["default"] or entry["read"])
        if not entry["im_code"] and (key in doc_keys or key in prof_keys):
            where = "Doku" if key in doc_keys else "Profil"
            inv["doku_ohne_code"].append({"art": "schluessel", "name": key, "quelle": where,
                                          "datei": next(iter(entry["doku"] or entry["profile"] or {"-": 0})),
                                          "zeile": next(iter((entry["doku"] or entry["profile"] or {"-": [0]}).values()))[0]
                                          if (entry["doku"] or entry["profile"]) else 0})
        inv["schluessel"].append(entry)

    # Hooks
    for h in hooks(hook, files):
        events = {e["event"] for es in h["events"].values() for e in es}
        h["doku"] = hits(docs, [rf"(?i)\b{re.escape(h['name'])}\b", *(rf"\b{e}\b" for e in sorted(events))])
        h["tests"] = hits(testtexts, [rf"hook\(\s*\"{re.escape(h['name'])}\""])
        plug = h["events"].get("hooksjson", [])
        skill = h["events"].get("settings", [])
        for a, b in zip(plug, skill):
            for field in ("event", "matcher", "timeout"):
                if a[field] != b[field]:
                    inv["abweichungen"].append({"art": "hook", "name": h["name"], "feld": field,
                                                "hooksjson": a[field], "settings": b[field],
                                                "zeile_hooksjson": a["line"], "zeile_settings": b["line"]})
        inv["hooks"].append(h)
    return inv


# ---------------------------------------------------------------------------------------------- Karten

def slug_for(kind: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("*", "typ")).strip("-")
    return f"{kind}-{base}"


class Card:
    def __init__(self, files: dict[str, tuple[str, str]]):
        self.files, self.refs, self.notes = files, {}, []

    def ref(self, label: str, start: int, end: int | None = None) -> str:
        fid = f"{label}-z{start}"
        name = Path(self.files[label][0]).name
        where = f"Z. {start}–{end}" if end and end != start else f"Z. {start}"
        self.refs[fid] = (label, f"[{name}](/sources/okfw-{label}.md), {where}")
        return f"[^{fid}]"

    def hitlist(self, found: dict[str, list[int]]) -> list[str]:
        out = []
        for label, lines in found.items():
            name = Path(self.files[label][0]).name
            shown = " ".join(self.ref(label, n) for n in lines[:MAX_HITS])
            more = f", {len(lines) - MAX_HITS} weitere" if len(lines) > MAX_HITS else ""
            zeilen = ", ".join(map(str, lines[:MAX_HITS]))
            out.append(f"- {name}: Z. {zeilen}{more}.{shown}")
        return out

    def footnotes(self) -> list[str]:
        return [f"[^{fid}]: {text}" for fid, (_, text) in self.refs.items()]


def befund_lines(entry: dict, kind: str) -> list[str]:
    out = []
    if not entry.get("doku"):
        out.append("im Code, aber in keiner der vier Doku-Dateien gefunden")
    if not entry.get("tests"):
        out.append("in keinem Test gefunden (Textsuche)")
    if kind == "regel":
        if not entry.get("emitted"):
            out.append("in SEVERITY, aber kein `add(…)` im Kern meldet den Code")
        if entry.get("emitted") and not entry.get("severity"):
            out.append("gemeldet, aber nicht in SEVERITY (Schwere fällt auf warn zurück)")
        ds = entry.get("doku_severity")
        if ds and entry.get("severity") and ds[0] != entry["severity"]:
            out.append(f"Schwere im Code {entry['severity']}, in konfiguration.md {ds[0]}")
        if entry.get("severity") and not ds:
            out.append("fehlt in der Tabelle „Lint-Codes und Standardschwere“")
    if kind == "schluessel" and not entry.get("im_code"):
        out.append("in Doku oder Profil, aber der Kern liest den Schlüssel nicht")
    return out


def cards(inv: dict, files: dict) -> dict[str, dict]:
    result = {}
    overview = {"befehl": [], "regel": [], "schluessel": [], "hook": []}

    for c in inv["befehle"]:
        card = Card(files)
        slug = slug_for("befehl", c["name"])
        alias = f" (Alias {', '.join(f'`{a}`' for a in c['aliases'])})" if c["aliases"] else ""
        body = [f"# Befehl `{c['name']}`", "",
                f"Der Unterbefehl `{c['name']}`{alias} steht im Parser mit der Hilfe „{c['help']}“."
                + card.ref(c["datei"], c["line"], c["end"])]
        if c.get("func"):
            body[-1] += f" Ausgeführt wird er in `{c['func']['name']}`." + card.ref(c["datei"], c["func"]["line"],
                                                                                         c["func"]["end"])
        body += ["", "## Im Code", ""]
        for o in c["options"]:
            req = " (Pflicht)" if o["required"] else ""
            hilfe = ": " + re.sub(r"(\[\^[^\]]*\])", r"`\1`", o["help"]) if o["help"] else ""
            flags = " / ".join(f"`{f}`" for f in o["flags"])
            doc = "" if o["doku"] or not o["flags"][0].startswith("-") else " – in keiner Doku-Datei"
            body.append(f"- {flags}{req}{hilfe}{doc}.{card.ref(c['datei'], o['line'])}")
        if not c["options"]:
            body.append("- keine Optionen.")
        body += ["", "## In der Doku", ""] + (card.hitlist(c["doku"]) or ["- keine Fundstelle in SKILL.md, README.md, Rundgang, Entwicklung, "
                                                                          "CHANGELOG.md oder konfiguration.md."])
        body += ["", "## Tests", ""] + (card.hitlist(c["tests"]) or ["- kein Test ruft den Befehl auf (Textsuche)."])
        notes = befund_lines(c, "befehl")
        notes += [f"Option {' / '.join(o['flags'])} in keiner Doku-Datei" for o in c["options"]
                  if not o["doku"] and o["flags"][0].startswith("-")]
        result[slug] = {"title": f"Befehl {c['name']}", "kind": "befehl", "card": card, "body": body, "befund": notes,
                        "description": f"Unterbefehl {c['name']}: {c['help']}", "aliases": [c["name"], *c["aliases"]]}
        overview["befehl"].append(slug)

    for r in inv["regeln"]:
        card = Card(files)
        slug = slug_for("regel", r["name"])
        body = [f"# Lint-Regel `{r['name']}`", ""]
        if r.get("severity"):
            body.append(f"Standardschwere im Kern: {r['severity']}." + card.ref(r["severity_datei"], r["severity_line"]))
        else:
            body.append("Der Code steht nicht in SEVERITY; `severity()` nimmt dann warn.")
        body += ["", "## Im Code", ""]
        body += [f"- gemeldet in {Path(files[d][0]).name}, Z. {n}.{card.ref(d, n)}" for d, n in r["emitted"]] or ["- kein `add(…)` im Kern."]
        if r.get("doku_severity"):
            body.append(f"- konfiguration.md führt ihn unter {r['doku_severity'][0]}."
                        + card.ref("konf", r["doku_severity"][1]))
        body += ["", "## In der Doku", ""] + (card.hitlist(r["doku"]) or ["- keine Fundstelle."])
        body += ["", "## Tests", ""] + (card.hitlist(r["tests"]) or ["- kein Test nennt den Code."])
        result[slug] = {"title": f"Lint-Regel {r['name']}", "kind": "regel", "card": card, "body": body,
                        "befund": befund_lines(r, "regel"), "aliases": [r["name"]],
                        "description": f"Lint-Code {r['name']}, Standardschwere {r.get('severity', '?')}"}
        overview["regel"].append(slug)

    for k in inv["schluessel"]:
        card = Card(files)
        slug = slug_for("schluessel", k["name"])
        shown = k["name"].replace(".*.", ".<typ>.") if k["name"].startswith("types") else k["name"]
        parts = shown.split(".")
        label = f"[{'.'.join(parts[:-1])}] {parts[-1]}" if len(parts) > 1 else f"[{shown}]"
        body = [f"# Schlüssel `{label}`", ""]
        if k["default"]:
            body.append("Standardwert im Kern (DEFAULT_CONFIG bzw. TYPE_DEFAULTS)."
                        + "".join(card.ref(k["default_datei"], n) for n in k["default"][:MAX_HITS]))
        elif k["im_code"]:
            body.append("Ohne Eintrag in DEFAULT_CONFIG; der Code liest ihn mit eigenem Standard.")
        else:
            body.append("Weder DEFAULT_CONFIG noch ein Lesezugriff im Kern oder Hook nennt diesen Schlüssel.")
        body += ["", "## Im Code", ""]
        for where, lines in k["read"].items():
            name = Path(files[where][0]).name
            body.append(f"- gelesen in {name}, Z. {', '.join(map(str, lines[:MAX_HITS]))}"
                        + (f", {len(lines) - MAX_HITS} weitere" if len(lines) > MAX_HITS else "")
                        + "." + "".join(card.ref(where, n) for n in lines[:MAX_HITS]))
        if not k["read"]:
            body.append("- kein Lesezugriff gefunden (AST-Suche nach cfg[…] und .get(…)).")
        if k["profile"]:
            body += ["", "## Profile", ""] + card.hitlist({p: v for p, v in k["profile"].items() if v})
        body += ["", "## In der Doku", ""] + (card.hitlist(k["doku"]) or ["- keine Fundstelle."])
        body += ["", "## Tests", ""] + (card.hitlist(k["tests"]) or ["- kein Test setzt den Schlüssel (Textsuche)."])
        result[slug] = {"title": f"Schlüssel {shown}", "kind": "schluessel", "card": card, "body": body,
                        "befund": befund_lines(k, "schluessel"), "description": f"wiki.toml-Schlüssel {label}",
                        "aliases": [label]}
        overview["schluessel"].append(slug)

    for h in inv["hooks"]:
        card = Card(files)
        slug = slug_for("hook", h["name"])
        body = [f"# Hook `{h['name']}`", "",
                f"`okf_hook.py {h['name']}` ruft `{h['func']['name']}` auf." + card.ref("hook", h["line"])
                + card.ref("hook", h["func"]["line"], h["func"]["end"]), "", "## Eintrag", ""]
        for label, events in h["events"].items():
            name = Path(files[label][0]).name
            for e in events:
                matcher = f", Matcher `{e['matcher']}`" if e["matcher"] else ""
                body.append(f"- {name}: {e['event']}{matcher}, Zeitlimit {e['timeout']} s.{card.ref(label, e['line'])}")
        body += ["", "## In der Doku", ""] + (card.hitlist(h["doku"]) or ["- keine Fundstelle."])
        body += ["", "## Tests", ""] + (card.hitlist(h["tests"]) or ["- kein Test ruft den Hook auf."])
        notes = befund_lines(h, "hook")
        notes += [f"{a['feld']} weicht ab: hooks.json {a['hooksjson']!r}, settings.example.json {a['settings']!r}"
                  for a in inv["abweichungen"] if a["art"] == "hook" and a["name"] == h["name"]]
        result[slug] = {"title": f"Hook {h['name']}", "kind": "hook", "card": card, "body": body, "befund": notes,
                        "description": f"Claude-Code-Hook {h['name']} aus okf_hook.py", "aliases": [h["name"]]}
        overview["hook"].append(slug)

    titles = {"befehl": "Unterbefehle", "regel": "Lint-Regeln", "schluessel": "Konfigurationsschlüssel",
              "hook": "Hooks"}
    for kind, slugs in overview.items():
        for i, slug in enumerate(slugs):
            nb = slugs[(i + 1) % len(slugs)]
            result[slug]["body"] += ["", "## Befund (mechanisch)", ""]
            result[slug]["body"] += [f"- {b}." for b in result[slug]["befund"]] or ["- keine Lücke gefunden."]
            result[slug]["body"] += ["", "## Verwandte Seiten", "",
                                     f"- [{titles[kind]}](/concepts/uebersicht-{kind}.md)",
                                     f"- [{result[nb]['title']}](/concepts/{nb}.md)"]
        card = Card(files)
        how = {"befehl": " (build_parser).", "regel": " (SEVERITY und add(…)).",
               "schluessel": " (DEFAULT_CONFIG, Lesezugriffe).", "hook": " (Handler in main)."}[kind]
        body = [f"# {titles[kind]}", "", f"{len(slugs)} Karten, mechanisch aus dem Code gewonnen{how}", "",
                "| Karte | Befund (mechanisch) |", "|---|---|"]
        for slug in slugs:
            body.append(f"| [{result[slug]['title']}](/concepts/{slug}.md) | {'; '.join(result[slug]['befund']) or '–'} |")
        if not card.refs:   # Fußnote Pflicht: auf die Stelle, die die Liste trägt
            anchor = {"befehl": lambda: (inv["befehle"][0]["datei"], inv["befehle"][0]["line"]),
                      "hook": lambda: ("hook", inv["hooks"][0]["line"]),
                      "regel": lambda: next((r["severity_datei"], r["severity_line"]) for r in inv["regeln"] if r.get("severity_line")),
                      "schluessel": lambda: next((k["default_datei"], k["default"][0]) for k in inv["schluessel"] if k["default"])
                      }[kind]()
            body[2] = body[2][:-1] + "." + card.ref(*anchor)
        others = [k for k in titles if k != kind]
        body += ["", "## Verwandte Seiten", ""] + [f"- [{titles[o]}](/concepts/uebersicht-{o}.md)" for o in others]
        result[f"uebersicht-{kind}"] = {"title": titles[kind], "kind": "uebersicht", "card": card, "body": body,
                                        "befund": [], "description": f"Alle Karten der Art {titles[kind]}",
                                        "aliases": [{"befehl": "Subcommands", "regel": "Lint-Codes",
                                                     "schluessel": "wiki.toml", "hook": "Claude-Code-Hooks"}[kind]]}
    for entry in result.values():
        entry["body"] += [""] + entry["card"].footnotes()
    return result


# ---------------------------------------------------------------------------------------------- Wiki bauen

class Core:
    def __init__(self, kern: Path, root: Path):
        self.kern, self.root = kern, root

    def __call__(self, *args: str, stdin: str | None = None) -> str:
        done = subprocess.run(["uv", "run", "--quiet", "--script", str(self.kern), "--root", str(self.root), *args],
                              capture_output=True, text=True, input=stdin, cwd=self.root, timeout=300)
        if done.returncode not in (0, 1) or (done.returncode == 1 and args[0] != "lint"):
            raise SystemExit(f"okfw {' '.join(args[:3])} …: Exit {done.returncode}\n{done.stdout}{done.stderr}")
        return done.stdout


def unlock(path: Path) -> None:
    for p in path.rglob("*"):
        if not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


def befunde_md(inv: dict, made: dict, sha: str, version: str) -> str:
    lines = [f"# Selbstreview okfw {version}: erste Befundliste (mechanisch)", "",
             f"Commit `{sha}`. Erzeugt von `tests/selbstreview.py`; Textsuche, also Näherung.", ""]
    count = {k: sum(1 for v in made.values() if v["kind"] == k) for k in ("befehl", "regel", "schluessel", "hook")}
    lines += [f"Karten: {sum(count.values())} ({count['befehl']} Befehle, {count['regel']} Lint-Regeln, "
              f"{count['schluessel']} Schlüssel, {count['hook']} Hooks) und 4 Übersichten.", ""]
    groups = [("Im Code, aber in keiner Doku", lambda v: any("keiner" in b for b in v["befund"])),
              ("Ohne Test", lambda v: any("in keinem Test" in b or "kein Test" in b for b in v["befund"])),
              ("Weitere mechanische Befunde", lambda v: any(not ("keiner" in b or "in keinem Test" in b)
                                                            for b in v["befund"]))]
    for title, pick in groups:
        rows = [(slug, v) for slug, v in made.items() if v["kind"] != "uebersicht" and pick(v)]
        lines += [f"## {title} ({len(rows)})", ""]
        for slug, v in rows:
            extra = [b for b in v["befund"] if (title.startswith("Weitere") and not ("keiner" in b
                     or "in keinem Test" in b)) or (title.startswith("Im Code") and "keiner" in b)]
            lines.append(f"- `{slug}`" + (": " + "; ".join(extra) if extra else ""))
        lines.append("")
    lines += [f"## In der Doku, aber nicht im Code ({len(inv['doku_ohne_code'])})", ""]
    seen = set()
    for d in inv["doku_ohne_code"]:
        key = (d["art"], d["name"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {d['art']} `{d['name']}` ({d.get('quelle', 'Doku')}: {d['datei']}, Z. {d['zeile']})")
    lines += ["", f"## Doku und Code weichen ab ({len(inv['abweichungen'])})", ""]
    for a in inv["abweichungen"]:
        lines.append("- " + ", ".join(f"{k}: {v}" for k, v in a.items()))
    lines += ["", "## Zwei Namen für eine Sache (Aliase im Parser)", ""]
    for b in inv["begriffe"]:
        opts = "; ".join(" = ".join(o) for o in b["optionen"])
        lines.append(f"- `{b['befehl']}`" + (f" = {', '.join(b['aliase'])}" if b["aliase"] else "")
                     + (f"; Optionen {opts}" if opts else ""))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rev", default="HEAD", help="Commit, dessen Stand geprüft wird (Standard HEAD)")
    ap.add_argument("--out", help="Zielordner (Standard .nachbau/selbstreview-<version>)")
    ap.add_argument("--neu", action="store_true", help="vorhandenen Zielordner ersetzen")
    args = ap.parse_args(argv)
    if shutil.which("uv") is None:
        raise SystemExit("uv fehlt im PATH")

    probe = git("grep", "-h", "^VERSION = ", args.rev, "--", "skills/okfw/okfw.py", "skills/okfw/okfw_kern/basis.py")
    version = re.search(r'^VERSION = "([^"]+)"', probe, re.M).group(1)
    out = Path(args.out or PLUGIN / ".nachbau" / f"selbstreview-{version}").resolve()
    if out.exists():
        if not args.neu:
            raise SystemExit(f"{out} gibt es schon (--neu ersetzt ihn)")
        unlock(out)
        shutil.rmtree(out)
    stand = out / "_stand"
    sha, date, tests = stage(args.rev, stand)
    files = {label: (rel, art) for label, rel, art in DOKU + CODE + kern_code(stand) + tests}
    print(f"Stand {sha[:12]} ({date}), okfw {version} → {out}")

    inv = collect(stand, tests)
    made = cards(inv, files)
    (out / "inventar.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "befunde.md").write_text(befunde_md(inv, made, sha, version), encoding="utf-8")

    core = Core(stand / "skills/okfw/okfw.py", out)
    core("init", "--profile", "dokumentation")
    toml = out / "wiki.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'title = "Wiki – zuerst lesen"', f'title = "okfw {version} – Selbstreview, zuerst lesen"'), encoding="utf-8")
    fmt = {label: ("documentation" if label in dict((d[0], 1) for d in DOKU) else "code") for label in files}
    for label, (rel, art) in files.items():
        slug = f"okfw-{label}"
        url = f"{REPO_URL}/blob/main/{rel}"
        core("capture", slug, "--url", url, "--commit", sha, "--file", f"{stand / rel}={rel}",
             "--license", "MIT", "--license-url", LICENSE_URL,
             "--note", f"okfw {version}, Plugin-Ordner am Commit {sha[:12]} des Entwicklungs-Repos")
        core("new", "source", slug, "--title", f"{rel} (okfw {version})", "--description", art,
             "--access", "fulltext", "--resource", url, "--set", f"raw_packet=raw/{slug}", "--set", f"format={fmt[label]}",
             "--set", f"date={date}", "--fundweg", "material")
    for slug, entry in made.items():
        core("new", "concept", slug, "--title", entry["title"], "--description", entry["description"],
             "--set", "aliases=" + json.dumps(entry["aliases"], ensure_ascii=False))
        refs = entry["card"].refs
        if refs:
            core("sources", f"concepts/{slug}", "--add", ",".join(f"{fid}=okfw-{lab}" for fid, (lab, _) in refs.items()))
        core("body", f"concepts/{slug}", "-", stdin="\n".join(entry["body"]) + "\n")
    core("rebuild", "--quiet")
    print(core("lint", "--summary").strip())
    n = sum(1 for v in made.values() if v["kind"] != "uebersicht")
    print(f"{n} Konzeptkarten und 4 Übersichten; Befunde: {out / 'befunde.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
