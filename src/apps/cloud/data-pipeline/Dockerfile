# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/cloud/data-pipeline/Dockerfile -t data-pipeline .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd -r -u 1001 pipeline

COPY apps/cloud/data-pipeline ./apps/cloud/data-pipeline
RUN pip install --no-cache-dir ./apps/cloud/data-pipeline

USER pipeline

ENTRYPOINT ["python", "-m", "data_pipeline.run"]
CMD ["ingest-raw"]
