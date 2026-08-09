/* 신선도 판정 — **근거가 없으면 초록이 아니다** (ALPHA-738 D).
 *
 * 이 파일이 지키는 것은 산식이 아니라 **부재의 갈래**다. 초록(FRESH)은 "봤고 괜찮다"라,
 * 비교하지 않은 것·비교할 수 없는 것이 그 칸에 서면 계측 공백이 정상으로 읽힌다.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { DatasetFact } from '../../rules/types.ts';
import { freshness } from './datasetFreshness.ts';

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
