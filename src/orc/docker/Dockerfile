# The black box every peon works in. Built locally by `orc build-image`.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl python3 python3-venv ripgrep procps \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# Agents run as `node`: Claude Code refuses --dangerously-skip-permissions as root.
USER node
WORKDIR /home/node
ENV PATH="/home/node/.local/bin:${PATH}"

# Cursor CLI, from Cursor's official installer (installs into ~/.local/bin).
RUN curl https://cursor.com/install -fsS | bash

RUN git config --global user.name "orc peon" \
    && git config --global user.email "peon@orc.local" \
    && git config --global init.defaultBranch main \
    && git config --global advice.detachedHead false
