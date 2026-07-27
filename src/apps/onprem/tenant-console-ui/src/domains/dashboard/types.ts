/* dashboard 도메인 — 타입 정의 */

/** 제공 API 트래픽 집계(최근 24시간) — 에러율은 화면이 파생한다. */
export interface TrafficSummary {
  totalRequests: number;
  errorRequests: number;
}
