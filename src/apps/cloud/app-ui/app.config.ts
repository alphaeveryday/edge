import type { ConfigContext, ExpoConfig } from 'expo/config';

// 저장소에 두지 않는 공개 키의 EAS 환경 변수 주입
// EAS production 빌드는 값이 없으면 실패
// GOOGLE_SERVICES_JSON 은 EAS 파일 변수의 경로, Android FCM 푸시용
const KAKAO = '@react-native-seoul/kakao-login';
const REQUIRED = ['KAKAO_NATIVE_APP_KEY', 'EXPO_PUBLIC_POSTHOG_KEY', 'GOOGLE_SERVICES_JSON'];

export default ({ config }: ConfigContext): ExpoConfig => {
  const kakaoAppKey = process.env.KAKAO_NATIVE_APP_KEY ?? '';
  const missing = REQUIRED.filter((k) => !process.env[k]);
  if (process.env.EAS_BUILD && process.env.EAS_BUILD_PROFILE === 'production' && missing.length) {
    throw new Error(`EAS production 환경 변수 누락: ${missing.join(', ')}`);
  }
  return {
    ...config,
    android: { ...config.android, googleServicesFile: process.env.GOOGLE_SERVICES_JSON },
    plugins: config.plugins?.map((p) => (Array.isArray(p) && p[0] === KAKAO ? [KAKAO, { ...p[1], kakaoAppKey }] : p)),
  } as ExpoConfig;
};
