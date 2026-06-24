/* settings 도메인 — mock·real 이 공유하는 타입 */

export interface OrgProfile {
  name: string;
  /** 사업자등록번호 */
  bizno: string;
  /** 대표 도메인 */
  domain: string;
  /** 담당자 이름 */
  manager: string;
  /** 위젯 요청·감사 로그에 사용되는 고유 식별자 */
  orgId: string;
}

export interface PolicyPreset {
  name: string;
  /** 프리셋 버전 (예: "v2") */
  ver: string;
  /** 적용 상태 */
  state: '적용' | '대기';
}

export interface NotificationSettings {
  /** 이벤트 분석 알림 */
  ev: boolean;
  /** 이상 트래픽 알림 */
  quota: boolean;
  /** 컴플라이언스 경고 */
  comp: boolean;
  /** 주간 리포트 메일 */
  weekly: boolean;
}
