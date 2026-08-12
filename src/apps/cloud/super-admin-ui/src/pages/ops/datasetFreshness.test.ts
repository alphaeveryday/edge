/* 신선도 판정 — **근거가 없으면 초록이 아니다** (ALPHA-738 D).
 *
 * 이 파일이 지키는 것은 산식이 아니라 **부재의 갈래**다. 초록(FRESH)은 "봤고 괜찮다"라,
 * 비교하지 않은 것·비교할 수 없는 것이 그 칸에 서면 계측 공백이 정상으로 읽힌다.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import type { DatasetFact, TaskFact } from '../../rules/types.ts';
import {
  datasetRunFlows,
  freshness,
  ROLLUP_BADGE,
  taskRollup,
} from './datasetFreshness.ts';

/** ⚠️ 자리마다 다른 값을 둔다 — 같은 값이면 두 필드를 맞바꾸는 실수가 안 잡힌다. */
const ds = (o: Partial<DatasetFact>): DatasetFact => ({
  id: 'etf_holdings',
  contract: true,
  expected_as_of: '2026-08-03',
  actual_as_of: '2026-08-02',
  collected_at: '2026-08-03T15:41:58+09:00',
  unverifiable: null,
  ...o,
});

test('계획에서 SKIPPED 된 작업은 필요한 작업의 귀결을 막지 않는다', () => {
  const tasks = [
    { plan_status: 'DUE', task_outcome: 'FULFILLED' },
    { plan_status: 'SKIPPED', task_outcome: null },
  ] as TaskFact[];
  assert.equal(taskRollup(tasks), 'fulfilled');
  assert.equal(
    taskRollup([{ plan_status: 'DUE', task_outcome: 'FAILED' } as TaskFact]),
    'failed',
  );
  assert.equal(
    taskRollup([{ plan_status: 'SKIPPED', task_outcome: null } as TaskFact]),
    'skipped',
    '전부 계획 제외인 날은 초록 귀결도 미귀결도 아니다',
  );
});

/* 🔴 `taskRollup` 의 다섯 갈래 중 FULFILLED·FAILED·SKIPPED 만 재고 있어, MISSED 를 실패에서
 * 빼거나 BLOCKED 갈래를 통째로 지워도 전건 초록이었다(변이 실증, 2026-08-12).
 * `DatasetPage` 는 다섯을 각각 다른 배지로 그리므로, 접히면 화면이 사실을 잃는다. */
test('🔴 귀결 다섯 갈래가 서로 다른 칸이다 — 접으면 실패가 "미귀결"로 흐려진다', () => {
  const t = (o: Partial<TaskFact>) => ({ plan_status: 'DUE', ...o }) as TaskFact;
  assert.equal(taskRollup([t({ task_outcome: 'MISSED' })]), 'failed');
  assert.equal(taskRollup([t({ task_outcome: 'FAILED' })]), 'failed');
  assert.equal(taskRollup([t({ task_outcome: 'BLOCKED' })]), 'blocked');
  assert.equal(taskRollup([t({ task_outcome: 'PENDING' })]), 'unresolved');
  assert.equal(taskRollup([t({ task_outcome: null })]), 'unresolved');
  assert.equal(taskRollup([t({ task_outcome: 'FULFILLED' })]), 'fulfilled');
  /* 모르는 어휘는 귀결로 세지 않는다 — `every(FULFILLED)` 가 그 자리를 지킨다 */
  assert.equal(taskRollup([t({ task_outcome: 'WEIRD' })]), 'unresolved');

  /* 섞이면 **더 나쁜 쪽**이 이긴다. 순서를 바꾸면 실패가 선행 미충족 뒤로 숨는다 */
  assert.equal(
    taskRollup([t({ task_outcome: 'FULFILLED' }), t({ task_outcome: 'BLOCKED' }), t({ task_outcome: 'FAILED' })]),
    'failed',
  );
  assert.equal(
    taskRollup([t({ task_outcome: 'FULFILLED' }), t({ task_outcome: 'PENDING' }), t({ task_outcome: 'BLOCKED' })]),
    'blocked',
  );
  /* SKIPPED 는 분모에서 빠진다 — 남은 것이 전부 귀결이면 전건 귀결이다 */
  assert.equal(
    taskRollup([t({ plan_status: 'SKIPPED', task_outcome: null }), t({ task_outcome: 'FULFILLED' })]),
    'fulfilled',
  );
});

