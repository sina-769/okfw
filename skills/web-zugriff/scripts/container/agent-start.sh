#!/bin/sh
# Entrypoint des Agent-Containers. Der Token kommt als erste Zeile über stdin (nicht als Argument,
# nicht in der Container-Konfiguration, also auch nicht in `docker inspect`). Danach ist stdin zu.
set -eu
IFS= read -r CLAUDE_CODE_OAUTH_TOKEN || true
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "agent-start: kein Token auf stdin" >&2
  exit 2
fi
export CLAUDE_CODE_OAUTH_TOKEN
exec claude "$@" </dev/null
