// 동시 만료(synchronized expiry) — 만료 직후 loader/DB 로 새는 폭을 관측한다
//
// 구성: warm 30s → 의도적 공백(TTL+1s, 요청 0) → burst 60s.
// 공백 동안 전 인스턴스의 L1 엔트리가 동시에 만료되므로, burst 첫 순간 같은 키에
// 대한 미스가 인스턴스 수만큼 겹쳐 loader/DB 로 내려간다. 그 폭(응답 꼬리·DB 쿼리 수)이
// 이 실험의 관측 대상이다. warm 은 LB round-robin 이라 전 인스턴스에 키가 실릴 만큼
// 충분한 요청 수가 필요하다.
//
// 사용 예: BASE_URL=http://localhost:18100 TTL=3s RATE=300 k6 run synchronized-expiry.js
import {
  RATE,
  HOT_TICKER,
  ttlSeconds,
  arrivalRate,
  thresholds,
  summaryTrendStats,
  doRequest,
} from './lib.js';

const WARM_SECONDS = 30;
const GAP_SECONDS = ttlSeconds() + 1; // TTL 이 확실히 지나도록 1s 여유
// burst 는 warm 종료 + 공백 이후에 시작한다.
const BURST_START = `${WARM_SECONDS + GAP_SECONDS}s`;

// warm 은 키를 전 인스턴스에 적재하는 것이 목적 — RATE(=burst 강도)와 무관하게 적당한 수준.
const WARM_RATE = Math.min(RATE, 20);

export const options = {
  scenarios: {
    warm: arrivalRate(WARM_RATE, `${WARM_SECONDS}s`, { exec: 'warm' }),
    burst: arrivalRate(RATE, '60s', { exec: 'burst', startTime: BURST_START }),
  },
  thresholds: thresholds,
  summaryTrendStats: summaryTrendStats,
};

// 동시 만료를 만들려면 단일 키여야 한다 — 여기서는 hot/cold 분산을 쓰지 않는다.
export function warm() {
  doRequest(HOT_TICKER, 'warmup');
}

export function burst() {
  doRequest(HOT_TICKER, 'measure');
}
