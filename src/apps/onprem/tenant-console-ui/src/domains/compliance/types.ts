/* compliance 도메인 — mock·real 이 공유하는 타입 */

export interface DisclaimerDoc {
  /** 면책 문구 버전 (예: "v4") */
  version: string;
  text: string;
  /** 모든 분석 출력 하단에 자동 부착 여부 */
  autoAttach: boolean;
}

/** 키워드 블랙리스트 항목 */
export type Keyword = string;
