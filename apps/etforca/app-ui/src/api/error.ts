export type ApiErrorCode = 'NOT_READY' | 'NOT_FOUND' | 'NETWORK' | 'UNAUTHORIZED' | 'INVALID';

// 화면 분기용 실패 원인 코드
export class ApiError extends Error {
  constructor(public code: ApiErrorCode, message?: string) {
    super(message ?? code);
    this.name = 'ApiError';
  }
}

export const isApiError = (e: unknown, code?: ApiErrorCode): e is ApiError => e instanceof ApiError && (!code || e.code === code);
