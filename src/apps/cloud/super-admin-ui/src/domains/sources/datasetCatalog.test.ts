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
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  ALL_DATASETS,
  DATASET_DOMAINS,
  DATASET_OF_TASK,
  kindOf,
} from './datasetCatalog.ts';

/** 원장 실측 — 작업 어휘의 정본. 카탈로그는 이 집합 위에서만 접을 수 있다. */
const LEDGER_TASKS: { task_key: string; dataset?: string }[] = JSON.parse(
  readFileSync(new URL('../../rules/facts-snapshot.json', import.meta.url), 'utf8'),
).tasks;

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

/* ── 역인덱스는 원장 어휘 위에서만 성립한다 ──
 *
 * `개수 > 0` 만 재면 오타(`PRICE_COLLECTION_KI`)도, 원장에 새로 생긴 작업이 어느 행에도
 * 안 매인 것도 통과한다. 그런데 `rollup()` 은 `DATASET_OF_TASK[key]` 가 없으면 그 셀을
 * **조용히 버린다** — 위 두 결함은 화면에서 "그 작업이 없던 일"로 보인다.
 * 그래서 어휘 자체를 원장 실측(facts-snapshot)과 양방향으로 맞물린다.
 */

test('카탈로그의 작업 키는 전부 원장에 실재한다 — 오타는 행을 영원히 비운다', () => {
  const ledger = new Set(LEDGER_TASKS.map((t) => t.task_key));
  const unknown = Object.keys(DATASET_OF_TASK).filter((k) => !ledger.has(k));
  assert.deepEqual(unknown, [], '원장에 없는 작업 키가 카탈로그에 있다');
});

test('원장의 모든 작업이 정확히 한 데이터셋에 귀속된다 — 안 매인 작업은 격자에서 사라진다', () => {
  /* 접기 방향은 카탈로그의 몫이지만(산출 테이블마다 행을 쪼개지 않는다), **누락**은 아니다.
   * 매인 데가 없으면 rollup 이 그 셀을 버려 실패조차 안 보인다. */
  const orphan = LEDGER_TASKS.filter((t) => t.dataset && !DATASET_OF_TASK[t.task_key]).map(
    (t) => t.task_key,
  );
  assert.deepEqual(orphan, [], '어느 데이터셋에도 안 매인 원장 작업이 있다');

  /* 같은 작업을 두 데이터셋이 주장하면 Object.fromEntries 가 뒤엣것으로 조용히 덮는다 —
   * 그러면 앞 데이터셋의 행에서 그 작업이 통째로 빠진다. */
  const claimed = ALL_DATASETS.flatMap((d) => d.taskKeys);
  assert.equal(
    claimed.length,
    new Set(claimed).size,
    `두 데이터셋이 같은 작업을 주장한다: ${claimed.filter((k, i) => claimed.indexOf(k) !== i)}`,
  );
  /* 역인덱스가 주장 전량을 담았는지 — 크기가 갈리면 위 중복 단언과 함께 원인이 좁혀진다 */
  assert.equal(Object.keys(DATASET_OF_TASK).length, claimed.length);
});
