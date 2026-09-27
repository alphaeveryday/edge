export type ApiErrorCode = 'NOT_READY' | 'NOT_FOUND' | 'NETWORK' | 'UNAUTHORIZED' | 'INVALID';

// 화면이 상태를 갈라 보여줄 수 있도록 실패 원인을 코드로 싣는다
export class ApiError extends Error {
  constructor(public code: ApiErrorCode, message?: string) {
    super(message ?? code);
    this.name = 'ApiError';
  }
}

export const isApiError = (e: unknown, code?: ApiErrorCode): e is ApiError => e instanceof ApiError && (!code || e.code === code);
