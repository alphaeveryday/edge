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
// 폴백 스냅샷이 곧 종목(검색 전체·관심종목=앞 4종)·지수 유니버스다 — 종목명·ETF 여부는 여기서, 숫자만 실시간 소스에서 온다.
const QUOTES_FALLBACK = JSON.parse(fs.readFileSync(path.join(__dirname, 'quotes-fallback.json'), 'utf8'));

// 데모용 고객 해시 — 실제 해시 생성 규칙·salt는 증권사 관리 영역(ADR-0013, 벤더 불관여).
// 원본 고객 ID/계좌번호는 Publication API로 절대 전달하지 않는다.
const DEMO_CUSTOMER_HASH = 'demo-c7f3a91b2e';
const CHANNEL = 'MTS';

// 계약 권장: 5xx·통신 실패는 폴백 문구로 처리해 설명 미제공이 고객 화면 오류로 보이지 않게 한다.
const FALLBACK_MESSAGE = 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';
const NO_DATA_MESSAGE = '이 종목·일자에 대해 제공되는 AI 분석이 아직 없습니다.';
const UNKNOWN_ETF_MESSAGE = '지원하지 않는 종목입니다. (국내 상장 ETF 대상)';
const CHART_FALLBACK_MESSAGE = '차트를 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';

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
let quotesInFlight = null;
let chartCandleCache = { date: null, bySymbol: {} };
let minuteChartCache = { bySymbol: {} }; // { ticker: { at, candles } } — TTL 기반(장중 매분 새 봉)
let chartInFlight = {};

