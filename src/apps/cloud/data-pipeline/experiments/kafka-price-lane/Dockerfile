# 실험 전용 이미지 — 운영 Dockerfile(apps/cloud/data-pipeline/Dockerfile)과 같은 설치에
# confluent-kafka 만 더한다. 운영 이미지에는 Kafka 클라이언트가 없다.
# Build context = src/ (compose.yaml 이 지정)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY libs/ontology ./libs/ontology
COPY apps/cloud/data-pipeline ./apps/cloud/data-pipeline
RUN pip install --no-cache-dir ./libs/ontology ./apps/cloud/data-pipeline "confluent-kafka==2.12.0"
