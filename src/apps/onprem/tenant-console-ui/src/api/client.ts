/**
 * 공통 fetch 래퍼. baseURL · 인증 헤더 · 에러 정규화를 한 곳에서 담당한다.
 * 모든 도메인의 repository.real.ts 는 이 client 만 사용한다.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

/** 정규화된 API 에러. mock·real 모두 동일한 에러 모양을 갖도록 강제한다. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function authHeaders(): Record<string, string> {
  // TODO(session 도메인): 토큰 저장/갱신은 추후 session 도메인으로 이동.
  const token =
    typeof localStorage !== 'undefined' ? localStorage.getItem('edge.token') : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** JSON 직렬화할 본문. 문자열이 아니면 JSON.stringify 된다. */
  body?: unknown;
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = opts;

  const res = await fetch(`${BASE_URL}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    let errBody: unknown;
    try {
      errBody = await res.json();
    } catch {
      /* 본문이 JSON 이 아닐 수 있다 */
    }
    throw new ApiError(res.status, `요청 실패 (${res.status} ${res.statusText})`, errBody);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const apiClient = {
  get: <T>(path: string, opts?: RequestOptions) => apiFetch<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'PATCH', body }),
  delete: <T>(path: string, opts?: RequestOptions) =>
    apiFetch<T>(path, { ...opts, method: 'DELETE' }),
};
