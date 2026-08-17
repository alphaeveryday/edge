// 콜드 스타트 — 캐시가 빈 상태에서 곧바로 부하를 건다
//
// warm-up 이 없는 것이 이 실험의 요지다. 인스턴스 재기동(캐시 비우기)은 run-matrix 가
// 수행하고, 이 스크립트는 재기동 직후 첫 90초의 응답을 전부 측정 대상으로 삼는다.
// 그래서 phase 태그는 전 구간 measure 다.
//
// 사용 예: BASE_URL=http://localhost:18101 RATE=100 k6 run cold-start.js
import {
  RATE,
  arrivalRate,
  thresholds,
  summaryTrendStats,
  pickTicker,
  doRequest,
} from './lib.js';

export const options = {
  scenarios: {
    cold: arrivalRate(RATE, '90s', { exec: 'measure' }),
  },
  thresholds: thresholds,
  summaryTrendStats: summaryTrendStats,
};

export function measure() {
  doRequest(pickTicker(), 'measure');
}
