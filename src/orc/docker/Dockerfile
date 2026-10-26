# The black box every peon works in. Built locally by `orc build-image`.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates curl ripgrep procps \
        python3 python3-venv python3-pip python3-pytest python-is-python3 \
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

# Build and test leftovers never belong in a peon's branch, even when the
# target repo has no .gitignore (orc commits whatever is left in the tree).
RUN mkdir -p /home/node/.config/git && printf '%s\n' \
    '__pycache__/' '*.py[cod]' '.pytest_cache/' '.mypy_cache/' '.ruff_cache/' '.venv/' \
    'node_modules/' '.npm/' '.cache/' '*.log' '.DS_Store' \
    > /home/node/.config/git/ignore
