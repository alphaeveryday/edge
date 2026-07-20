/* usage 도메인 — mock·real 이 공유하는 타입 */

/** 위젯 호출 추이 (주간 7일 · 월간 30일). */
export interface CallSeries {
  /** 7일치 호출량 (단위: 백만 M) */
  weekly: number[];
  /** 30일치 호출량 (단위: 백만 M) */
  monthly: number[];
  /** 주간 차트 라벨 (요일) */
  weekLabels: string[];
}

/** 앱 점유율 (최근 7일, 스택 막대용). */
export interface AppShare {
  /** 7일 × [앱별 호출량] (단위: 백만 M) */
  rows: number[][];
  /** 요일 라벨 */
  labels: string[];
  /** 앱 이름 (스택 세그먼트 순서와 일치) */
  names: string[];
}

/** 앱별 사용량 요약 (테이블 행). */
export interface UsageByApp {
  name: string;
  /** 호출 수 표시 문자열 (예: "1.24M") */
  calls: string;
  /** P95 지연 표시 문자열 (예: "138ms") */
  p95: string;
  /** 에러율 표시 문자열 (예: "0.21%") */
  err: string;
  /** 점유율 (%) */
  share: number;
}

/** MAU 추이 (최근 N개월). */
export interface Mau {
  /** 월별 MAU (단위: 천 K) */
  months: number[];
  /** 월 라벨 */
  labels: string[];
}

export interface UsageReport {
  callSeries: CallSeries;
  appShare: AppShare;
  byApp: UsageByApp[];
  mau: Mau;
}