function kstDateOf(value) {
  // en-CA 로캘은 YYYY-MM-DD 형식 — KST 기준 날짜 문자열 비교용.
  // null·비정상 시각은 null 반환 — new Date(null)=1970 이 날짜 비교를 오염시키지 않게.
  const d = new Date(value);
  if (value == null || isNaN(d.getTime())) {
    return null;
  }
  return d.toLocaleDateString('en-CA', { timeZone: 'Asia/Seoul' });
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

// 상류 decimal 문자열 → 숫자. 비정상 값은 OK 로 게시하지 않고 폴백으로 넘긴다(Rule 12).
// Number() 단독은 null·빈 문자열을 0 으로 강제하므로 타입·공백을 먼저 거른다.
function toFiniteNumber(value, label) {
  const acceptable = typeof value === 'number' || (typeof value === 'string' && value.trim() !== '');
  const n = acceptable ? Number(value) : NaN;
  if (!Number.isFinite(n)) {
    throw new Error('시세 값 비정상 (' + label + '=' + value + ')');
  }
  return n;
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
    const err = new Error('시세 소스 ' + res.status + ' (' + pathname + ')');
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// 차트 조회가 콜드 웜업(일봉 재수집 250ms 간격)과 겹치면 순간적으로 초당 한도(5회)를
// 넘을 수 있다 — 429 만 1초 뒤 1회 재시도한다(상류 Retry-After: 1 실측).
async function fetchOpenApiRetry429(pathname) {
  try {
    return await fetchOpenApi(pathname);
  } catch (err) {
    if (err.status !== 429) {
      throw err;
    }
    await sleep(1100);
    return fetchOpenApi(pathname);
  }
}

// 현재가 응답에는 전일대비가 없다 — 일봉(count=2)으로 전일종가를 KST 날짜당 1회 캐시한다.
// 순차 호출: 캔들 그룹 rate limit(초당 5회) 준수. 날짜가 바뀔 때만 도는 경로다.
async function getDailyCandles() {
  const today = kstDateOf(Date.now());
  if (dailyCandleCache.date === today && dailyCandleCache.bySymbol) {
    return dailyCandleCache.bySymbol;
  }
  const bySymbol = {};
  // 캔들 응답은 { result: { candles, nextBefore } } envelope 다 (openapi.json allOf ApiResponse)
  for (const s of QUOTES_FALLBACK.stocks) {
    bySymbol[s.ticker] = (await fetchOpenApi('/api/v1/candles?symbol=' + s.ticker + '&interval=1d&count=2')).result.candles;
    await sleep(250); // 종목 6연속이 차트 그룹 한도(초당 5회)를 넘지 않게 간격을 둔다
  }
  for (const ix of QUOTES_FALLBACK.indices) {
    bySymbol[ix.code] = (await fetchOpenApi('/api/v1/market-indicators/' + ix.code + '/candles?interval=1d&count=2')).result.candles;
    await sleep(250);
  }
  dailyCandleCache = { date: today, bySymbol: bySymbol };
  return bySymbol;
}

// ── 차트 프록시 — 종목 상세 차트 탭의 시계열 (조회된 종목만) ──
// 일봉(1d): 기간 1주~1년, KST 날짜당 1회 캐시(하루 안 불변).
// 분봉(1m): '1일' 기간, 최근 거래일 규칙 + TTL 60초 캐시(장중 매분 새 봉).

// 일봉 목표 봉 수 — 화면 기간 최대치(1년)를 거래일 기준으로 채운다.
const CHART_CANDLE_TARGET = 250;
// 분봉 수집 상한 — 시간외 거래 종목(비 ETF)은 08:00~20:00 하루 720봉(실측)이라 여유를 둔다.
// 실제 페이지 수는 거래일 경계 조기 종료가 줄인다(ETF 390봉이면 2~3페이지에서 멈춘다).
const MINUTE_FETCH_TARGET = 800;
const MINUTE_CACHE_MS = 60000;

function kstTimeOf(value) {
  return new Date(value).toLocaleTimeString('en-GB', {
    timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

// count 상한 200(실측: src/apps/cloud/data-pipeline/src/data_pipeline/sources/toss.py 헤더 주석)을
// nextBefore 로 이어 붙인다. 반환은 상류 그대로 최신순(newest-first)·중복 제거본.
// stopAtDayBoundary: 최신 봉과 다른 날짜에 닿으면 조기 종료 — 최근 거래일 필터용 수집에서
// 불필요한 페이지를 막는다(호출측이 날짜 필터를 최종 적용한다).
async function fetchCandlePages(ticker, interval, target, stopAtDayBoundary) {
  let rows = [];
  let cursor = null;
  while (rows.length < target) {
    if (rows.length > 0) {
      await sleep(250); // 차트 그룹 rate limit(초당 5회) 간격 — getDailyCandles 와 동일
    }
    // +1: before 는 inclusive 라 다음 페이지 첫 봉이 직전 페이지 마지막과 겹칠 수 있다 — 겹침 제거 후에도 목표를 채우게
    const count = Math.min(target - rows.length + (rows.length > 0 ? 1 : 0), 200);
    const page = (await fetchOpenApiRetry429('/api/v1/candles?symbol=' + ticker
      + '&interval=' + interval + '&count=' + count
      + (cursor ? '&before=' + encodeURIComponent(cursor) : ''))).result;
    let candles = page.candles;
    if (rows.length > 0) {
      // 겹침 제거 — 깨진 시각은 조용히 걸러지지 않게 던진다(NaN 비교는 전부 false 라 행이 소리 없이 사라진다, Rule 12)
      const oldest = new Date(rows[rows.length - 1].timestamp).getTime();
      if (isNaN(oldest)) {
        throw new Error('캔들 시각 비정상 (' + ticker + '=' + rows[rows.length - 1].timestamp + ')');
      }
      candles = candles.filter(function (c) {
        const at = new Date(c.timestamp).getTime();
        if (isNaN(at)) {
          throw new Error('캔들 시각 비정상 (' + ticker + '=' + c.timestamp + ')');
        }
        return at < oldest;
      });
    }
    if (candles.length === 0) {
      break; // 진전 없음 — 무한 루프 방지
    }
    rows = rows.concat(candles);
    if (!page.nextBefore) {
      break;
    }
    cursor = page.nextBefore;
    if (stopAtDayBoundary && kstDateOf(rows[rows.length - 1].timestamp) !== kstDateOf(rows[0].timestamp)) {
      break; // 최신 거래일 경계를 넘었다 — 더 받을 이유가 없다
    }
  }
  return rows;
}

function toChartCandle(c, ticker, withTime) {
  const date = kstDateOf(c.timestamp);
  if (date == null) {
    throw new Error('캔들 시각 비정상 (' + ticker + '=' + c.timestamp + ')');
  }
  const out = {
    date: date,
    open: toFiniteNumber(c.openPrice, ticker + '.open'),
    high: toFiniteNumber(c.highPrice, ticker + '.high'),
    low: toFiniteNumber(c.lowPrice, ticker + '.low'),
    close: toFiniteNumber(c.closePrice, ticker + '.close'),
    volume: toFiniteNumber(c.volume, ticker + '.volume'),
  };
  if (withTime) {
    out.time = kstTimeOf(c.timestamp); // 분봉 x축 라벨용 (KST HH:MM, 봉 구간의 끝 — 실측 right-labeled)
  }
  return out;
}

async function fetchChartCandles(ticker) {
  const rows = await fetchCandlePages(ticker, '1d', CHART_CANDLE_TARGET, false);
  const candles = rows.slice(0, CHART_CANDLE_TARGET).reverse().map(function (c) { // 화면은 과거→현재 순으로 그린다
    return toChartCandle(c, ticker, false);
  });
  if (candles.length === 0) {
    // 빈 성공을 OK 로 캐시하면 그 티커는 하루 종일 빈 화면에 갇힌다 — 폴백으로 수렴시켜 재시도를 살린다(Rule 12)
    throw new Error('일봉 응답이 비었다 (' + ticker + ')');
  }
  return candles;
}

// 최근 거래일 규칙: "오늘"이 아니라 최신 봉의 날짜로 필터한다 — 장중엔 오늘, 장전·휴장엔
// 자동으로 직전 거래일이 나와 휴장 캘린더 관리가 필요 없다(실 MTS 도 장전엔 전일 분봉을 보여준다).
async function fetchMinuteCandles(ticker) {
  const rows = await fetchCandlePages(ticker, '1m', MINUTE_FETCH_TARGET, true);
  if (rows.length === 0) {
    throw new Error('분봉 응답이 비었다 (' + ticker + ')');
  }
  const latestDay = kstDateOf(rows[0].timestamp);
  if (latestDay == null) {
    throw new Error('캔들 시각 비정상 (' + ticker + '=' + rows[0].timestamp + ')');
  }
  const candles = rows
    .filter(function (c) {
      const day = kstDateOf(c.timestamp);
      if (day == null) {
        // 깨진 시각을 조용히 걸러내면 봉·거래량 집계가 결손된 채 캐시된다 — 던져서 폴백 수렴(Rule 12)
        throw new Error('캔들 시각 비정상 (' + ticker + '=' + c.timestamp + ')');
      }
      return day === latestDay;
    })
    .reverse()
    .map(function (c) { return toChartCandle(c, ticker, true); });
  if (candles.length === 0) {
    throw new Error('분봉 응답이 비었다 (' + ticker + ')');
  }
  return candles;
}

// resolve 값: { state: 'OK', data: { candles } } | { state: 'FALLBACK', message }
async function getChartCandles(ticker, interval) {
  if (!QUOTES_ENABLED) {
    return { state: 'FALLBACK', message: CHART_FALLBACK_MESSAGE };
  }
  if (interval === '1m') {
    const cached = minuteChartCache.bySymbol[ticker];
    if (cached && Date.now() - cached.at < MINUTE_CACHE_MS) {
      // ageMs: 캐시 나이 — 화면이 자기 신선도 판정에서 이 나이를 승계해 이중 TTL 로 낡지 않게 한다
      return { state: 'OK', data: { candles: cached.candles, ageMs: Date.now() - cached.at } };
    }
  } else {
    const today = kstDateOf(Date.now());
    if (chartCandleCache.date !== today) {
      chartCandleCache = { date: today, bySymbol: {} }; // 날짜 롤오버 — 전날 봉 세트 폐기
    }
    if (chartCandleCache.bySymbol[ticker]) {
      return { state: 'OK', data: { candles: chartCandleCache.bySymbol[ticker] } };
    }
  }
  // 동시 요청은 진행 중 조회 하나를 공유한다 — getQuotes 의 스탬피드 방지와 동일
  const flightKey = ticker + ':' + interval;
  if (!chartInFlight[flightKey]) {
    const startDate = kstDateOf(Date.now());
    chartInFlight[flightKey] = (interval === '1m' ? fetchMinuteCandles(ticker) : fetchChartCandles(ticker))
      .then(function (candles) {
        if (interval === '1m') {
          minuteChartCache.bySymbol[ticker] = { at: Date.now(), candles: candles };
          return { state: 'OK', data: { candles: candles, ageMs: 0 } };
        }
        if (chartCandleCache.date === startDate) { // 시작 날짜와 캐시 날짜가 같을 때만 — 자정 걸친 응답을 오늘 캐시로 남기지 않는다
          chartCandleCache.bySymbol[ticker] = candles;
        }
        return { state: 'OK', data: { candles: candles } };
      })
      .catch(function (err) {
        console.warn('[mock-broker] 차트 소스 호출 실패 (%s %s) — %s', ticker, interval, err.message);
        return { state: 'FALLBACK', message: CHART_FALLBACK_MESSAGE };
      })
      .finally(function () {
        delete chartInFlight[flightKey];
      });
  }
  return chartInFlight[flightKey];
}

// 전일종가 = 현재가 시각(거래일)보다 앞선 가장 최근 일봉의 종가.
// 장중(오늘 봉 유무 무관)·장전(마지막 체결=전일)·휴장(주말) 전부 이 규칙 하나로 맞는다.
// 전일 봉이 없으면 등락을 지어내지 않고 던진다 — 호출측이 폴백 스냅샷으로 수렴(Rule 12).
function computeChange(candles, priceTimestamp, lastPrice, label) {
  // 체결 미발생(null)만 오늘로 간주 — 깨진 시각은 조용히 오늘로 대체하지 않고 던진다.
  const tradeDate = priceTimestamp == null ? kstDateOf(Date.now()) : kstDateOf(priceTimestamp);
  if (tradeDate == null) {
    throw new Error('현재가 시각 비정상 (' + label + '=' + priceTimestamp + ')');
  }
  // 시각이 깨진 봉은 전일 후보에서 제외한다.
  const prev = (candles || []).find(function (c) {
    const candleDate = kstDateOf(c.timestamp);
    return candleDate != null && candleDate < tradeDate;
  });
  if (!prev) {
    throw new Error('전일 봉 없음 (' + label + ', tradeDate=' + tradeDate + ')');
  }
  return lastPrice - toFiniteNumber(prev.closePrice, label + '.prevClose');
}

async function fetchLiveQuotes() {
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
    const last = toFiniteNumber(p.lastPrice, s.ticker);
    return { ticker: s.ticker, name: s.name, etf: s.etf, price: last, change: computeChange(candlesBySymbol[s.ticker], p.timestamp, last, s.ticker) };
  });
  const indices = QUOTES_FALLBACK.indices.map(function (ix) {
    const p = indexBySymbol.get(ix.code);
    if (!p) {
      throw new Error('지수 응답에 ' + ix.code + ' 없음');
    }
    const last = toFiniteNumber(p.lastPrice, ix.code);
    return { code: ix.code, name: ix.name, value: last, change: round2(computeChange(candlesBySymbol[ix.code], p.timestamp, last, ix.code)) };
  });
  const body = { state: 'OK', data: { indices: indices, stocks: stocks } };
  quotesCache = { at: Date.now(), body: body };
  return body;
}

// resolve 값: { state: 'OK', data } | { state: 'FALLBACK', data: 스냅샷 }
// data 형상은 quotes-fallback.json 과 동일 — 폴백이어도 화면은 같은 코드로 그린다.
async function getQuotes() {
  if (!QUOTES_ENABLED) {
    return { state: 'FALLBACK', data: QUOTES_FALLBACK };
  }
  if (quotesCache.body && Date.now() - quotesCache.at < QUOTES_CACHE_MS) {
    return quotesCache.body;
  }
  // 동시 요청은 진행 중 조회 하나를 공유한다 — 캐시가 비었을 때 관객 수만큼
  // 토큰 발급·일봉 조회가 중복 실행되는 스탬피드 방지.
  if (!quotesInFlight) {
    quotesInFlight = fetchLiveQuotes()
      .catch(function (err) {
        console.warn('[mock-broker] 시세 소스 호출 실패 — 스냅샷 폴백', err.message);
        return { state: 'FALLBACK', data: QUOTES_FALLBACK };
      })
      .finally(function () {
        quotesInFlight = null;
      });
  }
  // 웜 판단은 body 존재가 아니라 일봉이 오늘자로 캐시됐는지다 — 날짜가 바뀌면 전날 body 가
  // 남아 있어도 일봉 재수집(38심볼 × 250ms)이 돌아, body 기준으로 기다리면 응답이 수 초 묶인다.
  if (quotesCache.body && dailyCandleCache.date === kstDateOf(Date.now())) {
    return quotesInFlight; // 웜(일봉 오늘자 캐시) — 현재가 배치 2건뿐이라 그대로 기다린다
  }
  // 일봉 웜업(수 초)에 응답을 묶지 않는다 — 1.5초 내 미완이면 스냅샷 즉답.
  // 웜업은 뒤에서 계속 진행돼 다음 폴링(7초)이 실데이터로 채운다(화면 즉시 렌더 불변식 유지).
  return Promise.race([
    quotesInFlight,
    sleep(1500).then(function () {
      return { state: 'FALLBACK', data: QUOTES_FALLBACK };
    }),
  ]);
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
    const ext = path.extname(filePath);
    // 캐시 정책을 명시한다 — 무헤더 응답은 iOS standalone 웹뷰가 자체 캐시로 물고 재검증하지
    // 않아 배포가 실기기에 전파되지 않는다(ALPHA-736 실증). 배포마다 바뀌는 텍스트 자산은
    // no-cache(재검증 강제 — 데모 트래픽에서 재전송 비용은 무시 가능), 아이콘류 바이너리만 1시간.
    const cacheControl = ext === '.png' || ext === '.ico' || ext === '.woff2'
      ? 'max-age=3600'
      : 'no-cache';
    res.writeHead(200, {
      'Content-Type': CONTENT_TYPES[ext] || 'application/octet-stream',
      'Cache-Control': cacheControl,
    });
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
  if (url.pathname === '/api/broker/chart') {
    if (req.method !== 'GET') {
      res.writeHead(405);
      res.end();
      return;
    }
    const chartTicker = url.searchParams.get('ticker');
    const known = chartTicker && QUOTES_FALLBACK.stocks.some(function (s) {
      return s.ticker === chartTicker;
    });
    if (!known) {
      // 유니버스 밖 티커는 상류에 흘리지 않는다 — 화면은 폴백 문구로 수렴
      sendJson(res, { state: 'FALLBACK', message: CHART_FALLBACK_MESSAGE });
      return;
    }
    // interval 은 화이트리스트로만 상류에 전달한다 (기본 1d — 기존 호출 호환)
    const chartInterval = url.searchParams.get('interval') === '1m' ? '1m' : '1d';
    getChartCandles(chartTicker, chartInterval)
      .catch(function (err) {
        console.warn('[mock-broker] chart 처리 실패', err.message);
        return { state: 'FALLBACK', message: CHART_FALLBACK_MESSAGE };
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
