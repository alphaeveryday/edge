/* compliance 도메인 — mock 구현 (가상 증권사 "한빛투자증권" 기준)
 *
 * mock 도 반드시 Promise 를 반환한다(async). mock 과 real 의 로딩/에러 처리
 * 모양을 동일하게 유지하기 위함이다.
 */
import type { ComplianceRepository } from './repository';
import type { DisclaimerDoc, Keyword } from './types';

/** 네트워크 지연을 흉내내 로딩 상태가 실제처럼 동작하게 한다. */
function delay<T>(value: T, ms = 280): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

const SEED_DISCLAIMER: DisclaimerDoc = {
  version: 'v4',
  text: '본 분석은 정보 제공을 목적으로 하며 특정 종목의 매매를 권유하지 않습니다. 투자 판단의 최종 책임은 투자자 본인에게 있으며, 과거의 성과가 미래의 수익을 보장하지 않습니다. 본 콘텐츠는 한빛투자증권의 투자권유로 해석되지 않습니다.',
  autoAttach: true,
};

const SEED_KEYWORDS: Keyword[] = ['추천', '매수 타이밍', '리딩', '보장 수익', '급등 임박', '지금 사세요'];

const BLOCKED_TODAY = 14;

/** 모듈 수명 동안 유지되는 in-memory 상태 (발행·추가·제거 시 누적). */
let disclaimer: DisclaimerDoc = { ...SEED_DISCLAIMER };
const keywords: Keyword[] = [...SEED_KEYWORDS];

/** "v4" → "v5" 처럼 버전 숫자를 1 올린다. */
function bumpVersion(version: string): string {
  const match = version.match(/^v(\d+)$/);
  if (!match) return version;
  return `v${Number(match[1]) + 1}`;
}

export const mockComplianceRepository: ComplianceRepository = {
  getDisclaimer() {
    return delay({ ...disclaimer });
  },

  publishDisclaimer(text: string) {
    disclaimer = { ...disclaimer, version: bumpVersion(disclaimer.version), text };
    return delay({ ...disclaimer });
  },

  listKeywords() {
    return delay([...keywords]);
  },

  addKeyword(word: string) {
    const trimmed = word.trim();
    if (trimmed && !keywords.includes(trimmed)) keywords.push(trimmed);
    return delay([...keywords]);
  },

  removeKeyword(word: string) {
    const idx = keywords.indexOf(word);
    if (idx !== -1) keywords.splice(idx, 1);
    return delay([...keywords]);
  },

  getBlockedToday() {
    return delay(BLOCKED_TODAY);
  },
};
