import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend, Rate } from 'k6/metrics';
const voteStatus = new Counter('vote_status');
const voteFailure = new Rate('vote_failure');
const voteLatency = new Trend('vote_latency', true);
// 무관 요청 = 정적 / (Redis·DB 무관, 같은 Tomcat 풀) — actuator health 는 Redis 인디케이터를 포함해 부적합.
const unrelatedLatency = new Trend('unrelated_latency', true);
const unrelatedFailure = new Rate('unrelated_failure');
const base = __ENV.BASE_URL || 'http://localhost:8080';
const etf = __ENV.ETF_ID || '069500';
const runId = Number(__ENV.USER_OFFSET || '1');
export const options = {
  scenarios: {
    votes: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s', duration: '180s', preAllocatedVUs: 100, maxVUs: 300, exec: 'vote' },
    unrelated: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s', duration: '180s', preAllocatedVUs: 20, maxVUs: 100, exec: 'unrelated' },
  },
  summaryTrendStats: ['avg', 'p(95)', 'p(99)', 'max'],
  thresholds: { vote_failure: ['rate==0'], dropped_iterations: ['count==0'], ...(__ENV.SCENARIO === 'S2' ? {vote_latency: ['max<1000']} : {}) },
};
export function vote() {
  const r = http.post(`${base}/api/v1/forecasts/${etf}/votes`, JSON.stringify({choice: ['BUY', 'HOLD', 'SELL'][exec.scenario.iterationInTest % 3]}), {
    headers: {'Content-Type':'application/json', 'X-User-Id': String(runId + exec.scenario.iterationInTest)}, timeout: '10s',
  });
  voteStatus.add(1, {status: String(r.status)});
  voteFailure.add(r.status !== 200);
  voteLatency.add(r.timings.duration);
}
export function unrelated() {
  const r = http.get(`${base}/`, { timeout: '10s' });
  unrelatedLatency.add(r.timings.duration);
  unrelatedFailure.add(r.status !== 200);
}
export function handleSummary(data) {
  return { [__ENV.SUMMARY_PATH || 'summary.json']: JSON.stringify(data, null, 2) };
}
