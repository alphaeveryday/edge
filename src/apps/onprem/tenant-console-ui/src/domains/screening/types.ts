/* screening 도메인 — 점검 기준 (금칙어·자동 제공 기준·면책 문구). mock·real 공유 타입. */
import type { RiskLevel } from '../explanations/types';

export type WordAction = 'REVIEW' | 'BLOCK';

export interface BannedWord {
  id: number;
  text: string;
  risk: RiskLevel;
  action: WordAction;
  active: boolean;
  registeredAt: string;
}

export interface AutoPublishCriteria {
  /** 자동 제공 최소 출처 수 */
  minSources: 1 | 2 | 3;
  /** 자동 제공 최소 확신도 — null=미설정(게이트 꺼짐). LOW(보류) 허용은 미설정과
   * 실질 동일이라 설정 어휘에 없다(ALPHA-634). */
  minConfidence: 'MEDIUM' | 'HIGH' | null;
}

export interface NewBannedWord {
  text: string;
  risk: RiskLevel;
  action: WordAction;
}

export interface PolicyVersionSummary {
  versionNo: number;
  publishedAt: string;
  /** 발행자 이름 — 원장 조회 실패(탈퇴 등) 시 null */
  publishedBy: string | null;
  active: boolean;
  autoPublishEnabled: boolean;
  minSources: number | null;
  minConfidence: string | null;
}
