// publication-api 조회 폭증 baseline 부하 스크립트 (ALPHA-496)
//
// 시나리오: 특정 종목 급등 → 동일 아이템 집중 조회. 요청의 HOT_RATIO 비율이
// 단일 종목(HOT_TICKER)에 몰리고 나머지가 COLD_TICKERS 로 분산된다.
// 성공 조회의 서버 쓰기는 serving_request_metric 1행이다(ADR-0053 으로
// exposure_log INSERT 폐지) — 캐시(ALPHA-433)로 가릴 수 없는 잔여 쓰기
// 경로가 이것이라는 게 측정의 핵심이다.
//
// 실행 절차·측정 기록 방법은 같은 폴더의 README.md 참조.
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:18084';
const HOT_TICKER = __ENV.HOT_TICKER || '069500';
const COLD_TICKERS = (__ENV.COLD_TICKERS || '305720,091160').split(',');
const HOT_RATIO = Number(__ENV.HOT_RATIO || 0.9);
const RATE = Number(__ENV.RATE || 50); // 초당 도착 요청 수 — 스윕하며 재실행한다
const DURATION = __ENV.DURATION || '1m';

export const options = {
  scenarios: {
    surge: {
      // open model: 응답이 늦어도 도착률을 유지한다 — 조회 폭증의 실제 형태.
      // 러너가 도착률을 못 따라가면 dropped_iterations 로 드러난다(포화 신호).
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      // 런타임 VU 확장은 부하 발생기 자원을 소비해 측정을 왜곡한다 — RATE 에 비례해 선할당.
      preAllocatedVUs: Math.min(Math.max(50, RATE), 500),
      maxVUs: 500,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    // 계약 밖 응답(201·3xx 등)은 http_req_failed 에 안 잡힌다 — check 실패를
    // 실행 실패로 승격해 잘못된 실행이 성공 baseline 으로 기록되지 않게 한다.
    // 200 만 정상(ADR-0054 — 게시분 없음도 200 + result 부재)이므로 단 1건의
    // 이탈(404·구 204 등)도 fail-loud 로 드러낸다.
    checks: ['rate==1'],
  },
  // 기본 출력엔 p(99)가 없다 — README 기록 항목과 맞춘다.
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
};

// 200 응답 수 — serving_request_metric DB 카운트와 대조하는 교차 검증 지표.
const okResponses = new Counter('ok_responses');

export default function () {
  const hot = Math.random() < HOT_RATIO;
  const ticker = hot
    ? HOT_TICKER
    : COLD_TICKERS[Math.floor(Math.random() * COLD_TICKERS.length)];
  // 헤더 없음 — 위젯 직접 호출 계약(ADR-0053, 고객 식별·채널 헤더 폐지).
  const res = http.get(`${BASE_URL}/api/v1/explanations/${ticker}`, {
    tags: { hot: String(hot) },
  });

  // 성공은 항상 200 + 성공 envelope 다(ADR-0054). 404·비 envelope 200 은 계약 위반 신호다.
  // 키 순서 고정(@JsonPropertyOrder — isSuccess 첫 키)이라 prefix 검사가 형상 판별이 된다.
  check(res, {
    'status 200 envelope': (r) =>
      r.status === 200 && typeof r.body === 'string' && r.body.indexOf('{"isSuccess":true') === 0,
  });
  // 게시분 노출 응답만 센다 — result 부재 200(게시분 없음)은 제외(구 204 와 같은 구분).
  // result 값은 항상 객체다 — "result":null 명시 같은 계약 위반은 세지 않는다.
  if (res.status === 200 && typeof res.body === 'string' && res.body.indexOf('"result":{') !== -1) {
    okResponses.add(1);
  }
}
