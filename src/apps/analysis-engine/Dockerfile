# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/analysis-engine/Dockerfile -t analysis-engine .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd -r -u 1001 analysis

COPY apps/analysis-engine ./apps/analysis-engine
RUN pip install --no-cache-dir ./apps/analysis-engine

USER analysis

# Default run = today (Asia/Seoul). The Step Functions state machine overrides
# the command to pass --trade-date / --request-id for a specific run.
ENTRYPOINT ["python", "-m", "edge_analysis"]
