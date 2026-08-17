// 워킹셋 스윕 — L1 이 무력화되는 경계를 실측한다
//
// 키 수 N 을 바꿔가며 같은 도착률로 때린다. 키가 많아질수록 키 하나가 TTL 안에
// 다시 조회될 확률이 떨어지고, 어느 지점부터 L1 은 채워지기만 하고 쓰이지 않는다.
// N 은 호출자(run-matrix.sh)가 재실행으로 스윕한다 — 이 스크립트는 단일 N.
//
// 사전 등록 가설: 키당 TTL 내 도달률(= RATE / N × TTL)이 1 미만이면 캐시는 무력하다.
//
// 사용 예: WORKING_SET=1088 RATE=1600 k6 run working-set.js
import {
  RATE,
  WARMUP,
  DURATION,
  arrivalRate,
  thresholds,
  summaryTrendStats,
  pickWorkingSet,
  doRequestLite,
} from './lib.js';

// 워킹셋 크기는 실험의 독립변수다 — 기본값을 두면 어떤 N 을 쟀는지 모른 채 결과가 남는다.
const WORKING_SET = Number(__ENV.WORKING_SET);
if (!Number.isFinite(WORKING_SET) || WORKING_SET < 1) {
  throw new Error(`WORKING_SET 을 1 이상 정수로 지정하라 (받은 값: ${__ENV.WORKING_SET})`);
}

// 기본 0 = 순수 균등. 도달률 모델 검정이 목적이라 쏠림을 넣지 않는다.
// 0.9 를 주면 hot-key 와 같은 현실 분포로 대조할 수 있다.
const WS_HOT_RATIO = Number(__ENV.HOT_RATIO || 0);

export const options = {
  scenarios: {
    warmup: arrivalRate(RATE, WARMUP, { exec: 'warmup' }),
    // 측정은 warm-up 이 끝난 시점부터 — 첫 적재의 콜드 미스를 측정에서 뺀다.
    measure: arrivalRate(RATE, DURATION, { exec: 'measure', startTime: WARMUP }),
  },
  thresholds: thresholds,
  summaryTrendStats: summaryTrendStats,
};

export function warmup() {
  doRequestLite(pickWorkingSet(WORKING_SET, WS_HOT_RATIO), 'warmup');
}

export function measure() {
  doRequestLite(pickWorkingSet(WORKING_SET, WS_HOT_RATIO), 'measure');
}
