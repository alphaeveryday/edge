import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend, Rate } from 'k6/metrics';
import { bearer } from './token.js';
const voteStatus = new Counter('vote_status');
const voteFailure = new Rate('vote_failure');
const voteLatency = new Trend('vote_latency', true);
const readSource = new Counter('read_source');
const readLatency = new Trend('read_latency', true);
const unrelatedLatency = new Trend('unrelated_latency', true);
const unrelatedFailure = new Rate('unrelated_failure');
const base = __ENV.BASE_URL || 'http://localhost:8080';
const rate = Number(__ENV.RATE || '50');
const readRate = Number(__ENV.READ_RATE || '50');
const duration = __ENV.DURATION || '180s';
// 슬롯 범위 단위의 샤드 구분, 기본값은 --cluster create 의 3등분
// 러너가 CLUSTER SLOTS 로 읽은 범위의 주입 가능
const slotRanges = (__ENV.SLOT_RANGES || '0-5460,5461-10922,10923-16383').split(',').map(r => r.split('-').map(Number));
const perShard = Number(__ENV.FORECASTS_PER_SHARD || '10');
const hotShard = Number(__ENV.HOT_SHARD || '0');
const hotShare = Number(__ENV.HOT_SHARE || '0.5');
// VU 별 init 실행 때문에 Date.now() 기본값이면 VU 마다 종목 집합이 갈리므로 러너의 주입값 사용
if (!__ENV.FORECAST_BASE) throw new Error('FORECAST_BASE required');
const forecastBase = Number(__ENV.FORECAST_BASE);

// Redis CLUSTER KEYSLOT 과 같은 CRC16-CCITT(XMODEM) 기반 슬롯 계산
// 투표 키의 해시태그는 etfCode
function crc16(s) {
  let crc = 0;
  for (let i = 0; i < s.length; i++) {
    crc ^= s.charCodeAt(i) << 8;
    for (let b = 0; b < 8; b++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
  }
  return crc;
}
export function slotOf(etfCode) { return crc16(String(etfCode)) % 16384; }
function shardOf(slot) { return slotRanges.findIndex(([lo, hi]) => slot >= lo && slot <= hi); }

// 샤드마다 perShard 개가 찰 때까지 forecastBase 부터의 순차 탐색
// 각 샤드의 첫 원소가 인기 종목
// 6자리 종목 코드라 하위 6자리 사용
const shards = slotRanges.map(() => []);
for (let id = forecastBase; shards.some(s => s.length < perShard); id++) {
  const code = String(id % 1000000).padStart(6, '0');
  const s = shardOf(slotOf(code));
  if (s >= 0 && shards[s].length < perShard) shards[s].push(code);
}
const hot = shards[hotShard][0];
// cold 종목의 샤드 라운드로빈 배치
// 샤드별 연속 배치 시 장애 샤드 요청이 뭉쳐 20건 서킷 창의 실패율이 실제 비중을 넘는 문제 방지
const cold = [];
for (let k = 0; k < perShard; k++) for (const s of shards) if (s[k] !== hot) cold.push(s[k]);

export const options = {
  scenarios: {
    votes: { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration, preAllocatedVUs: 100, maxVUs: 300, exec: 'vote' },
    reads: { executor: 'constant-arrival-rate', rate: readRate, timeUnit: '1s', duration, preAllocatedVUs: 50, maxVUs: 300, exec: 'read' },
    unrelated: { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration, preAllocatedVUs: 20, maxVUs: 100, exec: 'unrelated' },
  },
  summaryTrendStats: ['avg', 'p(95)', 'p(99)', 'max'],
};
export function setup() {
  return { shards, hot };
}
// hotShare 비율의 인기 종목과 나머지 cold 균등 분배
// 결정적이면서 hot 과 cold 가 고르게 섞이는 황금비 수열
function pick(i) {
  const forecast = (i * 0.618033988749895) % 1 < hotShare ? hot : cold[i % cold.length];
  return [forecast, {shard: String(shardOf(slotOf(forecast))), forecast: String(forecast), hot: String(forecast === hot)}];
}
export function vote() {
  const i = exec.scenario.iterationInTest;
  const [forecast, t] = pick(i);
  const user = 1 + i;
  const choice = ['buy', 'wait', 'sell'][i % 3];
  const r = http.put(`${base}/api/v1/etfs/${forecast}/vote`, JSON.stringify({choice}), {
    headers: {'Content-Type':'application/json', 'Authorization': bearer(user)}, timeout: '10s',
  });
  const tags = Object.assign({status: String(r.status)}, t);
  voteStatus.add(1, tags);
  voteFailure.add(r.status !== 200, tags);
  voteLatency.add(r.timings.duration, tags);
}
// 서킷 열림 시 읽기의 DB 폴백
// 응답 source 기준의 폴백 QPS 집계
export function read() {
  const [forecast, t] = pick(exec.scenario.iterationInTest);
  const r = http.get(`${base}/api/v1/etfs/${forecast}/vote/count`, { timeout: '10s' });
  let source = 'error';
  if (r.status === 200) { try { source = r.json('result.source'); } catch (e) {} }
  const tags = Object.assign({status: String(r.status), source}, t);
  readSource.add(1, tags);
  readLatency.add(r.timings.duration, tags);
}
export function unrelated() {
  const r = http.get(`${base}/terms.html`, { timeout: '10s' });
  unrelatedLatency.add(r.timings.duration, {status: String(r.status)});
  unrelatedFailure.add(r.status !== 200);
}
export function handleSummary(data) {
  data.forecasts = { shards, hot, hotShard, hotShare, slotRanges };
  return { [__ENV.SUMMARY_PATH || 'summary.json']: JSON.stringify(data, null, 2) };
}
