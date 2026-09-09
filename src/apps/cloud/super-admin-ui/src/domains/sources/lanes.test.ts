/* 실행: node --test src/domains/sources/lanes.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { DATASET_GROUPS } from './datasetCatalog.ts';
import { LANE_LABEL, laneLabel } from './lanes.ts';

/** 데이터셋 카탈로그가 아는 레인 전량 — 그쪽은 `ops/catalog.py` 에서 파생됐다(조각 2). */
const lanesInCatalog = [
  ...new Set(
    DATASET_GROUPS.flatMap((g) => g.datasets)
      .map((d) => d.lane)
      .filter((l): l is string => !!l),
  ),
].sort();

test('카탈로그가 아는 레인은 전부 표시 이름을 얻는다', () => {
  /* 🔴 이게 이 파일의 존재 이유다. 개요 응답은 레인을 안 거르므로(`OVERVIEW_SQL` 의
   * `DISTINCT ON (pipeline_type)`), 파이프라인에 레인이 늘면 **다음 배포에 바로** 첫 화면에
   * 뜬다. 표를 손으로 유지하면 그때 원장 코드가 운영자에게 노출된다 — 실제로 이식분이
   * `investor-intraday` 를 빠뜨린 채 들어왔다. 정본 파생인 카탈로그를 오라클로 삼는다. */
  for (const lane of lanesInCatalog) {
    assert.ok(lane in LANE_LABEL, `레인 '${lane}' 의 표시 이름이 없다 — lanes.ts 에 한 줄 더해라`);
  }
});

test('현재 카탈로그 밖에는 개요 SQL이 계속 내는 종료 공시 레인만 남는다', () => {
  /* OVERVIEW_SQL은 전 이력에서 레인별 최신 런을 골라 은퇴한 레인도 계속 반환한다. 현재
   * 카탈로그로만 표를 자르면 공시 카드에 원장 코드가 노출되므로, 그 한 건은 종료를 직접
   * 밝힌 라벨로 보존한다. 다른 유령 라벨이 늘면 이 단언이 깨진다. */
  const retired = Object.keys(LANE_LABEL).filter((lane) => !lanesInCatalog.includes(lane));
  assert.deepEqual(retired, ['disclosure']);
  assert.match(LANE_LABEL.disclosure, /일배치·종료/);
});

test('모르는 레인은 이름을 지어내지 않고 원장 코드를 그대로 낸다', () => {
  /* 빈 문자열·'알 수 없음' 으로 접으면 운영자가 원장에서 그 런을 못 찾는다 */
  assert.equal(laneLabel('아직-없는-레인'), '아직-없는-레인');
  assert.equal(laneLabel('etf-daily'), '시장(EOD)');
});

test('표시 이름이 서로 겹치지 않는다 — 두 레인이 한 이름이면 카드가 구분 안 된다', () => {
  const labels = Object.values(LANE_LABEL);
  assert.equal(new Set(labels).size, labels.length, `중복된 레인 이름: ${labels.join(' · ')}`);
});
