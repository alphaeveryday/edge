/* 판정 근거 표현 모델 테스트(§33.12 로컬).
 *
 * 지키는 의도: 계산과 반영은 다른 사실이다(유니크 무시·tx 차단·회수 무효를 '발화/회수'로 뭉개지 않는다),
 * 기록 없음·이력 없음을 추정으로 채우지 않는다, 비잠금 관측을 잠금 관측처럼 말하지 않는다.
 *
 * 실행: node --test src/domains/sources/judgmentView.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  baseline, committed, computed, inputLabel, involved, judgmentErrors, txObservation,
} from './judgmentView.ts';
import type { MinuteJudgmentAttempt, MinuteJudgmentWindow } from './types.ts';

const attempt = (over: Partial<MinuteJudgmentAttempt>): MinuteJudgmentAttempt => ({
  attempt: 1, redriveGeneration: 0, judgedAt: '2026-10-05T00:02:03Z', txAnchorLocked: true,
  detectionPolicyVersion: 'p', summary: {}, anchorsUsed: {}, txAnchor: {}, baselines: {},
  judgedWithBaseline: 2, ...over,
});

test('계산한 발화와 확정 결과를 따로 말한다', () => {
  const s = { fired: ['A', 'B', 'C'], inserted: ['A'], stale_tx: ['B'], revert_candidates: ['D'], reverted: [] };
  assert.equal(computed(s, 'B'), '발화');
  assert.equal(committed(s, 'A'), '트리거 확정');
  assert.equal(committed(s, 'B'), '차단(tx 에서 더 늦은 앵커 확인)');
  assert.equal(committed(s, 'C'), '무시(같은 window 트리거가 이미 있음)');
  assert.equal(committed(s, 'D'), '회수 무효(조건부 갱신 0행)');
  assert.equal(committed(s, 'E'), '없음');
});

test('오류 문자열은 종목으로 세지 않는다', () => {
  const a = attempt({ summary: { fired: ['A'], errors: ['A 의 close 가 양수가 아니다'] }, anchorsUsed: { B: ['1', 'x'] } });
  assert.deepEqual(involved(a), ['A', 'B']);
});

test('비잠금 관측과 읽은 뒤 바뀐 앵커를 구분한다', () => {
  const a = attempt({
    txAnchorLocked: false,
    anchorsUsed: { A: ['104', '2026-10-05T00:00:00Z'] },
    txAnchor: { A: '2026-10-05T00:03:00Z' },
  });
  assert.equal(txObservation(a, 'A'), '09:03 (비잠금 관측 · 읽은 뒤 바뀜)');
  assert.equal(txObservation(a, 'Z'), '관측 안 함');
});

test('출처 세대 미상과 이력 없음을 추정으로 채우지 않는다', () => {
  assert.match(baseline({ value: 98, source: null, ref: null }), /출처 세대 미상/);
  assert.equal(baseline(undefined), '기준선 기록 없음');
  const w = {
    jobId: 'job-w1', windowStart: '2026-10-05T00:01:00Z', windowGeneration: 2, jobGeneration: 1, jobStatus: 'SUCCEEDED',
    jobAttemptCount: 1, correctedAfter: true, inputRecord: 'NO_HISTORY', artifactUri: null,
    artifactChecksum: null, sourceRecheck: 'NOT_PERFORMED', attempts: [],
  } satisfies MinuteJudgmentWindow;
  assert.equal(inputLabel(w),
    '판정 당시 입력: 세대 1 · artifact 이력 없음(이력 부재 — 원본 삭제·손상을 뜻하지 않음) · 현재 세대로 대체하지 않음 · 이후 세대 2로 정정됨');
});

test('이력 기록이 있어도 원본 검증 완료로 말하지 않는다', () => {
  const w = {
    jobId: 'job-w1', windowStart: '2026-10-05T00:01:00Z', windowGeneration: 1, jobGeneration: 1, jobStatus: 'SUCCEEDED',
    jobAttemptCount: 1, correctedAfter: false, inputRecord: 'RECORDED', artifactUri: 's3://x', artifactChecksum: 'a',
    sourceRecheck: 'NOT_PERFORMED', attempts: [],
  } satisfies MinuteJudgmentWindow;
  assert.match(inputLabel(w), /원본 본문 재검증 안 함/);
  assert.doesNotMatch(inputLabel(w), /검증 완료|존재함/);
});

test('앵커가 있는 종목의 판정 오류도 무발화로 말하지 않는다', () => {
  const a = attempt({
    summary: { fired: [], errors: ['500000 의 close 가 양수가 아니다: 0'], error_entities: ['500000'] },
    anchorsUsed: { '500000': ['104', '2026-10-05T00:02:00Z'] },
  });
  assert.deepEqual(involved(a), ['500000']);
  assert.equal(computed(a.summary, '500000'), '판정 불가(오류)');
});

test('판정 오류는 무발화로 뭉개지 않는다', () => {
  const a = attempt({ summary: { fired: [], errors: ['500000 의 close 가 양수가 아니다: 0'] } });
  assert.deepEqual(involved(a), []);
  assert.deepEqual(judgmentErrors(a), ['500000 의 close 가 양수가 아니다: 0']);
});
