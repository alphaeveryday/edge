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
let refreshing: Promise<Refresh> | null = null;
// 액세스 만료 시 동시 요청이 공유하는 리프레시 1회
const tryRefresh = () => {
  refreshing ??= (async (): Promise<Refresh> => {
    const refresh = await tokens.refresh();
    if (!refresh) return 'rejected';
    try {
      const r = await fetch(`${BASE_URL}/auth/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refreshToken: refresh }) });
      if (r.status >= 500) return 'offline';
      const body = (await r.json()) as Envelope<{ accessToken: string; refreshToken: string }>;
      if (!body.isSuccess || !body.result) return 'rejected';
      await tokens.save(body.result.accessToken, body.result.refreshToken);
      return 'ok';
    } catch {
      return 'offline';
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
};

// 로그인과 가입은 게스트 데이터 매핑용 디바이스 헤더만 전송
export async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown; auth?: boolean | 'device'; retry?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.auth === 'device') headers['X-Device-Id'] = await deviceId();
  else if (opts.auth !== false) {
    const access = await tokens.access();
    if (access) headers.Authorization = `Bearer ${access}`;
    else headers['X-Device-Id'] = await deviceId();
  }
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}${qs(opts.query)}`, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  } catch (e) {
    throw new ApiError('NETWORK', e instanceof Error ? e.message : 'network');
  }
  let body: Envelope<T>;
  try {
    body = (await res.json()) as Envelope<T>;
  } catch {
    throw new ApiError('NETWORK', `bad response ${res.status}`);
  }
  if (body.isSuccess) return body.result as T;
  if (body.code === 'COMMON401' && opts.retry !== false && (await tokens.refresh())) {
    const r = await tryRefresh();
    if (r === 'ok') return request<T>(method, path, { ...opts, retry: false });
    if (r === 'offline') throw new ApiError('NETWORK', 'refresh failed');
  }
  if (body.code === 'COMMON401') await tokens.clear();
  throw new ApiError(toCode(body.code), body.message);
}
