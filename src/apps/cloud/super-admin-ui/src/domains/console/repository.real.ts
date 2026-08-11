/* console 도메인 — super-admin-api 연동 구현 (ALPHA-738) */
import { apiClient } from '../../api/client';
import type { ConsoleRepository } from './repository';
import type { ConsoleFactsDto } from './types';

export const realConsoleRepository: ConsoleRepository = {
  /* 빈 문자열을 부재로 접지 않는다 — `?date=` 를 최신 날짜로 위장하면 서버의 날짜 검증
   * (형식 오류·미래 날짜는 400)이 우회된다(holdingsImpact 와 같은 이유). */
  facts: (date) =>
    apiClient.get<ConsoleFactsDto>(
      date !== undefined ? `/console/facts?date=${encodeURIComponent(date)}` : '/console/facts',
    ),
};
