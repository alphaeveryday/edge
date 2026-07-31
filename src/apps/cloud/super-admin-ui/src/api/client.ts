/**
 * 공통 fetch 래퍼. baseURL · 에러 정규화를 한 곳에서 담당한다.
 * 모든 도메인의 repository.real.ts 는 이 client 만 사용한다.
 * 인증은 세션 쿠키(same-origin 자동 전송, vite 프록시 경유) — 헤더 토큰 경로는 없다.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

/** 정규화된 API 에러. 모든 도메인이 동일한 에러 모양을 갖도록 강제한다. */
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

/**
 * 실패 봉투의 서버 사유(body.message)를 우선 반환한다 — 없으면 fallback.
 * 서버는 실패에도 공통 포맷({isSuccess:false, code, message})을 실어 보내므로,
 * HTTP 상태 문구("요청 실패 (409 Conflict)")보다 이 message 가 사용자 언어다.
 */
export function apiMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const msg = (err.body as { message?: string } | undefined)?.message;
    if (msg) return msg;
  }
  return fallback;
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
  const json = await res.json();
  // super-admin-api 는 성공 응답도 공통 포맷(ApiResponse)로 감싼다 — result 만 벗겨 반환한다.
  // (뮤테이션의 onSuccess(null) 은 result 가 생략돼 undefined → void 콜러 무영향)
  if (json && typeof json === 'object' && 'isSuccess' in json) {
    return (json as { result: T }).result;
  }
  return json as T;
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
