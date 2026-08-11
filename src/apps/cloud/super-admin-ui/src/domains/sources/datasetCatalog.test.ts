/* 데이터셋 카탈로그 — 행 축의 계약 (ALPHA-738).
 *
 * 지키는 의도:
 *   · 실행 이력의 행은 **데이터셋**이다. 유형·도메인은 필터·배지이지 상태를 갖는 부모가
 *     아니다 — 두 축이 직교하므로 어느 한쪽으로 접어도 트리가 되지 않는다는 것을 고정한다.
 *   · 실시간 데이터셋은 ops 격자 원장 밖이고 세션 상세로 보낼 키를 갖는다. 이게 깨지면
 *     격자가 실시간 행을 "계획 없음"(계획 행이 없다)으로 그려 없는 결손을 지어낸다.
 *
 * 실행: node --test src/domains/sources/datasetCatalog.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ALL_DATASETS,
  DATASET_DOMAINS,
  DATASET_OF_TASK,
  kindOf,
} from './datasetCatalog.ts';

test('유형은 cadence 에서 유도한다 — 두 벌로 두지 않는다', () => {
  for (const d of ALL_DATASETS) {
    assert.equal(kindOf(d), d.cadence.kind === 'intradayWindows' ? '실시간' : '일배치', d.id);
  }
});

test('유형과 도메인은 직교한다 — 어느 한 축도 트리의 부모가 되지 못한다', () => {
  const realtime = ALL_DATASETS.filter((d) => kindOf(d) === '실시간');
  const domains = new Set(realtime.map((d) => d.domain));
  /* 실시간 안에 시장·뉴스가 둘 다 있다 → "장중"을 도메인 부모 행으로 세울 수 없다 */
  assert.ok(domains.size > 1, `실시간 도메인이 갈리지 않는다: ${[...domains]}`);
  for (const d of ALL_DATASETS) assert.ok(DATASET_DOMAINS.includes(d.domain), d.id);
});

test('실시간 데이터셋은 격자 원장 밖이고 세션 상세로 갈 키를 갖는다', () => {
  const realtime = ALL_DATASETS.filter((d) => kindOf(d) === '실시간');
  assert.deepEqual(
    realtime.map((d) => d.id).sort(),
    ['news_minute', 'price_minute'],
    '1분 원장의 두 dataset(states.py 어휘)이 다 있어야 한다',
  );
  for (const d of realtime) {
    assert.equal(d.inOpsGrid, false, `${d.id} 는 ops_expected_task 소관이 아니다`);
    /* 지목 키가 없으면 드릴다운이 데이터셋을 못 고르고 첫 탭으로 떨어진다 */
    assert.equal(d.sessionDataset, d.id);
    assert.ok(d.elsewhere?.href.startsWith('/minute'), `${d.id} 는 세션 상세로 보내야 한다`);
  }
});

test('실시간 데이터셋이 작업→데이터셋 역인덱스를 오염시키지 않는다', () => {
  /* 1분 수집은 ops 작업(task_key)이 아니다 — 여기 끼면 배치 격자 셀이 실시간 행으로 샌다 */
  for (const id of Object.values(DATASET_OF_TASK)) {
    assert.notEqual(id, 'price_minute');
    assert.notEqual(id, 'news_minute');
  }
});

test('배치 데이터셋은 반드시 격자에 있고 작업이 매여 있다', () => {
  for (const d of ALL_DATASETS.filter((x) => kindOf(x) === '일배치')) {
    assert.equal(d.inOpsGrid, true, d.id);
    assert.ok(d.taskKeys.length > 0, `${d.id} 에 작업이 없으면 행이 영원히 빈다`);
  }
});
