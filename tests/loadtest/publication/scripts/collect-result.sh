#!/usr/bin/env bash
# 실행 구간(start~end)의 Prometheus 시계열을 결과 디렉터리로 내려받는다.
#
# WHY: 컨테이너를 내리면 Prometheus 의 인메모리·로컬 TSDB 도 함께 사라진다. 실행
# 직후 원본 시계열을 파일로 고정해 둬야 나중에 그래프를 다시 그릴 수 있다.
# 쿼리 하나가 실패해도(지표 미노출·모듈 미탑재) 전체를 세우지 않는다 — 캐시 모드마다
# 존재하는 지표가 다르다(none 모드엔 l2_gets 가 없다).
#
# 사용: scripts/collect-result.sh <run_id> <start_epoch> <end_epoch>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
PROM_URL="${PROM_URL:-http://localhost:19090}"
STEP="5s"

err() { printf '\033[31m[collect] %s\033[0m\n' "$*" >&2; }
warn() { printf '\033[33m[collect] %s\033[0m\n' "$*" >&2; }

usage() {
	cat <<'USAGE'
사용: collect-result.sh <run_id> <start_epoch> <end_epoch>

results/<run_id>/prom/<지표>.json 으로 query_range(step=5s) 결과를 저장한다.
PROM_URL 로 Prometheus 주소를 바꿀 수 있다(기본 http://localhost:19090).
USAGE
}

if [ $# -ne 3 ]; then
	usage >&2
	exit 2
fi

RUN_ID="$1"
START="$2"
END="$3"
OUT_DIR="$STACK_DIR/results/$RUN_ID/prom"

case "$START$END" in
	*[!0-9]*) err "start·end 는 epoch 초여야 한다: $START $END"; exit 2 ;;
esac

mkdir -p "$OUT_DIR"

# 이름<TAB>PromQL. bash 3.2 에 연관배열이 없어 한 줄 = 한 쿼리로 둔다.
# 탭 구분이라 쿼리 안에 탭을 넣지 말 것.
QUERIES=$(cat <<'EOF'
api_rps_by_status	sum by (status) (rate(http_server_requests_seconds_count[30s]))
api_p99_by_instance	histogram_quantile(0.99, sum by (le, instance) (rate(http_server_requests_seconds_bucket[30s])))
l1_gets	sum by (instance, result) (rate(cache_gets_total{cache="publication-serve"}[30s]))
db_loads	sum by (instance) (rate(publication_cache_db_loads_total[30s]))
l2_gets	sum by (result) (rate(publication_cache_l2_gets_total[30s]))
l2_errors	sum by (instance) (rate(publication_cache_l2_errors_total[30s]))
hikari_active	hikaricp_connections_active
hikari_pending	hikaricp_connections_pending
pg_xact	rate(pg_stat_database_xact_commit{datname="edge_onprem"}[30s])
redis_mem_used	redis_memory_used_bytes
redis_mem_max	redis_memory_max_bytes
redis_hits	rate(redis_keyspace_hits_total[30s])
redis_misses	rate(redis_keyspace_misses_total[30s])
redis_ops	rate(redis_commands_processed_total[30s])
jvm_heap	sum by (instance) (jvm_memory_used_bytes{area="heap"})
cpu	process_cpu_usage
EOF
)

FAILED=0
OK=0
while IFS='	' read -r name query; do
	[ -z "$name" ] && continue
	if curl -sfG "$PROM_URL/api/v1/query_range" \
		--data-urlencode "query=$query" \
		--data-urlencode "start=$START" \
		--data-urlencode "end=$END" \
		--data-urlencode "step=$STEP" \
		-o "$OUT_DIR/$name.json"; then
		OK=$((OK + 1))
	else
		warn "쿼리 실패(건너뜀): $name"
		rm -f "$OUT_DIR/$name.json"
		FAILED=$((FAILED + 1))
	fi
done <<EOF
$QUERIES
EOF

printf '[collect] %s: %d개 저장, %d개 실패 -> %s\n' "$RUN_ID" "$OK" "$FAILED" "$OUT_DIR"
if [ "$OK" -eq 0 ]; then
	err "저장된 시계열이 하나도 없다 — Prometheus($PROM_URL)가 떠 있는지 확인하라"
	exit 1
fi
