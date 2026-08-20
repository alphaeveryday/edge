import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { intradayOutcome, kstDateAt, parseIntradayAnalysisTrend } from './intradayAnalysisTrend.ts';

const point = (date: string, triggers = 5, results = triggers) => ({
  date, triggers, observations: triggers, runs: triggers, activeRuns: 0, failedRuns: 0,
  results, published: results,
});

test('현재일과 과거의 0/완료/부분/전량 미생성을 서로 다른 결과로 판정한다', () => {
  assert.equal(intradayOutcome(point('2026-08-20', 5, 0), '2026-08-20').label, '집계 중');
  assert.equal(intradayOutcome(point('2026-08-19', 0, 0), '2026-08-20').label, '발화 없음');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 5), '2026-08-20').label, '결과 생성 완료');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 4), '2026-08-20').label, '일부 미생성 1건');
  assert.equal(intradayOutcome(point('2026-08-19', 5, 0), '2026-08-20').label, '결과 미생성');
  assert.equal(intradayOutcome(point('2026-08-21', 5, 0), '2026-08-20').label, '집계 중');
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
