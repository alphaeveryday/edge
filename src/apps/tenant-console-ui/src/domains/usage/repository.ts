/* usage 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { UsageReport } from './types';

export interface UsageRepository {
  /** 사용량 분석 리포트 (호출 추이·앱 점유율·앱별 사용량·MAU) */
  getUsage(): Promise<UsageReport>;
}
