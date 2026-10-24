import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend, Rate } from 'k6/metrics';
import { bearer } from './token.js';
const voteStatus = new Counter('vote_status');
const voteFailure = new Rate('vote_failure');
const voteLatency = new Trend('vote_latency', true);
// Redis 와 DB 에 무관하고 같은 Tomcat 풀을 쓰는 정적 / 의 무관 요청
// Redis 인디케이터를 포함하는 actuator health 의 부적합
const unrelatedLatency = new Trend('unrelated_latency', true);
const unrelatedFailure = new Rate('unrelated_failure');
const base = __ENV.BASE_URL || 'http://localhost:8080';
const etf = __ENV.ETF_ID || '069500';
const runId = Number(__ENV.USER_OFFSET || '1');
// USER_POOL 설정 시 같은 사용자의 다른 choice 재투표
// 미설정 시 요청마다 고유 사용자
const userPool = Number(__ENV.USER_POOL || '0');
export const options = {
  scenarios: {
    votes: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s', duration: '180s', preAllocatedVUs: 100, maxVUs: 300, exec: 'vote' },
    unrelated: { executor: 'constant-arrival-rate', rate: 50, timeUnit: '1s', duration: '180s', preAllocatedVUs: 20, maxVUs: 100, exec: 'unrelated' },
  },
  summaryTrendStats: ['avg', 'p(95)', 'p(99)', 'max'],
  thresholds: { vote_failure: ['rate==0'], dropped_iterations: ['count==0'], ...(__ENV.SCENARIO === 'S2' ? {vote_latency: ['max<1000']} : {}) },
};
export function vote() {
  const i = exec.scenario.iterationInTest;
  const user = runId + (userPool ? i % userPool : i);
  const choice = ['buy', 'wait', 'sell'][(userPool ? Math.floor(i / userPool) + i % userPool : i) % 3];
  const r = http.put(`${base}/api/v1/etfs/${etf}/vote`, JSON.stringify({choice}), {
    headers: {'Content-Type':'application/json', 'Authorization': bearer(user)}, timeout: '10s',
  });
  voteStatus.add(1, {status: String(r.status), user: String(user), choice});
  voteFailure.add(r.status !== 200);
  voteLatency.add(r.timings.duration);
}
export function unrelated() {
  const r = http.get(`${base}/terms.html`, { timeout: '10s' });
  unrelatedLatency.add(r.timings.duration);
  unrelatedFailure.add(r.status !== 200);
}
export function handleSummary(data) {
  return { [__ENV.SUMMARY_PATH || 'summary.json']: JSON.stringify(data, null, 2) };
}
