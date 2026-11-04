import type { ConfigContext, ExpoConfig } from 'expo/config';

// 저장소에 두지 않는 공개 키의 EAS 환경 변수 주입
// EAS production 빌드는 값이 없으면 실패
// GOOGLE_SERVICES_JSON·GOOGLE_SERVICE_INFO_PLIST 는 EAS 파일 변수의 경로, Android FCM 푸시와 Firebase 애널리틱스용
// META_APP_ID·META_CLIENT_TOKEN 은 Meta 설치 광고의 앱 이벤트용
const KAKAO = '@react-native-seoul/kakao-login';
const META = 'react-native-fbsdk-next';
const REQUIRED = ['KAKAO_NATIVE_APP_KEY', 'EXPO_PUBLIC_POSTHOG_KEY', 'GOOGLE_SERVICES_JSON', 'GOOGLE_SERVICE_INFO_PLIST', 'META_APP_ID', 'META_CLIENT_TOKEN'];

export default ({ config }: ConfigContext): ExpoConfig => {
  const kakaoAppKey = process.env.KAKAO_NATIVE_APP_KEY ?? '';
  const appID = process.env.META_APP_ID ?? '';
  const clientToken = process.env.META_CLIENT_TOKEN ?? '';
  const missing = REQUIRED.filter((k) => !process.env[k]);
  if (process.env.EAS_BUILD && process.env.EAS_BUILD_PROFILE === 'production' && missing.length) {
    throw new Error(`EAS production 환경 변수 누락: ${missing.join(', ')}`);
  }
  return {
    ...config,
    ios: { ...config.ios, googleServicesFile: process.env.GOOGLE_SERVICE_INFO_PLIST },
    android: { ...config.android, googleServicesFile: process.env.GOOGLE_SERVICES_JSON },
    // Meta 플러그인은 appID 가 비면 설정 로드부터 실패하므로 값 없는 로컬에서는 제외
    plugins: config.plugins
      ?.filter((p) => appID || !(Array.isArray(p) && p[0] === META))
      .map((p) => {
        if (!Array.isArray(p)) return p;
        if (p[0] === KAKAO) return [KAKAO, { ...p[1], kakaoAppKey }];
        if (p[0] === META) return [META, { ...p[1], appID, clientToken, scheme: `fb${appID}` }];
        return p;
      }),
  } as ExpoConfig;
};
