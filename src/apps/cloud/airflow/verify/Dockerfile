# 격리 검증 태스크 이미지(ALPHA-1119) — **배포된 업무 이미지** + shim. 업무 코드는 한 줄도 바꾸지 않는다.
# BASE 는 검증 시점에 도는 data-pipeline 이미지의 다이제스트로 준다(verify/run.sh build).
# RUN 이 없다(x86_64 업무 이미지를 arm64 개발기에서 빌드해도 에뮬레이션 불필요).
# Build context = src/apps/cloud/airflow/verify
ARG BASE
FROM ${BASE}
COPY shim.py /verify/shim.py
