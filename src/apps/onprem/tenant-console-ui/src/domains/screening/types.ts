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
  /** 활성 정책 존재 여부 — false 면 아직 발행 전이라 설명 판정 자체가 진행되지 않는다
   * (screening-worker: 정책 부재 = 진행 중단). 이때 나머지 값은 첫 발행에 쓰일 기반값이다. */
  published: boolean;
  /** 자동 제공 스위치 — false 면 어디에도 걸리지 않은 설명까지 검수로 간다(룰·UNCERTAIN 은
   * 평가기에서 이보다 먼저 판정돼 스위치와 무관하게 적용된다). PATCH 로 변경한다. */
  autoPublishEnabled: boolean;
  /** 자동 제공 최소 출처 수 — null=미설정(출처 수 조건 없음). 확신도와 같은 처리다. */
  minSources: 1 | 2 | 3 | null;
  /** 자동 제공 최소 확신도 — null=미설정(게이트 꺼짐). LOW(보류) 허용은 미설정과
   * 실질 동일이라 설정 어휘에 없다(ALPHA-634). */
  minConfidence: 'MEDIUM' | 'HIGH' | null;
}

/** 룰 타입 어휘 — 코드와 함께만 확장된다(ADR-0018, screening_rule CHECK 와 1:1). */
export type RuleType = 'BANNED_WORD' | 'SINGLE_SOURCE' | 'ASSERTIVE_EXPRESSION';

/** 활성 정책의 룰 인스턴스 — 금칙어 표면(BannedWord)과 달리 타입을 가리지 않는다. */
export interface ScreeningRule {
  id: number;
  ruleType: RuleType;
  /** params.text — 텍스트 매칭이 없는 타입(SINGLE_SOURCE)은 null */
  text: string | null;
  action: WordAction;
  enabled: boolean;
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
