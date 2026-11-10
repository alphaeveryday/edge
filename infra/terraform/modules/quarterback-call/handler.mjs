// 쿼터백 통화 데모 API(ALPHA-1487) — quarterback.etforca.com/api/* 를 CloudFront 가 이 함수 URL 로 보낸다.
// 키는 Secrets Manager 에만 있고 브라우저로 내려가지 않는다. 하는 일은 두 가지뿐이다.
//   POST /api/call/token   — OpenAI 실시간 전사(gpt-live-transcribe) 임시 토큰(60초) 발급
//   POST /api/call/suggest — 고객 발화 + 종목 리포트로 상담사가 할 말을 DeepSeek 로 만들어 글자 단위로 흘려보낸다
// 응답 스트리밍 모드(RESPONSE_STREAM)로 돈다 — 두 경로 모두 같은 스트림 핸들러를 쓴다.
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS ?? '').split(',').filter(Boolean);
const TRANSCRIBE_MODEL = 'gpt-live-transcribe';
const SUGGEST_MODEL = 'deepseek-flash';
const MAX_KEYWORDS = 80;
const MAX_KEYWORD_LEN = 40;
const MAX_TRANSCRIPT = 1500;
const MAX_REPORT = 3000;

export const SYSTEM_PROMPT = [
  '너는 자산운용사 상담사(QFA)의 통화 보조다. 상담사는 지금 고객과 전화 중이다.',
  '고객이 방금 한 말과 해당 ETF 리포트를 보고, 상담사가 고객에게 그대로 읽어 줄 답변을 2~3문장, 150자 안쪽으로 써라.',
  '말투는 해요체(~했어요, ~이에요)로만 쓴다. ~습니다 체는 쓰지 않는다.',
  '고객 이름·호칭은 쓰지 않는다 — 통화 전사가 이름을 잘못 받아 적을 수 있다.',
  '리포트에 있는 사실만 쓰고 숫자는 리포트 그대로 쓴다. 리포트에 없는 내용은 지어내지 않는다.',
  '매수·매도 권유, 수익 보장, 앞으로의 가격 단정은 하지 않는다.',
  '머리말·따옴표·목록·마크다운 없이 답변 문장만 쓴다.',
].join('\n');

// 시크릿은 실행 환경당 한 번 읽어 둔다. SDK 는 람다 런타임 내장이라 처음 쓸 때 불러온다(테스트는 캐시를 채워 우회).
export const secrets = new Map();
let sm;
async function secret(id, key) {
  if (!secrets.has(id)) {
    const { GetSecretValueCommand, SecretsManagerClient } = await import('@aws-sdk/client-secrets-manager');
    sm ??= new SecretsManagerClient({});
    const out = await sm.send(new GetSecretValueCommand({ SecretId: id }));
    secrets.set(id, JSON.parse(out.SecretString));
  }
  const value = secrets.get(id)[key];
  if (!value) throw new Error(`${id} 에 ${key} 가 없다`);
  return value;
}

/** 입력 검사 — 실패하면 사유 문자열, 통과하면 정리된 값. */
export function parseTokenRequest(body) {
  const kw = body?.keywords ?? [];
  if (!Array.isArray(kw) || kw.length > MAX_KEYWORDS) return 'keywords 는 최대 80개 배열이어야 합니다';
  if (!kw.every((k) => typeof k === 'string' && k.length > 0 && k.length <= MAX_KEYWORD_LEN)) return 'keywords 항목은 1~40자 문자열이어야 합니다';
  return { keywords: kw };
}

export function parseSuggestRequest(body) {
  const { transcript, report } = body ?? {};
  if (typeof transcript !== 'string' || !transcript.trim()) return 'transcript 가 필요합니다';
  if (typeof report !== 'string' || !report.trim()) return 'report 가 필요합니다';
  return { transcript: transcript.slice(-MAX_TRANSCRIPT), report: report.slice(0, MAX_REPORT) };
}

export function suggestMessages({ transcript, report }) {
  return [
    { role: 'system', content: SYSTEM_PROMPT },
    { role: 'user', content: `[통화 내용 — 최근 발화가 끝에 있다]\n${transcript}\n\n[ETF 리포트]\n${report}` },
  ];
}

