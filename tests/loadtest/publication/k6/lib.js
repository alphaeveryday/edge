// 캐시 실험용 k6 공용 모듈 (publication-api 조회 경로)
//
// 용도: hot-key·synchronized-expiry·cold-start·publication-change 네 시나리오가
// 공유하는 파라미터 파싱·티커 선택·헤더·요청 함수·threshold 관례를 모은다.
// 관례는 tests/loadtest/publication-baseline.js 를 계승한다.
//
// 사용 예: import { doRequest, scenarioDefaults } from './lib.js';
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:18100'; // nginx LB. 1대 실험은 :18101 직결 주입
export const HOT_TICKER = __ENV.HOT_TICKER || '069500';
export const COLD_TICKERS = (__ENV.COLD_TICKERS || '305720,091160').split(',');
export const HOT_RATIO = Number(__ENV.HOT_RATIO || 0.9);
export const RATE = Number(__ENV.RATE || 50); // 초당 도착 요청 수 — 스윕은 호출자가 재실행으로 한다
export const WARMUP = __ENV.WARMUP || '30s';
export const DURATION = __ENV.DURATION || '3m';

// TTL 은 "3" 또는 "3s" 두 형태를 받는다 — synchronized-expiry 의 공백 구간 계산에 쓴다.
export function ttlSeconds() {
  const raw = String(__ENV.TTL || '3s').trim();
  const m = raw.match(/^(\d+(?:\.\d+)?)(ms|s|m)?$/);
  if (!m) {
    throw new Error(`TTL 형식이 잘못됐다: ${raw} (예: 3 또는 3s)`);
  }
  const n = Number(m[1]);
  if (m[2] === 'ms') return n / 1000;
  if (m[2] === 'm') return n * 60;
  return n;
}

// 캐시 히트/미스 자체는 서버 지표로 본다. 여기서는 200(게시분 있음)과
// 204(게시분 없음·차단)를 분리 집계해 응답 구성이 바뀌는 순간을 잡는다.
export const status200 = new Counter('status_200');
export const status204 = new Counter('status_204');

// 런타임 VU 확장은 부하 발생기 자원을 소비해 측정을 왜곡한다 — RATE 에 비례해 선할당.
export function preAllocatedVUs(rate) {
  return Math.min(Math.max(50, rate), 500);
}
export const MAX_VUS = 500;

// open model: 응답이 늦어도 도착률을 유지한다. 러너가 못 따라가면 dropped_iterations 로 드러난다.
export function arrivalRate(rate, duration, extra) {
  return Object.assign(
    {
      executor: 'constant-arrival-rate',
      rate: rate,
      timeUnit: '1s',
      duration: duration,
      preAllocatedVUs: preAllocatedVUs(rate),
      maxVUs: MAX_VUS,
    },
    extra || {}
  );
}

// warm-up 구간의 콜드 미스가 측정치를 오염시키면 안 된다 — 전부 phase:measure 로 한정한다.
export const thresholds = {
  // 값 자체는 SLO 가 아니라 요약에 서브메트릭을 노출시키기 위한 상한이다.
  'http_req_duration{phase:measure}': ['p(99)<600000'],
  'http_req_failed{phase:measure}': ['rate<0.01'],
  // 계약 밖 응답(404 등)은 http_req_failed 에 안 잡힌다 — check 실패를 실행 실패로 승격.
  'checks{phase:measure}': ['rate==1'],
};

// 기본 출력엔 p(99)가 없다 — baseline 기록 항목과 맞춘다.
export const summaryTrendStats = ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'];

export function pickTicker() {
  return Math.random() < HOT_RATIO
    ? HOT_TICKER
    : COLD_TICKERS[Math.floor(Math.random() * COLD_TICKERS.length)];
}

export function headers() {
  // setup() 컨텍스트에는 __ITER 가 없다(ReferenceError → exit 107) — setup 에서도
  // 안전하게 쓰도록 가드한다. VU 컨텍스트에서는 기존과 동일한 값이다.
  const iter = typeof __ITER === 'undefined' ? 0 : __ITER;
  return {
    // 요청마다 유사-고유 해시 — 증권사 백엔드가 고객별 해시를 부착하는 계약 재현.
    'X-Customer-Hash': `k6-${__VU}-${iter}`,
    'X-Channel': Math.random() < 0.8 ? 'MTS' : 'HTS',
  };
}

// phase 는 'warmup' | 'measure'. 태그는 요청과 check 양쪽에 붙여야 서브메트릭이 갈린다.
export function doRequest(ticker, phase) {
  const res = http.get(`${BASE_URL}/api/v1/explanations/${ticker}`, {
    headers: headers(),
    tags: { phase: phase, ticker: ticker },
  });

  // 200(게시분 노출)·204(게시분 없음 — 정상)만 정상. 404 는 시드/지원종목 불일치 신호다.
  check(
    res,
    { 'status 200/204': (r) => r.status === 200 || r.status === 204 },
    { phase: phase }
  );
  if (res.status === 200) status200.add(1);
  else if (res.status === 204) status204.add(1);

  return res;
}

// ── 워킹셋 스윕용 (working-set.js) ────────────────────────────────────────────

// 합성 티커 이름 규칙. 시드 스크립트와 공통 계약이다 — 한쪽만 바꾸면 전건 404 가 된다.
// 1-based: syntheticTicker(1) === 'LT00001', syntheticTicker(5000) === 'LT05000'.
export function syntheticTicker(i) {
  return 'LT' + String(i).padStart(5, '0');
}

// 워킹셋 N 종에서 키를 뽑는다. hotRatio 만큼은 HOT_TICKER 로 쏠리고(현실 분포 대조용),
// 나머지는 1..n 균등이다. hotRatio=0 이면 순수 균등 — 키당 도달률 모델을 그대로 검정한다.
export function pickWorkingSet(n, hotRatio) {
  if (Math.random() < hotRatio) return HOT_TICKER;
  return syntheticTicker(1 + Math.floor(Math.random() * n));
}

// doRequest 와 동일하되 tags 에 ticker 를 넣지 않는다.
// WHY: 워킹셋 N=5,000 이면 ticker 태그가 k6 시계열을 키 수만큼 쪼개 고카디널리티로 터진다.
// 티커별 분해는 서버(Prometheus) 지표에도 없으므로 클라이언트도 총량만 본다.
export function doRequestLite(ticker, phase) {
  const res = http.get(`${BASE_URL}/api/v1/explanations/${ticker}`, {
    headers: headers(),
    tags: { phase: phase },
  });

  check(
    res,
    { 'status 200/204': (r) => r.status === 200 || r.status === 204 },
    { phase: phase }
  );
  if (res.status === 200) status200.add(1);
  else if (res.status === 204) status204.add(1);

  return res;
}
