# syntax=docker/dockerfile:1
# Build context = 레포 루트. Build:
#   docker build -f apps/etforca/truth-research/Dockerfile -t truth-research .
#
# truth-agent 이미지와 같은 구조다(Python 배치 + Node pi 하네스). pi 세션이 도구를 부를 때마다
# 같은 컨테이너의 `python -m edge_truth_research tool <이름>` 을 실행한다.
# 화면 렌더 검사가 없어 Chromium·화면 파일·seed 사건은 넣지 않는다(이미지가 작고 기동이 빠르다).

FROM node:22.20.0-bookworm-slim AS pi
WORKDIR /pi
COPY apps/etforca/truth-research/pi/package.json apps/etforca/truth-research/pi/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY apps/etforca/truth-research/pi/extension.ts ./

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRUTH_PI_DIR=/opt/truth/pi \
    RDS_CA_PATH=/app/rds-ca.pem
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
WORKDIR /app

# 국내 보도를 운영 DB 에서 읽는다(dbnews) — 서버 인증서를 RDS CA 로 검증한다(analysis-engine-v2 이미지와 같은 CA 묶음).
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem', '/app/rds-ca.pem')" \
 && grep -q "BEGIN CERTIFICATE" /app/rds-ca.pem

COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /pi /opt/truth/pi

# 외부 의존성은 워크스페이스 lock 에서 뽑은 requirements.lock 그대로 설치한다(같은 커밋 = 같은 버전).
# 다시 만들기: 파일 첫 줄의 uv export 명령. 워크스페이스 패키지(ontology·data-pipeline·truth-agent·truth-research)는 --no-deps 로 얹는다.
COPY libs/ontology ./libs/ontology
COPY apps/engine/data-pipeline ./apps/engine/data-pipeline
COPY apps/etforca/truth-agent ./apps/etforca/truth-agent
COPY apps/etforca/truth-research ./apps/etforca/truth-research
RUN pip install --no-cache-dir --require-hashes -r ./apps/etforca/truth-research/requirements.lock \
 && pip install --no-cache-dir --no-deps ./libs/ontology ./apps/engine/data-pipeline ./apps/etforca/truth-agent ./apps/etforca/truth-research \
 && useradd -r -u 1001 -m truth

USER truth

ENTRYPOINT ["python", "-m", "edge_truth_research"]
CMD ["run"]
