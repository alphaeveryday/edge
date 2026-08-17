#!/usr/bin/env bash
# 캐시 실험 스택(edge-pubcache)의 게시 데이터 시드 — 멱등.
#
# WHY: 캐시 실험은 "200 이 나오는 조회"를 측정해야 의미가 있다. 게시분이 없으면
# 서빙은 204 로 수렴하고(ExplanationService.serve), 그건 캐시가 아니라 negative
# 캐시만 재는 실험이 된다. 그래서 마지막에 실제 HTTP 200 을 확인하고 끝낸다.
#
# 스키마 SSOT: src/libs/schema/migrations-onprem — INSERT 컬럼은 거기서 확인했다.
#   analysis_item NOT NULL: explanation_result_id, etf_instrument_id, trade_date,
#                           explanation_as_of, analysis_type(기본), explanation_type,
#                           summary, status(기본), received_at/updated_at(기본)
#   publication  NOT NULL: analysis_item_id, etf_ticker, trade_date, status(기본),
#                          published_at(기본), explanation_as_of(V202608041130)
#   policy_version NOT NULL: version_no, disclaimer_text, auto_publish_enabled(기본)
#
# 사용: scripts/prepare-data.sh [--hot 069500] [--cold-count 31] [--synthetic N]
#                              [--reset-scope] [--new-snapshot <ticker>] [--block <ticker>]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE=(docker compose -p edge-pubcache -f "$STACK_DIR/docker-compose.yml")

# 분석 유니버스 — 루트 docker-compose.yml 의 PUBLICATION_KNOWN_TICKERS 와 같은 목록.
# 여기 없는 종목을 조회하면 서빙이 404 를 낸다(allowlist 판정, ExplanationStore.isKnownTicker).
ALL_TICKERS="069500 305720 091160 0005G0 0093A0 0167A0 0176P0 0182R0 0190G0 0210A0 \
091230 139260 261060 266370 300950 363580 388420 395160 395270 396500 455850 469150 \
471760 471780 471990 474590 475300 475310 476260 482030 486240 488210 494220"

HOT="069500"
COLD_COUNT=31
SYNTHETIC=0
RESET_SCOPE=0
NEW_SNAPSHOT=""
BLOCK_TICKER=""

err() { printf '\033[31m[prepare-data] %s\033[0m\n' "$*" >&2; }
info() { printf '[prepare-data] %s\n' "$*"; }

usage() {
	cat <<'USAGE'
사용: prepare-data.sh [옵션]

  --hot <ticker>            핫키 종목 (기본 069500)
  --cold-count <n>          핫키 외 시드할 종목 수 (기본 31)
  --synthetic <n>           합성 티커 LT00001..LT<n> 을 시드 (워킹셋 스윕용, 33종 유니버스 대체)
  --reset-scope             실험이 넣은 serving_scope 차단 행 삭제
  --new-snapshot <ticker>   더 새로운 explanation_as_of 스냅샷 1쌍 추가 ("유효 최신 승리" 전환 유도)
  --block <ticker>          serving_scope INSTRUMENT enabled=false 로 차단 (204 전환 유도)
  -h, --help                이 도움말

옵션 없이 실행하면 기본 시드(정책 버전·analysis_item·publication) 후 HTTP 200 을 확인한다.
--new-snapshot·--block 은 측정 중간에 상태를 흔드는 액션이라 200 확인을 하지 않는다.
USAGE
}

while [ $# -gt 0 ]; do
	case "$1" in
		--hot) HOT="${2:?--hot 에 티커가 필요하다}"; shift 2 ;;
		--cold-count) COLD_COUNT="${2:?--cold-count 에 숫자가 필요하다}"; shift 2 ;;
		--synthetic) SYNTHETIC="${2:?--synthetic 에 숫자가 필요하다}"; shift 2 ;;
		--reset-scope) RESET_SCOPE=1; shift ;;
		--new-snapshot) NEW_SNAPSHOT="${2:?--new-snapshot 에 티커가 필요하다}"; shift 2 ;;
		--block) BLOCK_TICKER="${2:?--block 에 티커가 필요하다}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) err "알 수 없는 옵션: $1"; usage >&2; exit 2 ;;
	esac
done

# psql 은 컨테이너 안에서 돈다 — 호스트에 클라이언트가 없어도 되게(README 관례).
psql_run() {
	"${COMPOSE[@]}" exec -T postgres psql -U edge -d edge_onprem -v ON_ERROR_STOP=1
}

# 서빙 문구 패딩 — 실측 응답 크기(약 1.5KB)를 흉내낸다. 한글 1자 = UTF-8 3바이트라
# 500자로 자르면 1.5KB 근처가 된다. 캐시 메모리·직렬화 비용이 현실적인 크기여야 한다.
SUMMARY_SQL="left(repeat('본 자료는 부하 실험용으로 생성된 합성 설명 문구이며 실제 시세·공시와 무관합니다. ', 40), 500)"