function send(stream, statusCode, body) {
  const out = awslambda.HttpResponseStream.from(stream, {
    statusCode,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
  out.write(JSON.stringify(body));
  out.end();
}

async function mintToken(stream, { keywords }) {
  const key = await secret(process.env.OPENAI_SECRET_ID, 'OPENAI_API_KEY');
  const res = await fetch('https://api.openai.com/v1/realtime/client_secrets', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expires_after: { anchor: 'created_at', seconds: 60 },
      session: {
        type: 'transcription',
        audio: {
          input: {
            // live 전사 모델은 음성 구간 감지(VAD)를 지원하지 않는다 — 말하는 도중 조각(delta)으로 온다.
            transcription: { model: TRANSCRIBE_MODEL, languages: ['ko'], keywords },
            turn_detection: null,
          },
        },
      },
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.value) {
    console.error('openai client_secrets 실패', res.status, JSON.stringify(data).slice(0, 500));
    return send(stream, 502, { error: '전사 토큰 발급에 실패했습니다' });
  }
  return send(stream, 200, { value: data.value, expires_at: data.expires_at });
}

async function suggest(stream, req) {
  const key = await secret(process.env.DEEPSEEK_SECRET_ID, 'DEEPSEEK_API_KEY');
  const res = await fetch('https://api.deepseek.com/chat/completions', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: SUGGEST_MODEL, reasoning_effort: 'low', stream: true, max_tokens: 400, messages: suggestMessages(req) }),
  });
  if (!res.ok || !res.body) {
    console.error('deepseek 실패', res.status, (await res.text().catch(() => '')).slice(0, 500));
    return send(stream, 502, { error: '답변 생성에 실패했습니다' });
  }
  const out = awslambda.HttpResponseStream.from(stream, {
    statusCode: 200,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
  });
  // SSE(data: {...}) 줄에서 답변 글자(content)만 골라 그대로 흘려보낸다. 추론(reasoning_content)은 버린다.
  // 머리(200)를 이미 보냈으므로 도중 실패는 여기서 끝낸다 — 화면은 받은 데까지 보여 준다.
  const decoder = new TextDecoder();
  let buf = '';
  try {
    for await (const chunk of res.body) {
      buf += decoder.decode(chunk, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const text of lines.map(sseContent)) if (text) out.write(text);
    }
    const rest = sseContent(buf);
    if (rest) out.write(rest);
  } catch (err) {
    console.error('deepseek 스트림 중단', err);
  } finally {
    out.end();
  }
}

export function sseContent(line) {
  const s = line.trim();
  if (!s.startsWith('data:')) return '';
  const payload = s.slice(5).trim();
  if (payload === '[DONE]') return '';
  try {
    return JSON.parse(payload).choices?.[0]?.delta?.content ?? '';
  } catch {
    return '';
  }
}

const ROUTES = { '/api/call/token': [parseTokenRequest, mintToken], '/api/call/suggest': [parseSuggestRequest, suggest] };

export async function route(event, stream) {
  const method = event.requestContext?.http?.method;
  const entry = ROUTES[event.rawPath];
  if (!entry) return send(stream, 404, { error: 'not found' });
  if (method !== 'POST') return send(stream, 405, { error: 'POST only' });
  // 같은 사이트의 화면에서 온 요청만 받는다(브라우저는 POST 에 Origin 을 붙인다).
  if (!ALLOWED_ORIGINS.includes(event.headers?.origin ?? '')) return send(stream, 403, { error: 'origin not allowed' });
  let body;
  try {
    body = JSON.parse(event.isBase64Encoded ? Buffer.from(event.body ?? '', 'base64').toString() : event.body ?? '');
  } catch {
    return send(stream, 400, { error: 'JSON 본문이 필요합니다' });
  }
  const [parse, run] = entry;
  const req = parse(body);
  if (typeof req === 'string') return send(stream, 400, { error: req });
  try {
    return await run(stream, req);
  } catch (err) {
    console.error(event.rawPath, '실패', err);
    return send(stream, 500, { error: '처리 중 오류가 났습니다' });
  }
}

// awslambda 는 람다 실행 환경이 주는 전역이다. 테스트는 route 만 부른다.
export const handler = globalThis.awslambda ? awslambda.streamifyResponse(route) : undefined;
