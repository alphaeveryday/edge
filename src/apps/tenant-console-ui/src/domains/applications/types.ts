/* applications 도메인 — mock·real 이 공유하는 타입 */

/** 애플리케이션 (앱 목록·상세) */
export interface Application {
  id: string;
  /** 라우트 식별자 (예: "mts") */
  slug: string;
  name: string;
  /** 앱 유형 (아이콘 선택에 사용) */
  type: '모바일' | '웹';
  /** 배포 환경 (production | staging) */
  env: string;
  /** 위젯 호출 (오늘) 표시 문자열 */
  calls: string;
  /** 에러율 표시 문자열 */
  callsErr: string;
  /** MAU 표시 문자열 */
  mau: string;
  /** P95 레이턴시 표시 문자열 */
  p95: string;
  /** 활성 키 개수 */
  keys: number;
  /** 활성 웹훅 개수 */
  hooks: number;
}

/** 위젯 키 (조직 전역) */
export interface WidgetKey {
  key: string;
  label: string;
  scope: string;
  /** 키 환경 (live | test) */
  env: string;
  /** 발급일 표시 문자열 */
  created: string;
  /** 최근 사용 표시 문자열 */
  used: string;
}

/** 웹훅 엔드포인트 (조직 전역) */
export interface Webhook {
  url: string;
  /** 구독 이벤트 표시 문자열 */
  events: string;
  /** 상태 ("활성" | "일시정지") */
  status: string;
  /** 성공률 표시 문자열 */
  rate: string;
}

export interface CreateAppInput {
  name: string;
  type: '모바일' | '웹';
  env: string;
}

export interface IssueKeyInput {
  label: string;
  env: string;
  scope: string;
}

export interface AddWebhookInput {
  url: string;
  events: string;
}
