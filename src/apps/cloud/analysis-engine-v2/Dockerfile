# syntax=docker/dockerfile:1
# Build context: src/. The v1 image and entrypoint are unchanged.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RDS_CA_PATH=/app/rds-ca.pem
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl --fail --silent --show-error https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem -o /app/rds-ca.pem \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 analysis
COPY apps/cloud/analysis-engine-v2 ./analysis-engine-v2
RUN pip install --no-cache-dir './analysis-engine-v2[cloud]' \
    && python -c "from importlib.resources import files; assert (files('edge_analysis_v2.cloud') / 'request.schema.json').is_file(); assert (files('edge_analysis_v2.prompts') / 'outlook.yaml').is_file()"
USER analysis
ENTRYPOINT ["python", "-m", "edge_analysis_v2.cloud.worker"]
