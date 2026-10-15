# 로컬 검증 이미지 — Airflow 공식 이미지 + 업무 코드 별도 venv(/opt/dp).
# Airflow 의 고정 의존성과 업무 의존성을 섞지 않는다. ECS 대역(fake_aws)이 /opt/dp 로 업무 코드를 띄운다.
# 업무 소스는 compose 가 /dp-src 로 마운트해 PYTHONPATH 로 우선시킨다(수정 후 재빌드 불필요).
# Build context = src/
FROM apache/airflow:3.3.2-python3.12
USER root
COPY libs/ontology /build/libs/ontology
COPY apps/cloud/data-pipeline /build/apps/cloud/data-pipeline
RUN python -m venv /opt/dp \
 && /opt/dp/bin/pip install --no-cache-dir /build/libs/ontology /build/apps/cloud/data-pipeline \
 && rm -rf /build \
 && mkdir -p /lab-data && chown airflow: /lab-data
USER airflow
