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

/*
 * 정본은 **파이프라인 소스 자체**다 — facts-snapshot 이 아니다.
 *
 * 스냅샷은 2026-08-03 로 얼린 회귀 픽스처라 그날 이후의 레인 이동·신설을 모른다. 실제로
 * 그걸 정본으로 삼았더니 공시 4작업이 ops 에서 사라진 것도, 장중 수급 3작업이 생긴 것도
 * 통과했다 — "낡은 것끼리 맞음"이 초록으로 보였다. 두 언어를 잇는 자동 가드가 없어 이
 * 파일이 그 다리다(그래서 정규식으로 읽는다 — 생성물을 하나 더 만들지 않는다).
 *
 * 이 대조가 정당하게 깨지는 때: 파이프라인이 작업·1분 dataset 을 더하거나 뺐을 때. 그때
 * 화면 카탈로그도 같이 움직여야 하고, 안 움직이면 `dailyRollup` 이 그 작업 셀을 조용히
 * 버리거나(누락) 영원히 안 오는 행을 그린다(유령).
 */
const pipelineSrc = (rel: string) =>
  readFileSync(
    new URL(`../../../../data-pipeline/src/data_pipeline/${rel}`, import.meta.url),
    'utf8',
  );

/** ops 격자 원장이 계획하는 작업 전량 */
const OPS_TASK_KEYS: string[] = [...pipelineSrc('ops/catalog.py').matchAll(/task_key="([^"]+)"/g)].map(
  (m) => m[1],
);

/** 1분 원장이 아는 dataset 어휘 */
const MINUTE_DATASETS: string[] = [
  ...pipelineSrc('minute/states.py').matchAll(/^DATASET_[A-Z_]+ = "([a-z_]+)"$/gm),
].map((m) => m[1]);

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
  /* 어휘를 여기 하드코딩하면 원장이 늘어도 테스트가 초록이라, 새 레인이 실행 이력에서
   * 통째로 사라진 채 "고정됨"으로 보인다 — 실제로 세 개(iNAV·공시·업종지수)를 그렇게 놓쳤다. */
  assert.deepEqual(
    realtime.map((d) => d.id).sort(),
    [...MINUTE_DATASETS].sort(),
    '1분 원장 어휘(states.py) 전량이 행으로 서야 한다',
  );
  assert.ok(MINUTE_DATASETS.length >= 2, '어휘 추출이 빈 배열이면 위 단언은 아무것도 안 잰다');
  for (const d of realtime) {
    assert.equal(d.inOpsGrid, false, `${d.id} 는 ops_expected_task 소관이 아니다`);
    /* 지목 키가 없으면 드릴다운이 데이터셋을 못 고르고 첫 탭으로 떨어진다 */
    assert.equal(d.sessionDataset, d.id);
    assert.ok(d.elsewhere?.href.startsWith('/minute'), `${d.id} 는 세션 상세로 보내야 한다`);
  }
});

test('실시간 데이터셋이 작업→데이터셋 역인덱스를 오염시키지 않는다', () => {
  /* 1분 수집은 ops 작업(task_key)이 아니다 — 여기 끼면 배치 격자 셀이 실시간 행으로 샌다 */
  const minute = new Set(MINUTE_DATASETS);
  for (const id of Object.values(DATASET_OF_TASK)) {
    assert.ok(!minute.has(id), `${id} 는 1분 원장 소관인데 ops 작업이 매여 있다`);
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
 * 그래서 어휘 자체를 파이프라인 소스(ops/catalog.py)와 양방향으로 맞물린다.
 */

test('카탈로그의 작업 키는 전부 ops 원장에 실재한다 — 유령은 영원히 빈 행을 그린다', () => {
  assert.ok(OPS_TASK_KEYS.length > 10, '정본 추출이 실패하면 아래 단언이 빈 집합끼리 비교된다');
  const ops = new Set(OPS_TASK_KEYS);
  const ghost = Object.keys(DATASET_OF_TASK).filter((k) => !ops.has(k));
  assert.deepEqual(ghost, [], 'ops/catalog.py 에 없는 작업 키가 카탈로그에 있다');
});

test('ops 원장의 모든 작업이 정확히 한 데이터셋에 귀속된다 — 안 매인 작업은 격자에서 사라진다', () => {
  /* 접기 방향은 카탈로그의 몫이지만(산출 테이블마다 행을 쪼개지 않는다), **누락**은 아니다.
   * 매인 데가 없으면 rollup 이 그 셀을 버려 실패조차 안 보인다. */
  const orphan = OPS_TASK_KEYS.filter((k) => !DATASET_OF_TASK[k]);
  assert.deepEqual(orphan, [], '어느 데이터셋에도 안 매인 ops 작업이 있다');

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

test('데이터셋의 레인 선언이 ops 정본과 같다 — 기동 실패 귀속이 여기에 매여 있다', () => {
  /* `rollup` 은 작업이 하나도 없는 기동 실패 런을 이 `lane` 으로 데이터셋에 귀속시킨다.
   * 선언이 낡으면 **엉뚱한 데이터셋이 장애로** 서거나, 진짜 실패가 어디에도 안 남는다.
   * 정본은 ops 카탈로그의 `pipeline_type` 이고, 그 데이터셋의 작업들이 실제로 그 레인에
   * 속하는지로 잰다(레인을 따로 적은 두 벌이 되지 않게). */
  const laneOfTask = new Map<string, string>();
  for (const m of pipelineSrc('ops/catalog.py').matchAll(/CatalogEntry\(([\s\S]*?)\n    \)/g)) {
    const key = /task_key="([^"]+)"/.exec(m[1])?.[1];
    if (!key) continue;
    laneOfTask.set(key, /pipeline_type="([^"]+)"/.exec(m[1])?.[1] ?? 'etf-daily');
  }
  assert.ok(laneOfTask.size > 20, '정본 추출 실패');

  for (const d of ALL_DATASETS) {
    if (!d.inOpsGrid) {
      assert.equal(d.lane, undefined, `${d.id}: ops 격자 밖인데 레인이 붙어 있다`);
      continue;
    }
    assert.ok(d.lane, `${d.id}: 배치 데이터셋에 레인이 없다`);
    for (const k of d.taskKeys) {
      assert.equal(laneOfTask.get(k), d.lane, `${d.id}.${k}: 작업의 레인이 선언과 다르다`);
    }
  }
});
