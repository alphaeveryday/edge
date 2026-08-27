/* 레인(`pipeline_type`) 어휘 — 원장 코드를 운영자 말로 (ALPHA-738).
 *
 * 🔴 **이 표는 반드시 낡는다.** 이식된 개요 화면이 `{'etf-daily', 'news'}` 둘만 들고 있었는데
 * 정본(`data_pipeline/ops/catalog.py`)에는 그 사이 `investor-intraday` 3작업이 **신설**돼
 * 있었다(ALPHA-767·768·769 — 레인 이동이 아니라 신설이다). 개요 응답은 레인을 안 거른다 —
 * `JdbcPipelineStatusRepository.OVERVIEW_SQL` 이 `DISTINCT ON (pipeline_type)` 으로 **원장에
 * 있는 전 레인**을 낸다. 그래서 표에 없는 레인은 첫 화면에 원장 코드가 그대로 찍힌다.
 *
 * `disclosure` 는 ALPHA-987에서 18:10 일배치로 복원됐다. 레인 이동 때 이 표를 함께 바꾸지
 * 않으면 첫 화면에 원장 코드가 그대로 찍히므로 아래 테스트가 정본과 전건 대조한다.
 *
 * ⚠️ **JSX 를 쓰지 않는다.** `OverviewPage.tsx` 안에 있는 동안은 `node --test` 가 파일을 못
 * 집어, 레인이 늘어도 아무 테스트가 안 깨졌다. 이 트랙이 같은 이유로 판정 모듈을 여러 번
 * 내렸다. 여기 있으면 옆 테스트가 `datasetCatalog`(정본에서 파생된 것)와 대조할 수 있다.
 */

/**
 * 레인 코드 → 표시 이름. **정본은 `ops/catalog.py` 의 `pipeline_type`** 이고, 이 표는 그
 * 어휘에 한국어를 입힐 뿐이다. 새 레인이 생기면 여기 한 줄 — 안 더하면 테스트가 깨진다.
 */
export const LANE_LABEL: Record<string, string> = {
  'etf-daily': '시장(EOD)',
  news: '뉴스',
  disclosure: '공시',
  'investor-intraday': '수급(장중)',
};

/**
 * 모르는 레인이라고 빈 칸으로 두지 않는다 — **원장 코드를 그대로 보여 준다.**
 * 지어낸 이름보다 낯선 코드가 낫다(운영자가 원장에서 그 값을 찾을 수 있다).
 */
export const laneLabel = (pipelineType: string): string =>
  LANE_LABEL[pipelineType] ?? pipelineType;
