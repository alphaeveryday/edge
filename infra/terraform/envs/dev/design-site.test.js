// 실행: node --test infra/terraform/envs/dev/design-site.test.js
// design.etforca.com 함수가 비밀번호 없이는 아무것도 내주지 않고, 통과한 요청의 경로를 정적 파일로 바꾸는지 확인한다.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, 'design-site.js'), 'utf8').replace(/^import .*$/m, '');
const load = (store) => {
  const cf = { kvs: () => ({ get: async (k) => { if (!(k in store)) throw new Error('not found'); return store[k]; } }) };
  return new Function('cf', `${source}; return handler;`)(cf);
};

const GOOD = Buffer.from('orca:pw').toString('base64');
const event = (uri, auth) => ({ request: { uri, headers: auth ? { authorization: { value: auth } } : {} } });

test('비밀번호가 없거나 틀리면 401 과 로그인 창 헤더를 준다', async () => {
  const handler = load({ basic_auth: GOOD });
  for (const auth of [undefined, 'Basic ' + Buffer.from('orca:no').toString('base64'), GOOD]) {
    const res = await handler(event('/app/', auth));
    assert.strictEqual(res.statusCode, 401);
    assert.match(res.headers['www-authenticate'].value, /^Basic /);
  }
});

test('KeyValueStore 에 키가 없으면 맞는 비밀번호도 막는다(닫힌 쪽)', async () => {
  const res = await load({})(event('/', 'Basic ' + GOOD));
  assert.strictEqual(res.statusCode, 401);
});

test('통과한 요청의 경로를 정적 파일로 바꾼다', async () => {
  const handler = load({ basic_auth: GOOD });
  const cases = {
    '/': '/',
    '/app': '/app/index.html',
    '/app/': '/app/index.html',
    '/app/etf/091160/brief': '/app/index.html',
    '/app/_expo/static/js/web/entry-abc.js': '/app/_expo/static/js/web/entry-abc.js',
    '/screens/': '/screens/index.html',
    '/screens/light/home.png': '/screens/light/home.png',
  };
  for (const [from, to] of Object.entries(cases)) {
    const res = await handler(event(from, 'Basic ' + GOOD));
    assert.strictEqual(res.uri, to, from);
  }
});

test('슬래시 없는 폴더 주소는 슬래시를 붙여 이동시킨다', async () => {
  const res = await load({ basic_auth: GOOD })(event('/screens', 'Basic ' + GOOD));
  assert.strictEqual(res.statusCode, 301);
  assert.strictEqual(res.headers.location.value, '/screens/');
});
