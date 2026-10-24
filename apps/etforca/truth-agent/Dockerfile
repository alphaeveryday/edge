# syntax=docker/dockerfile:1
# Build context = 레포 루트. Build:
#   docker build -f apps/etforca/truth-agent/Dockerfile -t truth-agent .
#
# 한 이미지에 Python 배치(사건 검사·실행) + Node(pi 하네스) + Chromium(렌더 검사)이 함께 든다.
# pi 세션이 도구를 부를 때마다 같은 컨테이너의 `python -m edge_truth_agent tool <이름>` 을 실행하기 때문이다.

FROM node:22.20.0-bookworm-slim AS pi
WORKDIR /pi
COPY apps/etforca/truth-agent/pi/package.json apps/etforca/truth-agent/pi/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY apps/etforca/truth-agent/pi/extension.ts ./

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TRUTH_PI_DIR=/opt/truth/pi \
    TRUTH_WEB_DIR=/opt/truth/web \
    TRUTH_SEED_DIR=/opt/truth/seed
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
WORKDIR /app

COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /pi /opt/truth/pi

# 외부 의존성은 워크스페이스 lock 에서 뽑은 requirements.lock 그대로 설치한다(같은 커밋 = 같은 버전).
# 다시 만들기: 파일 첫 줄의 uv export 명령. 워크스페이스 패키지(ontology·data-pipeline·truth-agent)는 --no-deps 로 얹는다.
COPY libs/ontology ./libs/ontology
COPY apps/engine/data-pipeline ./apps/engine/data-pipeline
COPY apps/etforca/truth-agent ./apps/etforca/truth-agent
RUN pip install --no-cache-dir --require-hashes -r ./apps/etforca/truth-agent/requirements.lock \
 && pip install --no-cache-dir --no-deps ./libs/ontology ./apps/engine/data-pipeline ./apps/etforca/truth-agent \
 && playwright install --with-deps --only-shell chromium \
 && useradd -r -u 1001 -m truth

COPY apps/etforca/truth-web /opt/truth/web
COPY apps/etforca/truth-agent/seed /opt/truth/seed

USER truth

ENTRYPOINT ["python", "-m", "edge_truth_agent"]
CMD ["run", "hourly"]
