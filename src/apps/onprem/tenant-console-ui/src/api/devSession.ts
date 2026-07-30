/**
 * dev 세션 부트스트랩 — vite dev 전용 자동 세션 경로.
 * 콘솔 API 는 fail-closed(전 표면 세션 필수)라, 세션이 없으면 데모 부트스트랩
 * 계정(tenant-console-api application.yaml 기본값과 동일한 결)으로 자동 로그인해
 * 세션 쿠키를 확보한다 — 로컬 개발이 로그인 절차 없이 바로 콘솔에 진입하게.
 * main.tsx 가 dev 일 때만 동적 import 하므로, 서빙 빌드(데모 박스 포함)에는 이
 * 파일(자격증명 포함)이 실리지 않는다 — 유일한 세션 진입은 로그인 화면(ALPHA-626)이다
 * (데모 박스 autosession 빌드는 콘솔 CloudFront 공개와 함께 폐기 — ALPHA-627).
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
