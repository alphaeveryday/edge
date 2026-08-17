// 핫키 급등 — EDGE 의 존재 이유가 발동하는 순간을 재현한다
//
// 시장이 급변하면 한 종목의 가격이 튀고, 그 종목의 설명 조회가 동시에 폭주한다.
// 즉 쏠림(핫키)과 도착률 급변이 함께 온다. 정상 상태 스윕(hot-key)으로는 잡히지 않는,
// 온셋 순간의 loader/DB 유입과 응답 꼬리가 관측 대상이다.
//
// 사전 등록 가설: 온셋의 loader 호출 수는 키 수 × 인스턴스 수로 상수다.
// 도착률에 비례해 늘어난다면 가설은 기각된다(단일 비행 병합이 듣지 않는다는 뜻).
//
// 사용 예: BASE_URL=http://localhost:18100 k6 run hot-spike.js
import exec from 'k6/execution';
import {
  HOT_TICKER,
  COLD_TICKERS,
  preAllocatedVUs,
  MAX_VUS,
  summaryTrendStats,
  doRequest,
} from './lib.js';

// 쏠림은 이 시나리오의 본질이라 기본값을 hot-key(0.9)보다 세게 잡는다.
const SPIKE_HOT_RATIO = Number(__ENV.HOT_RATIO || 0.99);

const PEAK_RATE = 4000;
const BASE_RATE = 200;

// 구간 경계(초). exec 함수의 경과 시간 판정과 stages 가 같은 축을 쓴다 — 함께 고쳐야 한다.
const ONSET_AT = 60;
const SPIKE_AT = 70;
const COOLDOWN_AT = 130;

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate',
      // 시작부터 baseline 도착률이 나오게 startRate 를 첫 target 과 맞춘다.
      startRate: BASE_RATE,
      timeUnit: '1s',
      // 피크 기준으로 선할당한다. 러너가 4,000/s 를 못 내면 dropped_iterations 로 드러난다
      // — 상한(500)에 걸려 조용히 도착률이 깎이는 상황을 지표로 보이게 하는 구조다.
      preAllocatedVUs: preAllocatedVUs(PEAK_RATE),
      maxVUs: MAX_VUS,
      stages: [
        { target: BASE_RATE, duration: '60s' }, // baseline 정착
        { target: PEAK_RATE, duration: '10s' }, // 급등 램프(온셋)
        { target: PEAK_RATE, duration: '60s' }, // 유지
        { target: BASE_RATE, duration: '30s' }, // 감쇠
      ],
    },
  },
  thresholds: {
    // 판정은 spike 구간만 한다 — baseline·onset 은 관측용이라 상한을 걸지 않는다.
    'checks{phase:spike}': ['rate==1'],
    'http_req_failed{phase:spike}': ['rate<0.01'],
    'http_req_duration{phase:spike}': ['p(99)<600000'],
  },
  summaryTrendStats: summaryTrendStats,
};

// 스테이지 경계는 k6 태그로 안 나온다 — 시나리오 시작 이후 경과 시간으로 직접 가른다.
// exec.scenario.startTime 은 이 시나리오가 시작된 유닉스 시각(ms)이다.
function currentPhase() {
  const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  if (elapsed < ONSET_AT) return 'baseline';
  if (elapsed < SPIKE_AT) return 'onset';
  if (elapsed < COOLDOWN_AT) return 'spike';
  return 'cooldown';
}

// 키가 3종뿐이라 카디널리티 걱정이 없다 — ticker 태그를 유지하는 doRequest 를 쓴다.
function pickSpikeTicker() {
  return Math.random() < SPIKE_HOT_RATIO
    ? HOT_TICKER
    : COLD_TICKERS[Math.floor(Math.random() * COLD_TICKERS.length)];
}

export default function () {
  doRequest(pickSpikeTicker(), currentPhase());
}
