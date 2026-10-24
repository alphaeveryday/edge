/* 실행: node --test src/domains/analysisResults/labels.test.ts */
import { strict as assert } from 'node:assert';
import test from 'node:test';
import { SOURCE_LABEL, labelOf, publicationLabel } from './labels.ts';

const AT = '2026-10-13T00:05:00Z';

test('회수가 게시보다 먼저다 — 회수된 게시본을 "게시"로 말하지 않는다', () => {
  assert.deepEqual(publicationLabel({ published: false, publishedAt: AT, withdrawn: true }), { label: '회수됨', at: AT });
  /* 서버가 published 를 잘못 참으로 보내도 회수가 이긴다 — 순서가 바뀌면 이 단언이 깨진다 */
  assert.equal(publicationLabel({ published: true, publishedAt: AT, withdrawn: true }).label, '회수됨');
  assert.deepEqual(publicationLabel({ published: true, publishedAt: AT, withdrawn: false }), { label: '게시', at: AT });
});

test('게시 조건을 못 채운 행은 게시 시각이 남아 있어도 게시 안 됨이다', () => {
  /* 합성 입력·실패 행에도 published_at 이 있을 수 있다 — 시각을 보이면 게시된 것처럼 읽힌다 */
  assert.deepEqual(publicationLabel({ published: false, publishedAt: AT, withdrawn: null }), { label: '게시 안 됨', at: null });
  assert.equal(publicationLabel({ published: false, publishedAt: null, withdrawn: false }).label, '게시 안 됨');
});

test('모르는 코드 값은 지어내지 않고 그대로, 값이 없으면 대시', () => {
  assert.equal(labelOf(SOURCE_LABEL, 'synthetic'), '합성 입력');
  assert.equal(labelOf(SOURCE_LABEL, 'new_source'), 'new_source');
  assert.equal(labelOf(SOURCE_LABEL, null), '—');
});
