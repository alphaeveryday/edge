// 실행: node --test infra/terraform/modules/static-site/www-redirect.test.js
// www 이동 함수가 대표 주소를 바꾸지 않고(이동 고리 방지) 경로·쿼리를 잃지 않는지 확인한다.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, 'www-redirect.js'), 'utf8');
const handler = new Function(`${source}; return handler;`)();

const request = (host, uri, querystring, method) => ({
  request: { method: method || 'GET', uri, querystring: querystring || {}, headers: { host: { value: host } } },
});

test('대표 주소로 온 요청은 그대로 통과한다 — 이동시키면 고리가 된다', () => {
  const event = request('etforca.com', '/assets/og.png');
  assert.strictEqual(handler(event), event.request);
});

test('www 로 온 요청은 같은 경로의 대표 주소로 301 이동한다', () => {
  const response = handler(request('www.etforca.com', '/outlook/091160.json'));
  assert.strictEqual(response.statusCode, 301);
  assert.strictEqual(response.headers.location.value, 'https://etforca.com/outlook/091160.json');
});

test('쿼리스트링은 여러 값까지 그대로 옮긴다 — 유입 경로 표시(utm)가 이동 중에 사라지면 안 된다', () => {
  const response = handler(
    request('www.etforca.com', '/', {
      utm_source: { value: 'insta' },
      tag: { value: 'a', multiValue: [{ value: 'a' }, { value: 'b%20c' }] },
    }),
  );
  assert.strictEqual(response.headers.location.value, 'https://etforca.com/?utm_source=insta&tag=a&tag=b%20c');
});

test('POST 는 308 로 이동한다 — 301 이면 브라우저가 GET 으로 바꿔 쓰기 요청의 본문이 사라진다', () => {
  const response = handler(request('www.etforca.com', '/api/portfolio/items', {}, 'POST'));
  assert.strictEqual(response.statusCode, 308);
  assert.strictEqual(response.headers.location.value, 'https://etforca.com/api/portfolio/items');
});
