// 증권사 자체 제작 API(mock-broker) 데모 서버 — 실제 배치에서는 증권사 Backend/API Gateway 자리다.
// 계약 원칙(docs/contracts/publication-api.md): MTS 화면은 Publication API를 직접 호출하지 않는다.
// 화면은 이 서버의 /api/broker/* 만 호출하고, Publication API 호출·고객 해시·채널 부착·폴백 처리는
// 전부 이 레이어 책임이다(ADR-0035 런타임 경로: 위젯 UI → 증권사 백엔드 → On-Prem Publication API).
// 시세(/api/broker/quotes)도 같은 전제로 이 레이어가 외부 소스(토스증권 공식 Open API)를
// 프록시한다 — 시세는 증권사 자체 데이터. 키 미설정이면 스냅샷 폴백으로 동작한다.
// 의존성 0 — node:20 내장 http/fetch 만 사용.
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 8080);
const PUBLICATION_API_URL = process.env.PUBLICATION_API_URL || 'http://localhost:18084';
const STATIC_DIR = path.resolve(__dirname, process.env.STATIC_DIR || '../mts-ai-tab');

// 시세 소스 = 토스증권 공식 Open API (developers.tossinvest.com).
// OAuth 2.0 Client Credentials — 키는 WTS 설정 > Open API 에서 발급하며 허용 IP 등록이 필수다.
// 키 미설정이면 외부 호출 없이 폴백 스냅샷으로 동작한다(키 없이도 데모 성립).
const TOSS_OPENAPI_URL = process.env.TOSS_OPENAPI_URL || 'https://openapi.tossinvest.com';
const TOSS_CLIENT_ID = process.env.TOSS_CLIENT_ID || '';
const TOSS_CLIENT_SECRET = process.env.TOSS_CLIENT_SECRET || '';
const QUOTES_ENABLED = Boolean(TOSS_CLIENT_ID && TOSS_CLIENT_SECRET);
const QUOTES_CACHE_MS = 7000; // 새로고침 연타·관객 다수에도 상류 호출은 7초 1회
// 폴백 스냅샷이 곧 관심종목·지수 유니버스다 — 종목명·ETF 여부는 여기서, 숫자만 실시간 소스에서 온다.
const QUOTES_FALLBACK = JSON.parse(fs.readFileSync(path.join(__dirname, 'quotes-fallback.json'), 'utf8'));

// 데모용 고객 해시 — 실제 해시 생성 규칙·salt는 증권사 관리 영역(ADR-0013, 벤더 불관여).
// 원본 고객 ID/계좌번호는 Publication API로 절대 전달하지 않는다.
const DEMO_CUSTOMER_HASH = 'demo-c7f3a91b2e';
const CHANNEL = 'MTS';

// 계약 권장: 5xx·통신 실패는 폴백 문구로 처리해 설명 미제공이 고객 화면 오류로 보이지 않게 한다.
const FALLBACK_MESSAGE = 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';
const NO_DATA_MESSAGE = '이 종목·일자에 대해 제공되는 AI 분석이 아직 없습니다.';
const UNKNOWN_ETF_MESSAGE = '지원하지 않는 종목입니다. (국내 상장 ETF 대상)';

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

// resolve 값은 broker-api.js 가 화면에 그대로 넘기는 형상:
// { state: 'OK', data } | { state: 'NO_DATA', message } | { state: 'FALLBACK', message }
async function getAiAnalysis(ticker, tradeDate) {
  const upstream = new URL('/api/v1/explanations/' + encodeURIComponent(ticker), PUBLICATION_API_URL);
  if (tradeDate) {
    upstream.searchParams.set('trade_date', tradeDate);
  }
  let res;
  try {
    res = await fetch(upstream, {
      headers: { 'X-Customer-Hash': DEMO_CUSTOMER_HASH, 'X-Channel': CHANNEL },
      signal: AbortSignal.timeout(5000),
    });
  } catch (err) {
    console.warn('[mock-broker] Publication API 호출 실패', err.message);
    return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
  }
  if (res.status === 200) {
    return { state: 'OK', data: await res.json() };
  }
  if (res.status === 204) {
    return { state: 'NO_DATA', message: NO_DATA_MESSAGE };
  }
  if (res.status === 404) {
    return { state: 'NO_DATA', message: UNKNOWN_ETF_MESSAGE };
  }
  if (res.status === 400) {
    // 400은 일시 장애가 아니라 호출측 통합 버그 신호다 — 폴백 문구로 코팅하되 로그에 드러낸다
    console.warn('[mock-broker] Publication API 400 — 요청 파라미터/헤더 확인 필요 (ticker=%s, trade_date=%s)', ticker, tradeDate);
  }
  return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
}