# ── 1. 정책 버전 ─────────────────────────────────────────────────────────────
# 활성 행이 0건이면 발행한다. 활성 1건 불변식의 arbiter 는 부분 유니크 인덱스
# (uq_policy_version_active)라 여기서 조건부 INSERT 로만 접근한다.
seed_policy() {
	psql_run <<SQL
INSERT INTO policy_version (version_no, disclaimer_text, auto_publish_enabled, activated_at)
SELECT COALESCE(max(version_no), 0) + 1,
       '본 자료는 부하실험용 합성 데이터이며 투자 권유가 아닙니다.',
       TRUE, now()
  FROM policy_version
-- WHERE 는 집계 입력만 거른다 — 0행이어도 집계 결과 1행이 나와 INSERT 가 항상 실행된다
-- (2회째 호출에서 uq_policy_version_no 충돌). 집계 "결과" 행을 거르는 것은 HAVING 이다.
HAVING NOT EXISTS (
       SELECT 1 FROM policy_version
        WHERE activated_at IS NOT NULL AND deactivated_at IS NULL);
SQL
}

# 시드 대상 티커 목록(핫키 + cold N종)을 SQL VALUES 로 만든다.
# bash 3.2 라 연관배열·mapfile 없이 문자열 누적으로 조립한다.
build_values() {
	local wanted="$1" n=0 out="" t
	out="('$HOT')"
	n=1
	for t in $ALL_TICKERS; do
		[ "$t" = "$HOT" ] && continue
		[ "$n" -gt "$wanted" ] && break
		out="$out, ('$t')"
		n=$((n + 1))
	done
	printf '%s' "$out"
}

# ── 2·3. analysis_item + publication ─────────────────────────────────────────
# 한 psql 호출 = 한 트랜잭션이라 두 INSERT 의 now() 가 같은 값이다 — publication 의
# explanation_as_of 는 analysis_item 복사본이어야 하므로(V202608041130 규율) 중요하다.
seed_items() {
	local values
	values="$(build_values "$COLD_COUNT")"
	psql_run <<SQL
BEGIN;

INSERT INTO analysis_item (
    explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
    trade_date, explanation_as_of, content_as_of,
    analysis_type, explanation_type, summary, headline, confidence_level,
    evidences, status)
SELECT 'loadtest-' || t.ticker || '-' || CURRENT_DATE,
       'loadtest-instrument-' || t.ticker,
       t.ticker,
       'LOADTEST ' || t.ticker,
       CURRENT_DATE, now(), now(),
       'PRICE_MOVEMENT', 'MIXED', $SUMMARY_SQL, '부하 실험 합성 설명', 'MEDIUM',
       '[]'::jsonb, 'AUTO_PUBLISHED'
  FROM (VALUES $values) AS t(ticker)
ON CONFLICT (explanation_result_id) DO NOTHING;

-- 게시 grain = (etf_ticker, trade_date, explanation_as_of) WHERE status='PUBLISHED'
-- (부분 유니크 uq_publication_published_grain). 부분 인덱스는 index_predicate 를
-- 함께 적어야 arbiter 로 추론된다.
INSERT INTO publication (
    analysis_item_id, etf_ticker, trade_date, explanation_as_of, content_as_of,
    status, published_summary, published_at)
SELECT a.explanation_result_id, a.etf_ticker, a.trade_date, a.explanation_as_of,
       a.content_as_of, 'PUBLISHED', a.summary, now()
  FROM analysis_item a
 WHERE a.explanation_result_id LIKE 'loadtest-%'
   AND a.status = 'AUTO_PUBLISHED'
ON CONFLICT (etf_ticker, trade_date, explanation_as_of)
    WHERE status = 'PUBLISHED'
DO NOTHING;

COMMIT;
SQL
}

