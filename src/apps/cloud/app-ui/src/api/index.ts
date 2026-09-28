import type { ApiClient } from './client';
import { httpClient } from './http';
import { mockClient } from './mock';

// EXPO_PUBLIC_API_MODE=http 면 실서버(EXPO_PUBLIC_API_URL), 아니면 mock
const mode = process.env.EXPO_PUBLIC_API_MODE ?? 'mock';

export const api: ApiClient = mode === 'http' ? httpClient : mockClient;
export type { ApiClient } from './client';
export * from './types';
export { ApiError, isApiError } from './error';
