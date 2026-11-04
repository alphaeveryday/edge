# syntax=docker/dockerfile:1
# Build context = src/ root. Build:
#   docker build -f apps/engine/data-pipeline/Dockerfile -t data-pipeline .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 배포 워크플로가 이미지 태그(커밋 SHA)를 넣는다 — 원장 run 의 catalog_version(ops/catalog.version())이 이 값을 읽는다.
# 이제까지 주입 경로가 없어 'unknown' 이었다. 원천 관측 manifest 의 code_version(ALPHA-1130)도 같은 값을 쓴다.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
WORKDIR /app

RUN useradd -r -u 1001 pipeline

# 로컬 뉴스 태거(TAGGER_BACKEND=roberta, ALPHA-1446) — CPU 전용 torch 를 PyTorch 색인에서
# 받는다(PyPI 기본 휠은 CUDA 포함이라 수 GB). 버전은 모델 묶음을 내보낸 환경과 같게 고정한다 —
# int8 state_dict 형식이 torch 버전에 묶인다. 앱 COPY 앞에 둬 코드 변경이 이 층(수백 MB)을 다시
# 받지 않게 한다. pyproject 에 안 넣는 이유: 기본 경로(llm)·CI 테스트는 torch 가 필요 없고,
# uv.lock 에 플랫폼별 torch 가 실리는 비용만 남는다.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
 && pip install --no-cache-dir transformers==4.57.6

# edge-ontology 는 uv 워크스페이스 lib(libs/ontology) — 앱보다 먼저 설치해야
# 앱 의존성 해석이 로컬 설치본으로 충족된다(PyPI 에 없는 이름, ALPHA-539).
COPY libs/ontology ./libs/ontology
COPY apps/engine/data-pipeline ./apps/cloud/data-pipeline
RUN pip install --no-cache-dir ./libs/ontology ./apps/cloud/data-pipeline

USER pipeline

ENTRYPOINT ["python", "-m", "data_pipeline.run"]
CMD ["ingest-raw"]
