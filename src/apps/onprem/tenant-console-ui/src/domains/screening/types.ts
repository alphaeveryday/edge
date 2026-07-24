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
  /** 자동 제공 허용 위험 등급 상한 */
  maxRisk: 'LOW' | 'MEDIUM';
}

export interface NewBannedWord {
  text: string;
  risk: RiskLevel;
  action: WordAction;
}
