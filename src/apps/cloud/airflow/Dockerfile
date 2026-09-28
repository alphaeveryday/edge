# Airflow 실행 환경 이미지(ALPHA-1119) — 공식 이미지 + 이 저장소의 DAG. 구성요소 셋(api-server·scheduler·
# dag-processor)과 마이그레이션 작업이 **같은 이미지 태그**를 쓰므로 DAG·플러그인 버전이 어긋나지 않는다.
# 서버에 파일을 복사하지 않는다 — DAG 변경 = 새 이미지 = 새 태스크 정의 리비전(deploy-airflow).
# 베이스는 CI(test-airflow.yml)와 같은 다이제스트: apache/airflow:3.3.2-python3.12(amazon provider 9.36.0).
# RUN 이 없다 — 다른 아키텍처(arm64)로 빌드해도 에뮬레이션이 필요 없다.
# Build context = src/apps/cloud/airflow
FROM apache/airflow@sha256:9df9c8be4096b9cc626bd7cb1f2b8c712eef66c59c615f8c7e6200871ca10bd1

COPY dags /opt/edge/dags
COPY deploy/entrypoint.sh /opt/edge/entrypoint.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/edge/entrypoint.sh"]
