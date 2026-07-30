/**
 * dev 세션 부트스트랩 — 로그인 화면(ALPHA-626)과 공존하는 자동 세션 경로.
 * 콘솔 API 는 fail-closed(전 표면 세션 필수)라, 세션이 없으면 데모 부트스트랩
 * 계정(tenant-console-api application.yaml 기본값과 동일한 결)으로 자동 로그인해
 * 세션 쿠키를 확보한다 — 로컬 개발·데모 박스가 로그인 절차 없이 바로 콘솔에 진입하게.
 * main.tsx 가 dev 이거나 데모 박스 빌드(VITE_DEMO_AUTOSESSION='true')일 때만 동적
 * import 하므로, 실 온프렘 빌드(플래그 없음)에는 이 파일(자격증명 포함)이 실리지
 * 않는다 — 그 빌드의 유일한 세션 진입은 로그인 화면이다.
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