# ── 2·3'. 합성 티커 대량 시드 (--synthetic N) ────────────────────────────────
# WHY: 워킹셋 스윕(suite W)은 키 수 N 을 수십~수천까지 밀어 올려 L1 이 무너지는
# 무릎을 찾는 실험이다. 33종 유니버스로는 그 축을 만들 수 없다. bash 로 VALUES 를
# 5000개 조립하면 문자열·psql 인자 한계에 걸리므로 티커 소스를 SQL generate_series
# 로 옮긴다 — 컬럼 세트·'loadtest-' PK 규칙·ON CONFLICT 멱등·한 트랜잭션(now() 동일값)
# 은 seed_items 와 같은 규율을 그대로 따른다.
#
# 티커 형식 계약: 'LT' + 5자리 zero-pad, 1부터 (LT00001 … LT05000). 대문자 고정.
# run-matrix 가 만드는 PUBLICATION_KNOWN_TICKERS allowlist·k6 시나리오의 키 생성과
# 정확히 같은 형식이어야 한다 — 어긋나면 서빙이 404 를 낸다.
seed_items_synthetic() {
	local n="$1"
	psql_run <<SQL
BEGIN;

INSERT INTO analysis_item (
    explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
    trade_date, explanation_as_of, content_as_of,
    analysis_type, explanation_type, summary, headline, confidence_level,
    evidences, status)
SELECT 'loadtest-' || t.ticker || '-' || CURRENT_DATE,
       'loadtest-instrument-' || t.ticker,
       t.ticker,
       'LOADTEST ' || t.ticker,
       CURRENT_DATE, now(), now(),
       'PRICE_MOVEMENT', 'MIXED', $SUMMARY_SQL, '부하 실험 합성 설명', 'MEDIUM',
       '[]'::jsonb, 'AUTO_PUBLISHED'
  FROM (
        -- 핫키는 합성과 별도 1행 — 스파이크·핫비율 실험이 같은 시드를 재사용한다.
        SELECT '$HOT' AS ticker
        UNION ALL
        SELECT 'LT' || lpad(g::text, 5, '0') FROM generate_series(1, $n) g
       ) AS t
ON CONFLICT (explanation_result_id) DO NOTHING;

-- 게시 grain = (etf_ticker, trade_date, explanation_as_of) WHERE status='PUBLISHED'
-- (부분 유니크 uq_publication_published_grain). seed_items 와 같은 arbiter.
INSERT INTO publication (
    analysis_item_id, etf_ticker, trade_date, explanation_as_of, content_as_of,
    status, published_summary, published_at)
SELECT a.explanation_result_id, a.etf_ticker, a.trade_date, a.explanation_as_of,
       a.content_as_of, 'PUBLISHED', a.summary, now()
  FROM analysis_item a
 WHERE a.explanation_result_id LIKE 'loadtest-%'
   AND a.status = 'AUTO_PUBLISHED'
ON CONFLICT (etf_ticker, trade_date, explanation_as_of)
    WHERE status = 'PUBLISHED'
DO NOTHING;

COMMIT;
SQL
}

# ── 4. 제공 범위 초기화 ──────────────────────────────────────────────────────
# 실험이 넣은 차단 행만 지운다 — MARKET(XKRX) 전역 스위치와 시드 종목의 INSTRUMENT 행.
reset_scope() {
	local values
	values="$(build_values 999)"  # 유니버스 전체 — 어느 종목이 차단됐든 지운다
	psql_run <<SQL
DELETE FROM serving_scope
 WHERE (scope_type = 'MARKET' AND scope_key = 'XKRX')
    OR (scope_type = 'INSTRUMENT'
        AND scope_key IN (SELECT ticker FROM (VALUES $values) AS t(ticker)));
SQL
}

# ── 5. 새 스냅샷 ─────────────────────────────────────────────────────────────
# 같은 (ticker, trade_date)에 더 새로운 explanation_as_of 로 1쌍을 추가한다.
# 다스냅샷 공존이 스키마 계약이라(ADR-0045) 구본을 내리지 않는다 — 서빙 정렬이
# "유효 최신 승리"로 새 행을 고르는지 보는 것이 실험의 관측 대상이다.
new_snapshot() {
	local ticker="$1"
	psql_run <<SQL
BEGIN;

INSERT INTO analysis_item (
    explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
    trade_date, explanation_as_of, content_as_of,
    analysis_type, explanation_type, summary, headline, confidence_level,
    evidences, status)
SELECT 'loadtest-$ticker-' || CURRENT_DATE || '-snap-' || extract(epoch FROM now())::bigint,
       'loadtest-instrument-$ticker', '$ticker', 'LOADTEST $ticker',
       CURRENT_DATE, now(), now(),
       'PRICE_MOVEMENT', 'MIXED', $SUMMARY_SQL, '부하 실험 새 스냅샷', 'MEDIUM',
       '[]'::jsonb, 'AUTO_PUBLISHED'
ON CONFLICT (explanation_result_id) DO NOTHING;

INSERT INTO publication (
    analysis_item_id, etf_ticker, trade_date, explanation_as_of, content_as_of,
    status, published_summary, published_at)
SELECT a.explanation_result_id, a.etf_ticker, a.trade_date, a.explanation_as_of,
       a.content_as_of, 'PUBLISHED', a.summary, now()
  FROM analysis_item a
 WHERE a.etf_ticker = '$ticker'
   AND a.status = 'AUTO_PUBLISHED'
   AND a.explanation_result_id LIKE 'loadtest-$ticker-%-snap-%'
   AND NOT EXISTS (SELECT 1 FROM publication p WHERE p.analysis_item_id = a.explanation_result_id)
ON CONFLICT (etf_ticker, trade_date, explanation_as_of)
    WHERE status = 'PUBLISHED'
DO NOTHING;

COMMIT;
SQL
}

