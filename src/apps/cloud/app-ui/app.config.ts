import type { ConfigContext, ExpoConfig } from 'expo/config';

// 저장소에 두지 않는 공개 키의 EAS 환경 변수 주입
// EAS production 빌드는 값이 없으면 실패
const KAKAO = '@react-native-seoul/kakao-login';
const GOOGLE = '@react-native-google-signin/google-signin';
const REQUIRED = ['KAKAO_NATIVE_APP_KEY', 'EXPO_PUBLIC_POSTHOG_KEY', 'EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID', 'EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID'];

export default ({ config }: ConfigContext): ExpoConfig => {
  const kakaoAppKey = process.env.KAKAO_NATIVE_APP_KEY ?? '';
  const missing = REQUIRED.filter((k) => !process.env[k]);
  if (process.env.EAS_BUILD && process.env.EAS_BUILD_PROFILE === 'production' && missing.length) {
    throw new Error(`EAS production 환경 변수 누락: ${missing.join(', ')}`);
  }
  // 구글 iOS URL scheme 은 iOS 클라이언트 ID 의 역순 표기
  // 플러그인이 scheme 없으면 prebuild 를 멈춰 ID 있을 때만 추가
  const googleIos = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
  const google = googleIos
    ? [[GOOGLE, { iosUrlScheme: `com.googleusercontent.apps.${googleIos.replace('.apps.googleusercontent.com', '')}` }]]
    : [];
  return {
    ...config,
    plugins: [
      ...(config.plugins ?? []).map((p) => (Array.isArray(p) && p[0] === KAKAO ? [KAKAO, { ...p[1], kakaoAppKey }] : p)),
      ...google,
    ],
  } as ExpoConfig;
};
