import type { ApiClient } from './client';
import { mockClient } from './mock';

// 백엔드 연결 시 EXPO_PUBLIC_API_MODE=http 와 ./http 구현을 추가하고 여기서만 택일한다
const mode = process.env.EXPO_PUBLIC_API_MODE ?? 'mock';

export const api: ApiClient = mode === 'mock' ? mockClient : mockClient;
export type { ApiClient } from './client';
export * from './types';
