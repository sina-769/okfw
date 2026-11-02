"""Wiki: Sichtbarkeit und öffentliche Ausgabe (sicherheitskritisch): was hidden ist und was eine öffentliche Ansicht nicht enthalten darf."""

import re

from .basis import CODE_RE, Page, WikiError, as_list, is_empty


class WikiSicht:
    def scope_values(self) -> list[str]:
        """Werte des Umfangsfelds in Rangfolge vom Kern nach außen (aus [enums])."""
        field = self.cfg["scope"].get("field")
        return [str(v) for v in self.cfg["enums"].get(field, [])] if field else []

    def scopes(self) -> dict[str, tuple[str, bool]]:
        """{Seiten-ID: (Umfang, abgeleitet)}.

        Eigener Wert geht vor. Quellen ohne Wert erben den engsten Umfang der Seiten, die sie verlinken
        oder in sources[] führen ([scope] derive); abgeleitet wird nur hier, geschrieben wird nichts.
        """
        field = self.cfg["scope"].get("field")
        if not field:
            return {}
        own = {}
        for p in self.pages():
            values = [str(v) for v in as_list((p.fm or {}).get(field)) if not is_empty(v)]
            if values:
                own[p.id] = values[0]
        out = {pid: (v, False) for pid, v in own.items()}
        if self.cfg["scope"].get("derive", True):
            order = self.scope_values()
            rank = lambda v: order.index(v) if v in order else len(order)
            sources = {q.id for q in self.source_cards()}
            best: dict[str, str] = {}
            for p, q, _ in self.edges():
                if q.id in sources and q.id not in own and p.id not in sources and p.id in own:
                    if q.id not in best or rank(own[p.id]) < rank(best[q.id]):
                        best[q.id] = own[p.id]
            out.update({qid: (v, True) for qid, v in best.items()})
        return out

    def visibility_values(self) -> list[str] | None:
        """Erlaubte Werte des Sichtbarkeitsfelds: aus [enums], sonst default und hidden; None ohne Angabe."""
        spec = self.cfg["visibility"]
        field = spec.get("field")
        if field in self.cfg["enums"]:
            return [str(v) for v in self.cfg["enums"][field]]
        if spec.get("default"):
            return [str(spec["default"]), *map(str, as_list(spec.get("hidden")))]
        return None

    def visibility(self) -> dict[str, tuple[str, bool]]:
        """{Seiten-ID: (Sichtbarkeit, abgeleitet)}; leer ohne [visibility] field.

        Eigener Wert geht vor, sonst [visibility] default. Quellenkarten ohne eigenen Wert, die nur von
        verborgenen Seiten verlinkt oder in sources[] geführt werden, gelten selbst als verborgen ([visibility]
        derive), damit die Quellenauswahl eine interne Linie nicht verrät. Abgeleitet wird nur hier.
        """
        spec = self.cfg["visibility"]
        field = spec.get("field")
        if not field:
            return {}
        default = str(spec.get("default") or "")
        hidden = {str(v) for v in as_list(spec.get("hidden"))}
        out, own = {}, set()
        for p in self.pages():
            values = [str(v) for v in as_list((p.fm or {}).get(field)) if not is_empty(v)]
            if values:
                own.add(p.id)
            out[p.id] = (values[0] if values else default, False)
        if spec.get("derive", True) and hidden:
            sources = {q.id for q in self.source_cards()}
            users: dict[str, set[str]] = {}
            for p, q, _ in self.edges():
                if q.id in sources and q.id not in own:
                    users.setdefault(q.id, set()).add(p.id)
            # Quellen, die nur verborgene Quellen nutzen, erst im nächsten Durchgang
            changed = True
            while changed:
                changed = False
                for qid, ids in users.items():
                    if out[qid][0] not in hidden and all(out[i][0] in hidden for i in ids):
                        out[qid] = (min(out[i][0] for i in ids), True)
                        changed = True
        return out

    def hidden_ids(self) -> set[str]:
        """IDs der Seiten, die in der öffentlichen Ausgabe fehlen: Wert aus [visibility] hidden oder unbekannter
        Wert (ein Tippfehler soll eine Seite nicht veröffentlichen; der Lint meldet ihn als enum_invalid). Ohne
        bekannte Werte ist jeder Wert unbekannt (Lint visibility_values_missing)."""
        hidden = {str(v) for v in as_list(self.cfg["visibility"].get("hidden"))}
        allowed = self.visibility_values()
        ids = {pid for pid, (v, _) in self.visibility().items()
               if v in hidden or (v and (allowed is None or v not in allowed))}
        # eigener Text eines Pfads ist nicht geschützt, nur weil seine Karten es sind: ohne eigenen Wert bleibt er intern
        field = self.cfg["visibility"].get("field")
        return ids | {p.id for p in self.pfade() if field and is_empty(p.fm.get(field))}

    def visibility_leaks(self) -> list[tuple[Page, list[str]]]:
        """Öffentliche Seiten, die verborgene verlinken oder in sources[] führen, mit den Zielen."""
        hidden = self.hidden_ids()
        leaks: dict[str, tuple[Page, set[str]]] = {}
        for p, q, _ in self.edges():
            if p.id not in hidden and q.id in hidden:
                leaks.setdefault(p.id, (p, set()))[1].add(f"/{q.id}.md")
        return [(p, sorted(targets)) for p, targets in leaks.values()]

    def _drop_withheld(self, page: Page, body: str, refs: list) -> tuple[str, list]:
        """Öffentliche Ausgabe: Belege auf weggelassene Seiten entfallen samt Fußnote und Literaturangabe."""
        gone = {label for label, _, entry in refs if self.ref_path(page, entry) in self.withheld}
        if not gone:
            return body, refs
        alt = "|".join(map(re.escape, sorted(gone)))

        def clean(segment: str) -> str:
            segment = re.sub(rf"^\[\^(?:{alt})\]:.*\n?(?:[ \t]+\S.*\n?)*", "", segment, flags=re.M)
            return re.sub(rf"[ \t]*\[\^(?:{alt})\](?!:)", "", segment)

        parts = re.split(f"({CODE_RE.pattern})", body, flags=re.S)
        body = "".join(clean(seg) if i % 2 == 0 else seg for i, seg in enumerate(parts))
        return body, [r for r in refs if r[0] not in gone]

    def public_view(self) -> "Wiki":
        """Dasselbe Wiki ohne die Seiten aus hidden_ids(): Graph, Tabelle, Leseansicht, Übersichten, Kennzahlen,
        Literaturverzeichnis und Vorschauen entstehen dann nur aus öffentlichen Seiten."""
        if not self.cfg["visibility"].get("field") or not as_list(self.cfg["visibility"].get("hidden")):
            raise WikiError("öffentliche Ausgabe braucht [visibility] field und hidden in der wiki.toml")
        if self.visibility_values() is None:
            raise WikiError(f"öffentliche Ausgabe braucht bekannte Werte für {self.cfg['visibility']['field']}: "
                            "[enums] oder [visibility] default in der wiki.toml (lint: visibility_values_missing)")
        hidden = self.hidden_ids()
        view = type(self)(self.root, self.cfg, self.config_path)
        view.public = True
        view._pages = [p for p in self.pages() if p.id not in hidden]
        view.withheld = {p.path.resolve() for p in self.pages() if p.id in hidden}
        return view
