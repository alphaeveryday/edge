/* analysisResults 도메인 — 저장된 코드 값 → 화면 말 (뷰 관심사).
 * 화면(.tsx)에 두지 않는다 — `node --test` 가 .tsx 를 집지 못해 판단에 테스트가 붙지 않는다. */
import type { BadgeTone } from 'ui-kit';
import type { AnalysisKind, AnalysisResultStatus } from './types';

export const KIND_LABEL: Record<AnalysisKind, string> = { outlook: '전망', movement: '가격변동 분석' };

export const STATUS_TONE: Record<AnalysisResultStatus, BadgeTone> = {
  completed: 'active',
  failed: 'blocked',
  running: 'neutral',
};

/** 입력 출처(data_source) — 운영 입력은 따로 표시하지 않는다 */
export const SOURCE_LABEL: Record<string, string> = {
  database: '운영 입력',
  synthetic: '합성 입력',
  unknown: '출처 미상',
};
export const DETAIL_MODE_LABEL: Record<string, string> = {
  create: '새로 작성',
  update: '고쳐 씀',
  rewrite: '다시 작성',
};
export const CHANGE_TYPE_LABEL: Record<string, string> = { added: '추가', modified: '수정', deleted: '삭제' };
export const SENTIMENT_LABEL: Record<string, string> = { positive: '긍정', neutral: '중립', negative: '부정' };
export const KEYWORD_KIND_LABEL: Record<string, string> = { support: '지지', burden: '부담' };

/** 표에 없는 값은 지어내지 않고 저장된 값 그대로 보인다 */
export const labelOf = (table: Record<string, string>, value: string | null | undefined): string =>
  value == null ? '—' : (table[value] ?? value);

/**
 * 게시 칸의 말. 회수가 게시보다 먼저다 — 회수된 게시본은 서버가 `published=false` 로 내므로 게시 시각만
 * 보고 "게시"라 하면 회수된 분석이 나간 것처럼 읽힌다. 시각 표기는 화면이 한다(`at`).
 */
export function publicationLabel(r: {
  published: boolean;
  publishedAt: string | null;
  withdrawn: boolean | null;
}): { label: '회수됨' | '게시' | '게시 안 됨'; at: string | null } {
  if (r.withdrawn) return { label: '회수됨', at: r.publishedAt };
  if (r.published) return { label: '게시', at: r.publishedAt };
  return { label: '게시 안 됨', at: null };
}
