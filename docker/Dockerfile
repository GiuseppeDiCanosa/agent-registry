# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/container-deployment/spec.md
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    AGENT_REGISTRY_HOME=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client curl ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dipendenze: sia quelle degli script (wiki-ingest) sia quelle della webapp.
COPY scripts/requirements.txt scripts/requirements.txt
COPY scripts/webapp/requirements.txt scripts/webapp/requirements.txt
RUN pip install --no-cache-dir \
    -r scripts/requirements.txt \
    -r scripts/webapp/requirements.txt

# Codice della skill + notifier + entrypoint di sync.
COPY scripts/ scripts/
COPY notifier/ notifier/
COPY docker/ docker/

RUN mkdir -p /data
VOLUME ["/data"]

# Comando di default: sovrascritto per servizio nel compose.
CMD ["sleep", "infinity"]
