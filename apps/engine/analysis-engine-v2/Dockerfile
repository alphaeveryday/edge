# syntax=docker/dockerfile:1
# Build context: src/. The v1 image and entrypoint are unchanged.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RDS_CA_PATH=/app/rds-ca.pem
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl --fail --silent --show-error https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem -o /app/rds-ca.pem \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 analysis
COPY apps/engine/analysis-engine-v2 ./analysis-engine-v2
# Dependencies come from the workspace lock (requirements.lock is exported from uv.lock), so two
# builds of one commit install the same versions. Regenerate it with the command in its header.
ARG IMAGE_REVISION=unknown
ENV IMAGE_REVISION=$IMAGE_REVISION
RUN pip install --no-cache-dir --require-hashes -r ./analysis-engine-v2/requirements.lock \
    && pip install --no-cache-dir --no-deps ./analysis-engine-v2 \
    && python -c "from importlib.resources import files; root = files('edge_analysis_v2'); needed = ['cloud/request.schema.json', 'prompts/outlook.yaml', 'prompts/movement.yaml', 'prompts/base.md', 'prompts/skills.md', 'prompts/question.md'] + ['skills/' + name + '/SKILL.md' for name in ('cause-investigation', 'analysis-report-writing', 'hypothesis-analysis-workflow', 'etf-hypothesis-analysis')]; missing = [name for name in needed if not root.joinpath(name).is_file()]; assert not missing, missing"
VOLUME ["/tmp"]
USER analysis
ENTRYPOINT ["python", "-m", "edge_analysis_v2.cloud.worker"]
