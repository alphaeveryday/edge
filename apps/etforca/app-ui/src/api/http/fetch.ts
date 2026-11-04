import { ApiError, type ApiErrorCode } from '../error';
import { deviceId, tokens } from './storage';

export const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8080/api/v1';

interface Envelope<T> { isSuccess: boolean; code: string; message: string; result?: T }
type Query = Record<string, string | number | undefined>;

// 서버 코드의 앱 오류 코드 명시 매핑
const CODE_MAP: Record<string, ApiErrorCode> = {
  COMMON401: 'UNAUTHORIZED',
  AUTH4001: 'UNAUTHORIZED',
  ANALYSIS4001: 'NOT_READY',
  ETF4001: 'NOT_FOUND',
  POST4001: 'NOT_FOUND',
  MEMBER4005: 'NOT_FOUND',
};
const toCode = (code: string): ApiErrorCode => CODE_MAP[code] ?? 'INVALID';

const qs = (q?: Query) => {
  if (!q) return '';
  const p = Object.entries(q).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return p.length ? '?' + p.join('&') : '';
};

// 서버 거절만 토큰 폐기, 네트워크·서버 장애는 토큰 유지
type Refresh = 'ok' | 'rejected' | 'offline';
let refreshing: { version: number; promise: Promise<Refresh> } | null = null;
// 같은 세션의 동시 요청만 리프레시 1회를 공유한다.
const tryRefresh = (version: number) => {
  if (refreshing?.version === version) return refreshing.promise;
  const promise = (async (): Promise<Refresh> => {
    const refresh = await tokens.refresh();
    tokens.assertCurrent(version);
    if (!refresh) return 'rejected';
    try {
      const r = await fetch(`${BASE_URL}/auth/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refreshToken: refresh }) });
      tokens.assertCurrent(version);
      if (r.status >= 500 || r.status === 429) return 'offline';
      const body = (await r.json()) as Envelope<{ accessToken: string; refreshToken: string }>;
      tokens.assertCurrent(version);
      if (!body.isSuccess || !body.result) {
        return !body.isSuccess && ['COMMON400', 'COMMON401', 'AUTH4001'].includes(body.code) ? 'rejected' : 'offline';
      }
      await tokens.save(body.result.accessToken, body.result.refreshToken, version);
      return 'ok';
    } catch {
      tokens.assertCurrent(version);
      return 'offline';
    }
  })();
  refreshing = { version, promise };
  void promise.finally(() => { if (refreshing?.promise === promise) refreshing = null; }).catch(() => undefined);
  return promise;
};

// 로그인과 가입은 게스트 데이터 매핑용 디바이스 헤더만 전송
// both 는 회원 토큰이 있어도 기기 헤더를 함께 보내는 기기 단위 요청
export async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; auth?: boolean | 'device' | 'both'; retry?: boolean } = {}): Promise<T> {
  const version = tokens.version();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.auth === 'device') headers['X-Device-Id'] = await deviceId();
  else if (opts.auth !== false) {
    const access = await tokens.access();
    if (access) headers.Authorization = `Bearer ${access}`;
    if (!access || opts.auth === 'both') headers['X-Device-Id'] = await deviceId();
  }
  tokens.assertCurrent(version);
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}${qs(opts.query)}`, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  } catch (e) {
    tokens.assertCurrent(version);
    throw new ApiError('NETWORK', e instanceof Error ? e.message : 'network');
  }
  tokens.assertCurrent(version);
  if (res.status >= 500) throw new ApiError('NETWORK', `server error ${res.status}`);
  let body: Envelope<T>;
  try {
    body = (await res.json()) as Envelope<T>;
  } catch {
    tokens.assertCurrent(version);
    throw new ApiError('NETWORK', `bad response ${res.status}`);
  }
  tokens.assertCurrent(version);
  if (body.isSuccess) return body.result as T;
  if (body.code === 'COMMON401' && headers.Authorization && opts.retry !== false) {
    // 이미 다른 요청이 갱신한 토큰을 다시 회전시키지 않는다.
    const access = await tokens.access();
    tokens.assertCurrent(version);
    const r = headers.Authorization !== `Bearer ${access}` ? 'ok' : await tryRefresh(version);
    tokens.assertCurrent(version);
    if (r === 'ok') return request<T>(method, path, { ...opts, retry: false });
    if (r === 'offline') throw new ApiError('NETWORK', 'refresh failed');
  }
  if (body.code === 'COMMON401' && headers.Authorization) await tokens.clear('expired', version);
  throw new ApiError(toCode(body.code), body.message);
}
