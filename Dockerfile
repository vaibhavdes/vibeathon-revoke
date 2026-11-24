# Revoke: both legacy systems, the Synapse-generated MCP servers and the console in one container.
#   docker build -t revoke . && docker run -p 7860:7860 revoke              # replay-only, no key
#   docker run -p 7860:7860 -e GROQ_API_KEY=... revoke                      # live questions too
# PORT and HOST can be overridden by whatever runs the container.

# Synapse CLI (Node) is needed in the container for the live update: "Ship endpoint + rebuild".
FROM node:20-bookworm-slim AS synapse
# tree-sitter (used by Synapse to read code) compiles native modules when no prebuilt binary fits.
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=7860 HOST=0.0.0.0 REVOKE_HOSTED=1 SYNAPSE_TELEMETRY=0
COPY --from=synapse /usr/local/bin/node /usr/local/bin/node
RUN useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY --chown=app:app . .
COPY --from=synapse --chown=app:app /app/node_modules ./node_modules
RUN mkdir -p runtime evidence/runs && chown app:app /app runtime evidence evidence/runs
USER app
EXPOSE 7860
CMD ["python", "-m", "scripts.start", "--fresh"]
