/* sources 도메인 — 데이터 소스 수집 상태. mock·real 공유 타입. */

export type SourceStatus =
  | 'COLLECTING' // 정상 수집
  | 'DELAYED'; // 수집 지연 (재시도 큐 자동 등록)

export interface DataSource {
  name: string;
  provider: string;
  status: SourceStatus;
  lastCollected: string;
  /** 최근 24시간 수집량 (단위 포함 표시 문자열) */
  volume: string;
}

export interface SourceReport {
  /** 마지막 점검 시각 */
  checkedAt: string;
  sources: DataSource[];
}
