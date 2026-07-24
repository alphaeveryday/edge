// 증권사 자체 제작 API(mock-broker) 데모 서버 — 실제 배치에서는 증권사 Backend/API Gateway 자리다.
// 계약 원칙(docs/contracts/publication-api.md): MTS 화면은 Publication API를 직접 호출하지 않는다.
// 화면은 이 서버의 /api/broker/* 만 호출하고, Publication API 호출·고객 해시·채널 부착·폴백 처리는
// 전부 이 레이어 책임이다(ADR-0035 런타임 경로: 위젯 UI → 증권사 백엔드 → On-Prem Publication API).
// 의존성 0 — node:20 내장 http/fetch 만 사용.
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.PORT || 8080);
const PUBLICATION_API_URL = process.env.PUBLICATION_API_URL || 'http://localhost:18084';
const STATIC_DIR = path.resolve(__dirname, process.env.STATIC_DIR || '../mts-ai-tab');

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
});
