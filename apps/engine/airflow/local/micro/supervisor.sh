#!/usr/bin/env bash
# Airflow 세 구성요소를 **한 컨테이너 = 한 cgroup** 에서 띄운다. ECS on EC2 에서 태스크 수준 memory 하나를 세 컨테이너가
# 공유하는 경계와 같다(구성요소·자식 task 프로세스·헬스체크 exec 가 모두 한 상한 안). 하나가 죽으면 전부 내린다
# (ECS essential 컨테이너와 같은 동작). PGAPPNAME 은 메타DB 연결을 구성요소별로 세기 위한 표지다(자식이 상속).
set -u
# 실험 조건: glibc malloc 아레나 수 상한(스레드 많은 프로세스의 단편화 억제). 비우면 glibc 기본(코어 수 × 8).
if [ -n "${MICRO_MALLOC_ARENA_MAX:-}" ]; then export MALLOC_ARENA_MAX="${MICRO_MALLOC_ARENA_MAX}"; fi
PGAPPNAME=af-api airflow api-server --port 8080 &
PGAPPNAME=af-scheduler airflow scheduler &
PGAPPNAME=af-dagproc airflow dag-processor &
wait -n
code=$?
echo "micro: 구성요소 하나가 exit=${code} 로 끝남 — 전부 내린다" >&2
kill 0
exit "${code}"
