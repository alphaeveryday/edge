// 핫키 집중 조회 — 캐시 실험 주력 시나리오 (B0/B1/C1/R1/T1/F1, rate 스윕)
//
// 요청의 HOT_RATIO 비율이 단일 종목에 몰리는 조회 폭증 형태를 유지한 채,
// warm-up 구간과 측정 구간을 시나리오로 분리해 콜드 미스가 p99 를 오염시키지 않게 한다.
// rate 스윕은 호출자(run-matrix.sh)가 RATE 를 바꿔 재실행한다 — 이 스크립트는 단일 rate.
//
// 사용 예: BASE_URL=http://localhost:18100 RATE=200 k6 run hot-key.js
import {
  RATE,
  WARMUP,
  DURATION,
  arrivalRate,
  thresholds,
  summaryTrendStats,
  pickTicker,
  doRequest,
} from './lib.js';

export const options = {
  scenarios: {
    warmup: arrivalRate(RATE, WARMUP, { exec: 'warmup' }),
    // 측정은 warm-up 이 끝난 시점부터 — 캐시가 채워진 정상 상태의 응답만 본다.
    measure: arrivalRate(RATE, DURATION, { exec: 'measure', startTime: WARMUP }),
  },
  thresholds: thresholds,
  summaryTrendStats: summaryTrendStats,
};

export function warmup() {
  doRequest(pickTicker(), 'warmup');
}

export function measure() {
  doRequest(pickTicker(), 'measure');
}
