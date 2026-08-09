# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/cloud/analysis-engine/Dockerfile -t analysis-engine .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd -r -m -u 1001 analysis

COPY libs/ontology ./libs/ontology
COPY apps/cloud/analysis-engine ./apps/cloud/analysis-engine
RUN pip install --no-cache-dir ./libs/ontology ./apps/cloud/analysis-engine

USER analysis

# Default run = today (Asia/Seoul). The Step Functions state machine overrides
# the command to pass --trade-date / --request-id for a specific run.
ENTRYPOINT ["python", "-m", "edge_analysis"]
