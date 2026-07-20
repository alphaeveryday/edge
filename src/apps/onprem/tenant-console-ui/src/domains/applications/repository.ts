/* applications 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type {
  Application,
  WidgetKey,
  Webhook,
  CreateAppInput,
  IssueKeyInput,
  AddWebhookInput,
} from './types';

export interface ApplicationsRepository {
  /** 앱 목록 */
  listApps(): Promise<Application[]>;
  /** slug 로 앱 단건 조회 (없으면 null) */
  getApp(slug: string): Promise<Application | null>;
  /** 앱 생성 — 생성된 앱을 반환 */
  createApp(input: CreateAppInput): Promise<Application>;
  /** 위젯 키 목록 (조직 전역) */
  listKeys(): Promise<WidgetKey[]>;
  /** 키 발급 — 생성된 키를 반환 */
  issueKey(input: IssueKeyInput): Promise<WidgetKey>;
  /** 키 재발급 — 새 값으로 교체된 키를 반환 */
  regenerateKey(key: string): Promise<WidgetKey>;
  /** 키 폐기 */
  revokeKey(key: string): Promise<void>;
  /** 웹훅 목록 (조직 전역) */
  listWebhooks(): Promise<Webhook[]>;
  /** 웹훅 추가 — 생성된 웹훅을 반환 */
  addWebhook(input: AddWebhookInput): Promise<Webhook>;
  /** 대상 ETF 유니버스 */
  getUniverse(): Promise<string[]>;
}
