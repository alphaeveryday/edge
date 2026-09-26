# 실험 전용 이미지 — Airflow 공식 이미지 + 업무 코드 별도 venv(/opt/dp).
# 업무 코드는 운영 Dockerfile 과 같은 설치(ontology + data-pipeline)이고, Airflow 의 고정 의존성과
# 섞지 않으려고 venv 를 나눈다. A·B·C 모두 /opt/dp/bin/python 으로 같은 코드를 부른다.
# Build context = src/ (compose.yaml 이 지정)
FROM apache/airflow:3.3.2-python3.12
USER root
COPY libs/ontology /build/libs/ontology
COPY apps/cloud/data-pipeline /build/apps/cloud/data-pipeline
RUN python -m venv /opt/dp \
 && /opt/dp/bin/pip install --no-cache-dir /build/libs/ontology /build/apps/cloud/data-pipeline \
 && rm -rf /build \
 && mkdir -p /lab-data && chown airflow: /lab-data   # named volume 초기 소유자
USER airflow
