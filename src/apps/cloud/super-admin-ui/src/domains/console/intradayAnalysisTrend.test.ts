import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { intradayOutcome, kstDateAt, parseIntradayAnalysisTrend } from './intradayAnalysisTrend.ts';

/* 옛 설명 엔진 단계는 10-02 이후 0 이다 — 판정이 그 값을 읽으면 아래 완료 판정이 전부 "미완료"가 된다.
 * 그래서 옛 단계는 0 으로 두고 새 분석 엔진 단계(`analysis`)만 다르게 둔다. */
const point = (date: string, triggers = 5, completed = triggers, blind = false) => ({
  date, triggers, observations: 0, runs: 0, activeRuns: 0, failedRuns: 0, results: 0, published: 0,
  analysis: { blind, requested: triggers, requestPending: 0, started: triggers, startPending: 0, completed, published: completed },
});

test('현재일과 과거의 0/완료/부분/전량 미완료를 서로 다른 결과로 판정한다 — 새 분석 엔진의 분석 완료를 본다', () => {
  assert.equal(intradayOutcome(point('2026-08-20', 5, 0), '2026-08-20').label, '집계 중');
  assert.equal(intradayOutcome(point('2026-08-19', 0, 0), '2026-08-20').label, '발화 없음');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 5), '2026-08-20').label, '분석 완료');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 4), '2026-08-20').label, '일부 미완료 1건');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 0), '2026-08-20').label, '분석 미완료');
  assert.equal(intradayOutcome(point('2026-08-21', 5, 0), '2026-08-20').label, '집계 중');
});

test('🔴 유예 시간 안의 트리거가 남은 지난 날은 미완료가 아니라 판정 보류다 — 흐름 띠와 같은 판단이다', () => {
  const p = point('2026-08-19', 3, 1);
  p.analysis = { ...p.analysis, requested: 3, started: 1, startPending: 2 };
  const outcome = intradayOutcome(p, '2026-08-20');
  assert.equal(outcome.kind, 'collecting');
  assert.equal(outcome.label, '판정 보류 2건');
  /* 판정 보류가 없으면 같은 수가 미완료로 선다 */
  p.analysis = { ...p.analysis, startPending: 0 };
  assert.equal(intradayOutcome(p, '2026-08-20').kind, 'partial');
});

test('🔴 판정 보류가 확정된 미완료를 가리지 않는다 — 미완료는 판정 보류를 뺀 나머지다', () => {
  /* 트리거 5 · 완료 1 · 요청 판정 보류 1 → 확정된 미완료 3 */
  const p = point('2026-08-19', 5, 1);
  p.analysis = { ...p.analysis, requested: 4, requestPending: 1, started: 1 };
  assert.deepEqual(intradayOutcome(p, '2026-08-20'), { kind: 'partial', label: '일부 미완료 3건', missing: 3 });
  /* 완료가 0 이어도 판정 보류가 있으면 "전량 미완료"가 아니다 */
  const q = point('2026-08-19', 2, 0);
  q.analysis = { ...q.analysis, requested: 1, requestPending: 1, started: 0 };
  assert.deepEqual(intradayOutcome(q, '2026-08-20'), { kind: 'partial', label: '일부 미완료 1건', missing: 1 });
});

test('🔴 새 분석 엔진의 요청이 없던 날은 관측 불가이고, 그 단계가 없는 옛 응답은 상태 미제공이다 — 어느 쪽도 미완료로 그리지 않는다', () => {
  assert.equal(intradayOutcome(point('2026-09-20', 5, 0, true), '2026-10-20').kind, 'blind');
  const { analysis: _dropped, ...old } = point('2026-09-20', 5, 0);
  assert.equal(intradayOutcome(old, '2026-10-20').kind, 'unavailable');
});

test('옛 응답(새 분석 엔진 단계 없음)도 받고, 오면 단계 불변식을 본다', () => {
  const asOf = '2026-08-20T01:00:00Z';
  const { analysis: _dropped, ...old } = point('2026-08-20');
  assert.deepEqual(parseIntradayAnalysisTrend({ asOf, points: [old] }, '2026-08-20', 1).points[0], old);
  const bad = (analysis: Record<string, unknown>) =>
    assert.throws(() => parseIntradayAnalysisTrend({ asOf, points: [{ ...point('2026-08-20'), analysis }] }, '2026-08-20', 1),
      JSON.stringify(analysis));
  const base = point('2026-08-20').analysis;
  bad({ ...base, completed: 6, published: 0 });           // 완료가 시작보다 많다
  bad({ ...base, requested: 4, requestPending: 2 });       // 도달 + 판정 보류가 트리거보다 많다
  bad({ ...base, blind: 'false' });                        // 관측 불가 표시가 불리언이 아니다
  bad({ ...base, startPending: -1 });                      // 음수
});

