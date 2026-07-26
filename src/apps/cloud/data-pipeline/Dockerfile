# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/cloud/data-pipeline/Dockerfile -t data-pipeline .

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd -r -u 1001 pipeline

# edge-ontology 는 uv 워크스페이스 lib(libs/ontology) — 앱보다 먼저 설치해야
# 앱 의존성 해석이 로컬 설치본으로 충족된다(PyPI 에 없는 이름, ALPHA-539).
COPY libs/ontology ./libs/ontology
COPY apps/cloud/data-pipeline ./apps/cloud/data-pipeline
RUN pip install --no-cache-dir ./libs/ontology ./apps/cloud/data-pipeline

USER pipeline

ENTRYPOINT ["python", "-m", "data_pipeline.run"]
CMD ["ingest-raw"]
