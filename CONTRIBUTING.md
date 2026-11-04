# Mitarbeiten

**In short:** Open a pull request against `main` of the public repo [sina-769/okfw](https://github.com/sina-769/okfw); questions and ideas without code go into an issue.
Your branch starts from `main`, `tests/pruefen.sh` ends with exit 0, `CHANGELOG.md` has an entry under "Unveröffentlicht", and the PR template is filled in.
Name every model that took part in your commit lines. Changes that contradict a clause in `docs/richtung.md` are not accepted.
Your PR is not merged here: this repo only holds released versions, so we take your commits over into a private working repo, with you as author.
They come back with the next version, whose release commit names you as co-author, and we then close your PR with a pointer to that version.
The sections below are in German, like the rest of the repo; write in German or English, either is fine.

## Wohin

- **Code und Doku:** Pull Request gegen `main` des öffentlichen Repos [sina-769/okfw](https://github.com/sina-769/okfw). Ein Zweig von `main` pro Änderung hält jeden PR klein genug zum Prüfen.
- **Fragen und Vorschläge ohne Code:** [Issue](https://github.com/sina-769/okfw/issues). Eine Idee, die erst besprochen werden muss, ist im Issue billiger als in einem fertigen PR.

## Was ein PR braucht

- **`tests/pruefen.sh` endet mit Exit 0.** Es ist der einzige Prüfeinstieg, und die CI ruft nur ihn auf; er braucht `uv` ([Entwicklung](docs/entwicklung.md#tests)). Einen Test abzuschwächen, damit er grün wird, gilt nicht.
- **Ein Eintrag im `CHANGELOG.md`** unter „Unveröffentlicht“, damit jede Version sagt, was sich geändert hat.
- **Die ausgefüllte PR-Vorlage** ([.github/pull_request_template.md](.github/pull_request_template.md)). Plan, Abweichungen und die Ausgabe der Prüfung machen den PR ohne Rückfrage lesbar.
- **Alle beteiligten Modelle als Commit-Zeilen:** `Co-Authored-By` für das, was geschrieben hat, `Reviewed-by` für das, was geprüft hat, `Tested-by` für das, was die Tests laufen ließ, jeweils mit Name, Version und Adresse des Anbieters. So bleibt nachvollziehbar, wer woran beteiligt war. Muster und Nachrichtenform stehen in [.claude/CLAUDE.md](.claude/CLAUDE.md).
- **Arbeitest du mit einem Coding-Agenten,** gilt für ihn dieselbe [Anleitung](.claude/CLAUDE.md), samt der Liste dessen, was ein Agent nie tut. Sie steht nur dort, damit es sie nicht zweimal gibt.

## Was nicht angenommen wird

Alles, was einer Klausel in [docs/richtung.md](docs/richtung.md) widerspricht. Die Klauseln legen fest, was okfw ist und was nicht, und ein Beitrag, der sie verschiebt, ändert das Werkzeug für alle. Eine Klausel ändert nur die Person, die das Repo verantwortet, und sie bekommt die Frage als Issue, nicht als PR.

## Wie es weitergeht

1. Dein PR wird **nicht** im öffentlichen Repo gemergt. Dort liegt nur, was geprüft veröffentlicht ist.
2. Ein angenommener Beitrag wird in das Arbeitsrepo übernommen, **mit dir als Autor**. Jede Version durchläuft vor dem Export Prüfungen, die dort laufen: Nachbau in frischer Umgebung, Diff-Review und Prüfungen auf Privates ([Release-Schritte](docs/entwicklung.md#release-schritte)).
3. Dein Beitrag kommt mit der nächsten Version zurück, und deren Release-Commit nennt dich als **Co-Author**.
4. Dann schließen wir deinen PR mit Verweis auf die Version.

Das Arbeitsrepo ist privat, weil es Kursmaterial unter fremder Lizenz trägt. Darum lädt es keine Fremden ein, und der Weg über den PR ist der einzige.