// ── 시세 프록시 — getAiAnalysis 와 대칭인 두 번째 상류 호출 (토스증권 공식 Open API) ──

let tokenCache = { accessToken: null, expiresAt: 0 };
let dailyCandleCache = { date: null, bySymbol: null };
let quotesCache = { at: 0, body: null };

function kstDateOf(value) {
  // en-CA 로캘은 YYYY-MM-DD 형식 — KST 기준 날짜 문자열 비교용
  return new Date(value).toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' });
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

async function getAccessToken() {
  const now = Date.now();
  if (tokenCache.accessToken && now < tokenCache.expiresAt) {
    return tokenCache.accessToken;
  }
  const res = await fetch(TOSS_OPENAPI_URL + '/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: TOSS_CLIENT_ID,
      client_secret: TOSS_CLIENT_SECRET,
    }),
    signal: AbortSignal.timeout(3000),
  });
  if (!res.ok) {
    throw new Error('토스 토큰 발급 실패 ' + res.status);
  }
  const token = await res.json();
  tokenCache = { accessToken: token.access_token, expiresAt: now + (token.expires_in - 60) * 1000 };
  return tokenCache.accessToken;
}

async function fetchOpenApi(pathname) {
  const accessToken = await getAccessToken();
  const res = await fetch(TOSS_OPENAPI_URL + pathname, {
    headers: { Authorization: 'Bearer ' + accessToken },
    signal: AbortSignal.timeout(3000),
  });
  if (res.status === 401) {
    tokenCache = { accessToken: null, expiresAt: 0 }; // 만료·무효 토큰 — 다음 주기에 재발급
  }
  if (!res.ok) {
    throw new Error('시세 소스 ' + res.status + ' (' + pathname + ')');
  }
  return res.json();
}

// 현재가 응답에는 전일대비가 없다 — 일봉(count=2)으로 전일종가를 KST 날짜당 1회 캐시한다.
// 순차 호출: 캔들 그룹 rate limit(초당 5회) 준수. 날짜가 바뀔 때만 도는 경로다.
async function getDailyCandles() {
  const today = kstDateOf(Date.now());
  if (dailyCandleCache.date === today && dailyCandleCache.bySymbol) {
    return dailyCandleCache.bySymbol;
  }
  const bySymbol = {};
  for (const s of QUOTES_FALLBACK.stocks) {
    bySymbol[s.ticker] = (await fetchOpenApi('/api/v1/candles?symbol=' + s.ticker + '&interval=1d&count=2')).candles;
  }
  for (const ix of QUOTES_FALLBACK.indices) {
    bySymbol[ix.code] = (await fetchOpenApi('/api/v1/market-indicators/' + ix.code + '/candles?interval=1d&count=2')).candles;
  }
  dailyCandleCache = { date: today, bySymbol: bySymbol };
  return bySymbol;
}

// 전일종가 = 현재가 시각(거래일)보다 앞선 가장 최근 일봉의 종가.
// 장중(오늘 봉 유무 무관)·장전(마지막 체결=전일)·휴장(주말) 전부 이 규칙 하나로 맞는다.
function computeChange(candles, priceTimestamp, lastPrice) {
  const tradeDate = kstDateOf(priceTimestamp || Date.now());
  const prev = (candles || []).find(function (c) {
    return kstDateOf(c.timestamp) < tradeDate;
  });
  if (!prev) {
    console.warn('[mock-broker] 전일 봉 없음 — 등락 0 처리 (tradeDate=%s)', tradeDate);
    return 0;
  }
  return lastPrice - Number(prev.closePrice);
}

