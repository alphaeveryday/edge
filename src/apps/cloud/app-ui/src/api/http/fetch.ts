import { ApiError, type ApiErrorCode } from '../error';
import { deviceId, tokens } from './storage';

export const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8080/api/v1';

interface Envelope<T> { isSuccess: boolean; code: string; message: string; result?: T }
type Query = Record<string, string | number | undefined>;

// 서버 code → 앱 ApiErrorCode. 앱은 화면 분기에 이 넷만 쓴다
const toCode = (code: string): ApiErrorCode => {
  if (code === 'COMMON401' || code === 'AUTH4010') return 'UNAUTHORIZED';
  if (code === 'ANALYSIS4041') return 'NOT_READY';
  if (/4040$/.test(code)) return 'NOT_FOUND';
  return 'INVALID';
};

const qs = (q?: Query) => {
  if (!q) return '';
  const p = Object.entries(q).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return p.length ? '?' + p.join('&') : '';
};

let refreshing: Promise<boolean> | null = null;
// 액세스 만료 시 리프레시 1회. 동시 요청은 한 번의 갱신을 공유한다
const tryRefresh = () => {
  refreshing ??= (async () => {
    const refresh = await tokens.refresh();
    if (!refresh) return false;
    try {
      const r = await fetch(`${BASE_URL}/auth/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refreshToken: refresh }) });
      const body = (await r.json()) as Envelope<{ accessToken: string; refreshToken: string }>;
      if (!body.isSuccess || !body.result) return false;
      await tokens.save(body.result.accessToken, body.result.refreshToken);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
};

// auth: 기본은 Bearer(없으면 X-Device-Id), 'device' 는 X-Device-Id 만(로그인·가입, 서버가 게스트 데이터를 계정에 매핑), false 는 헤더 없음
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
  if (body.code === 'COMMON401' && opts.retry !== false && (await tokens.refresh()) && (await tryRefresh())) {
    return request<T>(method, path, { ...opts, retry: false });
  }
  if (body.code === 'COMMON401') await tokens.clear();
  throw new ApiError(toCode(body.code), body.message);
}
