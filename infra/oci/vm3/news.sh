#!/usr/bin/env bash
# 뉴스 레인 — 매일 00:10, 평일 08:10. apps/engine/airflow/dags/edge_news.py 와 같은 스텝이다.
#
# 순서와 관문(DAG 와 같다):
# - 수집이 실패해도 정제는 돈다. 정제는 exit 0 만 통과한다.
# - 문서 적재를 태깅보다 먼저 돈다. 태깅 결과를 쓰지 않는 짧은 스텝이라, 긴 태깅 뒤에 두면 태깅이 늦을 때
#   기사 적재까지 늦는다(DAG 도 한 줄로 돌 때 이 순서였다).
# - 태깅과 문서 적재가 모두 exit 0 이어야 assertion 적재, 그 뒤 이벤트 조립을 돈다.
. "$(dirname "$0")/../lib/batch.sh"
# 이 VM 의 스텝은 4g 를 기본으로 준다. 로컬 태거(tag-news)는 최대 2.0~2.2GB 를 썼고 assemble-events 는 AWS 에서 4096MB 였다.
EDGE_STEP_MEMORY_DEFAULT=4g

begin_lane news "${1:-}"
TAG_LIMIT="$(need EDGE_TAG_NEWS_LIMIT)" || exit 1
WINDOW_DAYS="$(need EDGE_ASSEMBLE_WINDOW_DAYS)" || exit 1

run_step collect ingest-raw --source bigkinds --run-id "$RID"
run_step normalize normalize-news --run-id "$RID" --input-run-id "$RID"
if ok normalize; then
  run_step load_documents load-documents --run-id "$RID" --input-run-id "$RID"
  run_step tag tag-news --run-id "$RID" --input-run-id "$RID" --limit "$TAG_LIMIT"
fi
ok tag load_documents && run_step load_assertions load-assertions --run-id "$RID" --input-run-id "$RID"
ok load_assertions && run_step assemble assemble-events --run-id "$RID" --window-days "$WINDOW_DAYS"

end_lane
