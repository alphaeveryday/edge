/* 미리보기 픽스처가 **서버가 낼 수 있는 응답**인가 (ALPHA-738).
 *
 * 이 픽스처는 실 데이터가 0건일 때 화면을 검수하는 데 쓴다. 그래서 여기 담긴 조합이
 * 실 API 가 만들 수 없는 모양이면, 검수는 **존재하지 않는 화면**을 승인하거나 존재하는
 * 경로를 한 번도 안 본다. 실제로 두 번 그렇게 뚫렸다:
 *   · 완료 분석에 `publicationStatus` 를 안 채워 소비자(`hasResult`)가 전부 대기로 읽었다.
 *   · 게시 상태·산문 블록을 아무 픽스처도 안 담아 그 경로를 검수가 못 봤다.
 *
 * 실행: node --test src/mock/preview.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupBySymbol, hasResult } from '../domains/analyses/symbols.ts';
import { MOCK_ANALYSES } from './preview.ts';

test('본문이 있는 분석은 게시 상태를 갖는다 — 서버는 그 조합을 낼 수 없다', () => {
  /* `explanation_result.publication_status` 는 NOT NULL DEFAULT 'DRAFT' 이고 목록 SQL 이
   * 그 테이블만 LEFT JOIN 한다 — 결과 행이 있으면 null 이 나올 수 없다. */
  const impossible = MOCK_ANALYSES.filter(
    (a) =>
      a.publicationStatus === null &&
      (a.result.trim().length > 0 || (a.resultBlocks?.length ?? 0) > 0),
  );
  assert.deepEqual(
    impossible.map((a) => a.id),
    [],
    '본문은 있는데 게시 상태가 없는 픽스처가 있다',
  );
});

test('소비자가 실제로 유효 설명을 읽는다 — 픽스처가 스스로를 무효로 만들지 않는다', () => {
  const groups = groupBySymbol(MOCK_ANALYSES);
  assert.ok(groups.length > 3, '종목이 몇 개는 있어야 이 단언이 의미가 있다');
  const withValid = groups.filter((g) => g.latestValid !== null);
  assert.ok(
    withValid.length >= groups.length - 1,
    `유효 설명이 있는 종목이 ${withValid.length}/${groups.length} 뿐이다 — 픽스처가 소비자 규칙과 어긋난다`,
  );
});

test('검수가 봐야 할 경로를 픽스처가 실제로 담는다', () => {
  /* 하나도 안 담기면 그 경로는 실 데이터가 0건인 동안 한 번도 안 그려진다 */
  assert.ok(
    MOCK_ANALYSES.some((a) => a.publicationStatus === 'PUBLISHED'),
    '무효화 액션은 PUBLISHED 에서만 활성이다(ALPHA-737)',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.resultBlocks?.some((b) => b.text.trim().length > 0)),
    '고객 산문 블록(ALPHA-878) 경로',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.evidence.some((e) => /^[A-Z_]+$/.test(e.type))),
    '실 API 의 영문 근거 코드 경로',
  );
  assert.ok(
    MOCK_ANALYSES.some((a) => a.evidence.some((e) => !/^[A-Z_]+$/.test(e.type))),
    '코드 전환 전 API 가 보내는 한글 폴백 경로',
  );
  /* 결과가 아직 없는 런도 있어야 한다 — 전부 유효면 대기 표현을 검수할 수 없다 */
  assert.ok(MOCK_ANALYSES.some((a) => !hasResult(a)), '결과 없는 런');
});

test('블록 코드·근거 참조가 엔진이 실제로 저장하는 형식이다', () => {
  /* `statics/interval.py` `final_explanation_payload` — 코드는 H·1·2·3·4|N,
   * 참조는 `bars_5m:<ticker>`·`source_event:<id>`. 형식을 지어내면 상세 화면이 그대로
   * 출력하므로 운영 응답에 없는 모양이 정상 UI 로 승인된다. */
  const blocks = MOCK_ANALYSES.flatMap((a) => a.resultBlocks ?? []);
  assert.ok(blocks.length > 0, '블록이 없으면 아래 단언은 아무것도 안 잰다');
  for (const b of blocks) {
    assert.match(b.code, /^(H|N|[1-4])$/, `엔진이 만들지 않는 블록 코드: ${b.code}`);
    for (const ref of b.evidenceRefs) {
      assert.match(ref, /^(bars_5m|source_event):/, `엔진이 만들지 않는 참조 형식: ${ref}`);
    }
  }
});