# ── 6. 종목 차단 ─────────────────────────────────────────────────────────────
# 옵트아웃 모델이라 행이 곧 차단이다(enabled=false). 재실행 시 되살아나게 upsert.
block_ticker() {
	local ticker="$1"
	psql_run <<SQL
INSERT INTO serving_scope (scope_type, scope_key, enabled, updated_at)
VALUES ('INSTRUMENT', '$ticker', FALSE, now())
ON CONFLICT (scope_type, scope_key)
DO UPDATE SET enabled = FALSE, updated_at = now();
SQL
}

# ── 7. 200 확인 ──────────────────────────────────────────────────────────────
# 204 만 나오면 측정이 무의미하다(캐시가 가릴 read path 자체가 없다).
# api-1 직결로 본다 — nginx 를 끼면 어느 인스턴스가 답했는지 흐려진다.
check_serving() {
	local ticker="$1" code
	code="$(curl -s -o /dev/null -w '%{http_code}' \
		-H 'X-Customer-Hash: seed-check' -H 'X-Channel: INTERNAL' \
		"http://localhost:18101/api/v1/explanations/$ticker" || echo 000)"
	if [ "$code" != "200" ]; then
		err "시드 확인 실패: $ticker -> HTTP $code (200 이어야 한다)"
		case "$code" in
			204) err "  204 = 게시분 없음 또는 제공 범위 차단. --reset-scope 후 재시드하라." ;;
			404) err "  404 = PUBLICATION_KNOWN_TICKERS allowlist 밖 종목이다."
			     err "        합성 티커라면 allowlist 미주입이다 — PUB_KNOWN_TICKERS 를 export 하고 스택을 재생성하라." ;;
			000) err "  연결 실패 = api-1(:18101)이 아직 안 떴다." ;;
		esac
		return 1
	fi
	info "시드 확인 OK: $ticker -> 200"
}

# ── 실행 ─────────────────────────────────────────────────────────────────────
case "$SYNTHETIC" in
	''|*[!0-9]*) err "--synthetic 은 0 이상의 정수여야 한다: $SYNTHETIC"; exit 2 ;;
esac

seed_policy
if [ "$SYNTHETIC" -gt 0 ]; then
	# INSERT 태그("INSERT 0 <행수>")를 그대로 남긴다 — N=5000 시드가 몇 행 들어갔고
	# 몇 초 걸렸는지 눈으로 확인해야 스윕 앞단에서 시간을 잡아먹는지 판단할 수 있다.
	SEED_T0="$(date +%s)"
	SEED_OUT="$(seed_items_synthetic "$SYNTHETIC")"
	info "합성 시드 결과($(( $(date +%s) - SEED_T0 ))s): $(printf '%s' "$SEED_OUT" | tr '\n' ' ')"
	info "시드 완료: hot=$HOT, synthetic=$SYNTHETIC 종 (LT00001..LT$(printf '%05d' "$SYNTHETIC"))"
else
	seed_items
	info "시드 완료: hot=$HOT, cold=$COLD_COUNT 종"
fi

ACTION_TAKEN=0
if [ "$RESET_SCOPE" -eq 1 ]; then
	reset_scope
	info "serving_scope 실험 행 삭제 완료"
fi
if [ -n "$NEW_SNAPSHOT" ]; then
	new_snapshot "$NEW_SNAPSHOT"
	info "새 스냅샷 추가: $NEW_SNAPSHOT"
	ACTION_TAKEN=1
fi
if [ -n "$BLOCK_TICKER" ]; then
	block_ticker "$BLOCK_TICKER"
	info "제공 범위 차단: $BLOCK_TICKER"
	ACTION_TAKEN=1
fi

if [ "$ACTION_TAKEN" -eq 0 ]; then
	if [ "$SYNTHETIC" -gt 0 ]; then
		# 합성 표본 1종을 반드시 함께 본다 — 핫키는 기본 allowlist 에 있어서
		# 200 이 나오지만, 합성 티커가 주입되지 않았으면(PUB_KNOWN_TICKERS 미설정)
		# 404 라 워킹셋 스윕 전체가 무의미해진다.
		check_serving "$HOT"
		check_serving "LT00001"
	else
		# cold 표본 1종도 함께 본다 — 핫키만 확인하면 cold 시드 누락을 놓친다.
		COLD_SAMPLE=""
		for t in $ALL_TICKERS; do
			if [ "$t" != "$HOT" ]; then COLD_SAMPLE="$t"; break; fi
		done
		check_serving "$HOT"
		[ -n "$COLD_SAMPLE" ] && check_serving "$COLD_SAMPLE"
	fi
fi
