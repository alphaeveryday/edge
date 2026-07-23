/**
 * dev 세션 부트스트랩 — 로그인 화면(ALPHA-486 범위 밖)이 생기기 전까지의 임시 경로.
 * 콘솔 API 는 fail-closed(전 표면 세션 필수)라, 세션이 없으면 데모 부트스트랩
 * 계정(tenant-console-api application.yaml 기본값과 동일한 결)으로 자동 로그인해
 * 세션 쿠키를 확보한다. 자격증명은 로컬·데모 스택 전용 — 로그인 화면 도입 시 이
 * 파일을 제거한다.
 */
import { apiClient, ApiError } from './client';

const EMAIL = import.meta.env.VITE_DEV_LOGIN_EMAIL ?? 'admin@demo.edge.local';
const PASSWORD = import.meta.env.VITE_DEV_LOGIN_PASSWORD ?? 'demo-admin-1';

/** 세션이 있으면 그대로 쓰고, 없으면(401) 데모 계정으로 로그인한다. */
export async function ensureDevSession(): Promise<void> {
  try {
    await apiClient.get('/auth/session');
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      await apiClient.post('/auth/login', { email: EMAIL, password: PASSWORD });
      return;
    }
    throw error;
  }
}