/* 🔴 위 테스트만 있을 때 **화면은 정반대를 그리고 있었고 전건 초록이었다.** 함수 안 순위만 재고
 * 소비 자리의 톤을 한 줄도 안 봤기 때문이다 — `unresolved`=blocked(빨강) · `blocked`=warn(주황)
 * 이라, 미귀결 흐름에 BLOCKED 작업이 **하나 더 생기면 배지가 덜 심각해졌다**.
 * 순위를 못박는 단언은 **톤까지 내려가야** 의미가 있다. 그래서 톤 사상을 JSX 밖으로 내렸다. */
test('🔴 롤업 순위와 배지 톤이 같은 방향이다 — 나빠졌는데 배지가 내려가면 안 된다', () => {
  const t = (o: Partial<TaskFact>) => ({ plan_status: 'DUE', ...o }) as TaskFact;
  const RANK: Record<string, number> = { active: 0, neutral: 1, warn: 2, blocked: 3 };

  /* 표를 **전수로** 못박는다. 갈래 셋만 하드코딩했더니 `skipped`→active·`fulfilled`→neutral
   * 변이가 살아남았다(실증) — 단조성 루프는 `[0,0,1,2,3]` 도 단조라 못 잡고, 아래 누적 순회는
   * `skipped`(due 가 0이라야 나오는 배타 갈래)를 한 번도 안 밟는다.
   * ⚠️ `skipped` 가 `active` 가 되면 "할 일이 아니었다"와 "다 잘 됐다"가 같은 초록으로 선다. */
  assert.deepEqual(
    Object.fromEntries(Object.entries(ROLLUP_BADGE).map(([k, v]) => [k, v.tone])),
    {
      fulfilled: 'active',
      skipped: 'neutral',
      unresolved: 'neutral',
      blocked: 'warn',
      failed: 'blocked',
    },
    '롤업 배지 톤이 바뀌었다 — 순위와 같은 방향인지 다시 판단하고 이 표를 고쳐라',
  );

  /* 표만 고치고 함수를 안 고치면 위 단언은 통과하면서 화면이 어긋난다. 나쁜 것을 하나씩 더해
   * `taskRollup` 의 분기 순서와 톤이 **함께** 역행하지 않는지 본다 */
  const worse = [
    { outcome: 'FULFILLED', expect: 'fulfilled' },
    { outcome: 'PENDING', expect: 'unresolved' },
    { outcome: 'BLOCKED', expect: 'blocked' },
    { outcome: 'FAILED', expect: 'failed' },
  ] as const;
  const acc: TaskFact[] = [];
  let last = -1;
  for (const w of worse) {
    acc.push(t({ task_outcome: w.outcome }));
    const r = taskRollup(acc);
    assert.equal(r, w.expect, `${w.outcome} 를 더했는데 롤업이 ${r} 다`);
    const rank = RANK[ROLLUP_BADGE[r].tone];
    assert.ok(rank >= last, `${w.outcome} 를 더했더니 배지 톤이 내려갔다(${r})`);
    last = rank;
  }

  /* 소비 자리가 이 표를 실제로 쓰는지 — 손으로 칠하는 갈래가 돌아오면 여기서 걸린다 */
  const page = readFileSync(new URL('./DatasetPage.tsx', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(page, /rollupBadge\(flow\.tasks\)\.tone/, '화면이 톤을 다시 손으로 정한다');
  assert.doesNotMatch(page, /<StatusBadge tone="(blocked|warn)">\s*\S*\s*포함/, '갈래별 하드코딩 배지가 돌아왔다');
});

test('같은 데이터셋의 재실행은 run_id별 흐름으로 갈린다', () => {
  const base = { dataset: 'price_daily', task_key: 'collect', plan_status: 'DUE' };
  const flows = datasetRunFlows([
    { ...base, run_id: 'run-1', task_outcome: 'FAILED' },
    { ...base, run_id: 'run-2', task_outcome: 'FULFILLED' },
  ] as TaskFact[]);
  assert.deepEqual(flows.map((flow) => flow.runId), ['run-1', 'run-2']);
  assert.deepEqual(flows.map((flow) => taskRollup(flow.tasks)), ['failed', 'fulfilled']);
});

test('흐름의 화살표 순서는 서버 task_key 정렬이 아니라 stage 순서다', () => {
  const flows = datasetRunFlows([
    { dataset: 'holdings', run_id: 'run', stage: 'feature', task_key: 'LOAD' },
    { dataset: 'holdings', run_id: 'run', stage: 'raw', task_key: 'COLLECT' },
    { dataset: 'holdings', run_id: 'run', stage: 'normalize', task_key: 'NORMALIZE' },
  ] as TaskFact[]);
  assert.deepEqual(flows[0].tasks.map((task) => task.stage), ['raw', 'normalize', 'feature']);
});

test('🔴 기대 기준일이 없으면 FRESH 가 아니다 — 비교 안 한 것이 초록으로 서던 자리다', () => {
  /* 서버는 **actual 이 없을 때만** `unverifiable` 을 단다(ConsoleFactsService). 그래서
   * `contract: true` + actual 있음 + expected 없음 조합이 이 함수에 그대로 도달한다 —
   * 가장 오래된 근거를 가진 작업의 `expected_as_of` 컬럼이 NULL 인 경우다. */
  const f = freshness(ds({ expected_as_of: null, actual_as_of: '2026-08-02' }));
  assert.equal(f.label, '기준 없음');
  assert.notEqual(f.tone, 'active', '비교하지 않은 것을 초록으로 그렸다');
  assert.notEqual(f.label, 'FRESH');
});

test('갈래가 서로를 잡아먹지 않는다 — 넓은 부재부터 좁혀 간다', () => {
  /* 원장의 판정 불가가 가장 넓다: 계약도 기준일도 없는 행에서도 그 사유가 먼저 답해야 한다 */
  assert.equal(
    freshness(ds({ unverifiable: 'ACTUAL_AS_OF_MISSING', contract: false, expected_as_of: null }))
      .label,
    '판정 불가',
  );
  assert.equal(freshness(ds({ contract: false, expected_as_of: null })).label, '계약 없음');
  assert.equal(freshness(ds({ window_contract: true })).label, '창 계약');
});

test('근거가 다 있으면 그때는 판정한다 — 부재 갈래가 값 갈래를 삼키지 않았는가', () => {
  assert.equal(freshness(ds({ actual_as_of: '2026-08-02' })).label, 'STALE', '하루 밀렸다');
  assert.equal(freshness(ds({ actual_as_of: '2026-08-03' })).label, 'FRESH', '같은 날은 최신이다');
  assert.equal(freshness(ds({ actual_as_of: '2026-08-04' })).label, 'FRESH', '앞선 날도 최신이다');
});

test('🔴 actual 이 없는데 사유도 없으면 **FRESH 가 아니다** — 위험한 쪽은 STALE 이 아니라 초록이다', () => {
  /* ⚠️ 이 단언이 예전에는 `notEqual('STALE')` 뿐이었다 — 가장 위험한 결과(FRESH)를 그대로
   * 허용하는 단언이라, 지키려던 계약("근거가 없으면 FRESH 라고 말하지 않는다")의 반례를
   * 통과시켰다. 부재 단언은 **금지할 값이 아니라 허용할 값**으로 써야 한다. */
  const f = freshness(ds({ actual_as_of: null }));
  assert.equal(f.label, '근거 없음');
  assert.notEqual(f.tone, 'active', '근거가 하나도 없는 행을 초록으로 그렸다');
  assert.notEqual(f.label, 'STALE', '근거 없이 STALE 을 단정했다');
});

test('초록은 **근거가 전부 있을 때만** 선다 — 상태 공간 전체에 FRESH 로 새는 조합이 없다', () => {
  /* 집합 순회 — 판정에 쓰이는 **다섯 축을 전부** 곱한다(32조합).
   * ⚠️ 날짜 두 축만 돌던 동안은 `unverifiable`·`contract` 가드를 잘못 좁혀도 통과했다:
   * 그 가드가 무력해져도 두 날짜가 다 있는 행은 여전히 FRESH 라 순회가 아무것도 못 봤다.
   * 불변식이 "근거가 없으면 초록이 아니다"라면 순회도 그 근거들을 전부 흔들어야 한다. */
  const dates = ['2026-08-03', null] as const;
  const green: string[] = [];
  for (const contract of [true, false]) {
    for (const unverifiable of ['ACTUAL_AS_OF_MISSING', null]) {
      for (const window_contract of [true, false]) {
        for (const expected_as_of of dates) {
          for (const actual_as_of of dates) {
            const f = freshness(
              ds({ contract, unverifiable, window_contract, expected_as_of, actual_as_of }),
            );
            if (f.tone === 'active') {
              green.push(
                `contract=${contract} unverifiable=${unverifiable} window=${window_contract} ` +
                  `expected=${expected_as_of} actual=${actual_as_of} → ${f.label}`,
              );
            }
          }
        }
      }
    }
  }
  /* 초록이 서도 되는 자리는 **하나**다: 계약이 있고 · 판정 불가가 아니고 · 창 계약이 아니고 ·
   * 두 날짜가 다 있고 · actual 이 뒤지지 않았다. */
  assert.deepEqual(
    green,
    [
      'contract=true unverifiable=null window=false ' +
        'expected=2026-08-03 actual=2026-08-03 → FRESH',
    ],
    '근거 없이 초록이 섰다',
  );
});