// resolve 값: { state: 'OK', data } | { state: 'FALLBACK', data: 스냅샷 }
// data 형상은 quotes-fallback.json 과 동일 — 폴백이어도 화면은 같은 코드로 그린다.
async function getQuotes() {
  if (!QUOTES_ENABLED) {
    return { state: 'FALLBACK', data: QUOTES_FALLBACK };
  }
  const now = Date.now();
  if (quotesCache.body && now - quotesCache.at < QUOTES_CACHE_MS) {
    return quotesCache.body;
  }
  try {
    const candlesBySymbol = await getDailyCandles();
    const stockSymbols = QUOTES_FALLBACK.stocks.map(function (s) { return s.ticker; }).join(',');
    const indexSymbols = QUOTES_FALLBACK.indices.map(function (ix) { return ix.code; }).join(',');
    const [stockRes, indexRes] = await Promise.all([
      fetchOpenApi('/api/v1/prices?symbols=' + stockSymbols),
      fetchOpenApi('/api/v1/market-indicators/prices?symbols=' + indexSymbols),
    ]);
    const stockBySymbol = new Map(stockRes.result.map(function (p) { return [p.symbol, p]; }));
    const indexBySymbol = new Map(indexRes.result.map(function (p) { return [p.symbol, p]; }));
    const stocks = QUOTES_FALLBACK.stocks.map(function (s) {
      const p = stockBySymbol.get(s.ticker);
      if (!p) {
        throw new Error('현재가 응답에 ' + s.ticker + ' 없음');
      }
      const last = Number(p.lastPrice);
      return { ticker: s.ticker, name: s.name, etf: s.etf, price: last, change: computeChange(candlesBySymbol[s.ticker], p.timestamp, last) };
    });
    const indices = QUOTES_FALLBACK.indices.map(function (ix) {
      const p = indexBySymbol.get(ix.code);
      if (!p) {
        throw new Error('지수 응답에 ' + ix.code + ' 없음');
      }
      const last = Number(p.lastPrice);
      return { code: ix.code, name: ix.name, value: last, change: round2(computeChange(candlesBySymbol[ix.code], p.timestamp, last)) };
    });
    const body = { state: 'OK', data: { indices: indices, stocks: stocks } };
    quotesCache = { at: now, body: body };
    return body;
  } catch (err) {
    console.warn('[mock-broker] 시세 소스 호출 실패 — 스냅샷 폴백', err.message);
    return { state: 'FALLBACK', data: QUOTES_FALLBACK };
  }
}

function sendJson(res, body) {
  // 브로커 응답은 항상 200 — 상태 구분은 body.state 로 전달한다(화면에 HTTP 에러를 흘리지 않는다)
  res.writeHead(200, { 'Content-Type': CONTENT_TYPES['.json'], 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

function serveStatic(res, urlPath) {
  const rel = urlPath === '/' ? 'index.html' : urlPath.replace(/^\/+/, '');
  const filePath = path.normalize(path.join(STATIC_DIR, rel));
  if (!filePath.startsWith(STATIC_DIR + path.sep) && filePath !== path.join(STATIC_DIR, 'index.html')) {
    res.writeHead(403);
    res.end();
    return;
  }
  fs.readFile(filePath, function (err, data) {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not Found');
      return;
    }
    res.writeHead(200, { 'Content-Type': CONTENT_TYPES[path.extname(filePath)] || 'application/octet-stream' });
    res.end(data);
  });
}

const server = http.createServer(function (req, res) {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/broker/quotes') {
    if (req.method !== 'GET') {
      res.writeHead(405);
      res.end();
      return;
    }
    getQuotes()
      .catch(function (err) {
        console.warn('[mock-broker] quotes 처리 실패', err.message);
        return { state: 'FALLBACK', data: QUOTES_FALLBACK };
      })
      .then(function (body) {
        sendJson(res, body);
      });
    return;
  }
  if (url.pathname === '/api/broker/ai-analysis') {
    if (req.method !== 'GET') {
      res.writeHead(405);
      res.end();
      return;
    }
    const ticker = url.searchParams.get('ticker');
    if (!ticker) {
      sendJson(res, { state: 'FALLBACK', message: FALLBACK_MESSAGE });
      return;
    }
    getAiAnalysis(ticker, url.searchParams.get('trade_date'))
      .catch(function (err) {
        // 손상 JSON·URL 조립 실패 등 getAiAnalysis 내부 try 밖 예외 — 응답 미종료로 행 걸리지 않게 폴백
        console.warn('[mock-broker] ai-analysis 처리 실패', err.message);
        return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
      })
      .then(function (body) {
        sendJson(res, body);
      });
    return;
  }
  serveStatic(res, url.pathname);
});

server.listen(PORT, function () {
  console.log('[mock-broker] listening on :%d — static=%s, publication-api=%s', PORT, STATIC_DIR, PUBLICATION_API_URL);
  if (!QUOTES_ENABLED) {
    console.log('[mock-broker] TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 미설정 — 시세는 폴백 스냅샷으로 동작');
  }
});
