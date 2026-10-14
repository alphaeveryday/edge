import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { EtfAnalysisRow } from '../../rules/types.ts';
import { etfTable, rowTone, stateTone } from './etfAnalysisView.ts';

const row = (etf: string, outlook: EtfAnalysisRow['outlook'], movement: EtfAnalysisRow['movement']): EtfAnalysisRow =>
  ({ etf, name: etf, outlook, movement });

test('🔴 새 축이 없으면 표는 없다 — 화면이 "기록 없음"을 그릴 근거다', () => {
  assert.equal(etfTable(undefined), null);
  /* 행이 0개인 축은 부재가 아니다 — 그날 분석도 트리거도 없던 날이다 */
  assert.deepEqual(etfTable({ rows: [] }), { rows: [], summary: '0종 · 실패 0 · 시작 못 함 0' });
});

test('문제 있는 ETF 가 먼저 온다 — 실패, 시작 못 함, 진행 중, 판정 보류, 완료, 대상 아님', () => {
  const t = etfTable({ rows: [
    row('F', 'none', 'none'),
    row('E', 'completed', 'none'),
    row('D', 'completed', 'pending'),
    row('C', 'running', 'none'),
    row('B', 'completed', 'not_started'),
    row('A', 'completed', 'failed'),
  ] })!;
  assert.deepEqual(t.rows.map((r) => r.etf), ['A', 'B', 'C', 'D', 'E', 'F']);
});

test('한 종류만 문제여도 앞에 온다 — 다른 종류의 완료가 실패를 뒤로 밀지 않는다', () => {
  const t = etfTable({ rows: [
    row('ok', 'completed', 'completed'),
    row('one', 'completed', 'failed'),
    row('both', 'failed', 'failed'),
  ] })!;
  /* 둘 다 실패인 ETF 가 한쪽만 실패인 ETF 보다 앞이다 */
  assert.deepEqual(t.rows.map((r) => r.etf), ['both', 'one', 'ok']);
});

test('같은 상태면 코드 순이다 — 다시 불러도 자리가 바뀌지 않는다', () => {
  const t = etfTable({ rows: [row('305720', 'completed', 'none'), row('069500', 'completed', 'none')] })!;
  assert.deepEqual(t.rows.map((r) => r.etf), ['069500', '305720']);
});

test('요약은 ETF 수로 센다 — 두 종류가 모두 실패인 ETF 도 실패 1 이다', () => {
  const t = etfTable({ rows: [
    row('A', 'failed', 'failed'),
    row('B', 'completed', 'failed'),
    row('C', 'completed', 'not_started'),
    row('D', 'completed', 'completed'),
  ] })!;
  assert.equal(t.summary, '4종 · 실패 2 · 시작 못 함 1');
});

test('진행 중·판정 보류는 있을 때만 요약에 적는다', () => {
  const t = etfTable({ rows: [row('A', 'running', 'pending'), row('B', 'completed', 'pending')] })!;
  assert.equal(t.summary, '2종 · 실패 0 · 시작 못 함 0 · 진행 중 1 · 판정 보류 2');
});

test('🔴 진행 중과 판정 보류는 문제 색이 아니다 — 아직 끝나지 않은 것을 실패처럼 칠하지 않는다', () => {
  assert.equal(stateTone('running'), 'wait');
  assert.equal(stateTone('pending'), 'wait');
  assert.equal(stateTone('failed'), 'bad');
  assert.equal(stateTone('not_started'), 'bad');
  assert.equal(stateTone('completed'), 'ok');
  assert.equal(stateTone('none'), 'none');
  /* 칸의 색은 먼저 봐야 하는 쪽을 따른다 */
  assert.equal(rowTone(row('A', 'completed', 'failed')), 'bad');
  assert.equal(rowTone(row('B', 'running', 'completed')), 'wait');
  assert.equal(rowTone(row('C', 'completed', 'none')), 'ok');
  assert.equal(rowTone(row('D', 'none', 'none')), 'none');
});