test('DB asOf의 KST 날짜를 현재일 경계로 사용한다', () => {
  assert.equal(kstDateAt('2026-08-19T15:05:00Z'), '2026-08-20');
});

test('정확한 연속 일수와 단계 불변식을 가진 응답만 받는다', () => {
  const valid = { asOf: '2026-08-20T01:00:00Z', points: [point('2026-08-19'), point('2026-08-20')] };
  assert.deepEqual(parseIntradayAnalysisTrend(valid, '2026-08-20', 2), valid);
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, points: valid.points.slice(1) }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, points: [point('2026-08-18'), point('2026-08-20')] }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, points: [{ ...point('2026-08-19'), results: 6 }, point('2026-08-20')] }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, points: [{ ...point('2026-08-19'), activeRuns: 4, failedRuns: 2 }, point('2026-08-20')] }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend(valid, '2026-08-19', 2));
  assert.throws(() => parseIntradayAnalysisTrend(
    { ...valid, points: [point('2026-08-20'), point('2026-08-21')] },
    '2026-08-21',
    2,
  ));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, asOf: '2026-08-20' }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, asOf: '2026-02-29T01:00:00Z' }, undefined, 2));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, asOf: '2026-08-20T24:00:00Z', points: [point('2026-08-20'), point('2026-08-21')] }, undefined, 2));
  assert.deepEqual(
    parseIntradayAnalysisTrend(
      { ...valid, asOf: '2026-08-20T15:00:00Z', points: [point('2026-08-19'), point('2026-08-20')] },
      undefined,
      2,
    ).points.at(-1)?.date,
    '2026-08-20',
    'API maxDate 계산과 DB asOf 조회 사이의 KST 자정 경합은 정상 응답이다',
  );
  assert.throws(() => parseIntradayAnalysisTrend(
    { ...valid, asOf: '2026-08-20T03:00:00Z', points: [point('2026-08-18'), point('2026-08-19')] },
    undefined,
    2,
  ));
  assert.throws(() => parseIntradayAnalysisTrend({ ...valid, points: [point('2026-08-17'), point('2026-08-18')] }, undefined, 2));
});

test('실행 이력은 30일 API를 한 번 조회하고 순수 판정을 스트립과 상세가 함께 쓴다', () => {
  const source = readFileSync(new URL('../../pages/GridPage.tsx', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  assert.equal(source.match(/useIntradayAnalysisTrend\(undefined, 30\)/g)?.length, 1);
  assert.match(source, /intradayOutcome\(point, current\)/);
  assert.match(source, /<IntradayOutcomeDetail/);
  assert.doesNotMatch(source, /\/console\/facts\?date=/, '날짜별 facts N+1이 되살아났다');
  const gridPageOwner = source.indexOf('const [selectedOutcomeDate, setSelectedOutcomeDate]');
  assert.ok(gridPageOwner > 0 && gridPageOwner < source.indexOf('{isError ? ('), '독립 축 선택은 grid query 분기 위에서 소유한다');
  const gridBody = source.slice(source.indexOf('function GridBody'), source.indexOf('const OUTCOME_TONE'));
  assert.doesNotMatch(gridBody, /setSelectedOutcomeDate/, '그리드 전용 전환이 독립 축 선택을 직접 지우지 않는다');
  assert.match(
    gridBody,
    /selectedRowMissing[\s\S]*!dates\.some\(\(date\) => rolled\.has/,
    '재조회로 데이터셋 행 전체가 사라진 선택만 정리하고 유효한 계획 없음 셀은 남긴다',
  );
  assert.doesNotMatch(source, /OutcomeWithFallback/, '독립 축 상세를 grid query 분기마다 다시 마운트하지 않는다');
  assert.match(source, /id="gd-outcome-detail" tabIndex=\{-1\}/, '선택 상세는 프로그래밍 방식으로 초점을 받을 수 있다');
});
