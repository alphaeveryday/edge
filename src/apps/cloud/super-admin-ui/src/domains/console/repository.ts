/* console 도메인 — repository 인터페이스 (다른 도메인과 같은 관례) */
import type { ConsoleFactsDto } from './types';

export interface ConsoleRepository {
  /**
   * 규칙 엔진이 읽는 사실.
   *
   * @param date 볼 날짜(KST, `YYYY-MM-DD`). 없으면 **원장이 아는 가장 최근 날** —
   *             거래일이라는 보장은 없다. 미래 날짜는 서버가 400 이다.
   */
  facts(date?: string): Promise<ConsoleFactsDto>;
}
