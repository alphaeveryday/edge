# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/cloud/data-pipeline/Dockerfile -t data-pipeline .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 배포 워크플로가 이미지 태그(커밋 SHA)를 넣는다 — 원장 run 의 catalog_version(ops/catalog.version())이 이 값을 읽는다.
# 이제까지 주입 경로가 없어 'unknown' 이었다. 원천 관측 manifest 의 code_version(ALPHA-1130)도 같은 값을 쓴다.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
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
