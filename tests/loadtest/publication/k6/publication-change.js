// 게시 변경 전파 관측 — 부하 중 새 게시/차단이 응답에 언제 반영되는지 본다
//
// hot ticker 를 고정 rate 로 계속 조회하면서 응답의 `explanation_as_of` 가
// 시작 시점 기준값에서 언제 바뀌는지 센다. 변경 주입(새 게시 INSERT·차단 토글)은
// run-matrix 가 수행한다 — 이 스크립트는 관측만 한다.
//   fresh_responses   기준값과 다른 as_of (변경 반영)
//   stale_responses   기준값과 같은 as_of (캐시가 아직 구본을 준다)
//   blocked_responses 200 + result 부재 (차단·게시분 없음으로 전환 — ADR-0054, 구 204)
//
// 사용 예: BASE_URL=http://localhost:18100 RATE=100 DURATION=3m k6 run publication-change.js
import {
  BASE_URL,
  HOT_TICKER,
  RATE,
  WARMUP,
  DURATION,
  headers,
  hasResult,
  arrivalRate,
  thresholds,
  summaryTrendStats,
  doRequest,
} from './lib.js';
import http from 'k6/http';
import { Counter } from 'k6/metrics';

const freshResponses = new Counter('fresh_responses');
const staleResponses = new Counter('stale_responses');
const blockedResponses = new Counter('blocked_responses');

export const options = {
  scenarios: {
    warmup: arrivalRate(RATE, WARMUP, { exec: 'warmup' }),
    measure: arrivalRate(RATE, DURATION, { exec: 'measure', startTime: WARMUP }),
  },
  thresholds: thresholds,
  summaryTrendStats: summaryTrendStats,
};

// 기준값은 부하 시작 전 1회 조회로 잡는다 — 이후 이 값과 달라지는 순간이 전파 시점이다.
export function setup() {
  const res = http.get(`${BASE_URL}/api/v1/explanations/${HOT_TICKER}`, {
    headers: headers(),
    tags: { phase: 'setup' },
  });
  if (res.status !== 200 || !hasResult(res)) {
    // 게시분 없이 시작하면 기준 as_of 가 없다 — 이후 게시분 응답은 전부 fresh 로 센다.
    return { baselineAsOf: null };
  }
  return { baselineAsOf: res.json('result.explanation_as_of') };
}

export function warmup() {
  doRequest(HOT_TICKER, 'warmup');
}

export function measure(data) {
  const res = doRequest(HOT_TICKER, 'measure');
  if (res.status !== 200) return;
  if (!hasResult(res)) {
    // 차단·게시분 없음 — 상태코드가 아니라 result 부재가 신호다(ADR-0054).
    blockedResponses.add(1);
    return;
  }

  const asOf = res.json('result.explanation_as_of');
  if (asOf === data.baselineAsOf) staleResponses.add(1);
  else freshResponses.add(1);
}
