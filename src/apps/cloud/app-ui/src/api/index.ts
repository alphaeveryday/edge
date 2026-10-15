import type { ApiClient } from './client';
import { httpClient } from './http';
import { mockClient } from './mock';

// 환경 변수로 고르는 실서버와 mock 구현
const mode = process.env.EXPO_PUBLIC_API_MODE ?? 'mock';

export const api: ApiClient = mode === 'http' ? httpClient : mockClient;
export type { ApiClient } from './client';
export * from './types';
export { ApiError, isApiError } from './error';
