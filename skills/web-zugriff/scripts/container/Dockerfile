# Agent-Container für den Skill web-zugriff: Claude Code ohne Shell-Werkzeuge, nicht root.
# Bauen: scripts/web-recherche --bauen (setzt Tag und Versionen). Dasselbe Image dient als Proxy und als PDF-Leser.
FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

ARG CLAUDE_CODE_VERSION
ARG PDFJS_VERSION
RUN test -n "$CLAUDE_CODE_VERSION" && test -n "$PDFJS_VERSION" \
 && npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm install -g --no-fund --no-audit --omit=optional --ignore-scripts "pdfjs-dist@${PDFJS_VERSION}" \
 && npm cache clean --force \
 && rm -rf /root/.npm /tmp/* \
 && useradd --uid 10001 --user-group --no-create-home --home-dir /tmp --shell /usr/sbin/nologin agent

# Richtlinie mit höchstem Vorrang: Claude Code liest sie immer, der Agent kann sie nicht ändern.
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY agent-start.sh /usr/local/bin/agent-start
COPY proxy.mjs pdf-text.mjs /opt/web-zugriff/
RUN chmod 0644 /etc/claude-code/managed-settings.json /opt/web-zugriff/proxy.mjs /opt/web-zugriff/pdf-text.mjs \
 && chmod 0755 /usr/local/bin/agent-start

# HOME liegt im tmpfs /tmp (Wurzeldateisystem ist read-only); nichts davon überlebt den Lauf.
ENV HOME=/tmp \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1

USER 10001:10001
WORKDIR /ausgabe
ENTRYPOINT ["/usr/local/bin/agent-start"]
