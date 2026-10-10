import type { ConfigContext, ExpoConfig } from 'expo/config';

// 저장소에 두지 않는 공개 키의 EAS 환경 변수 주입
// EAS production 빌드는 값이 없으면 실패
const KAKAO = '@react-native-seoul/kakao-login';

export default ({ config }: ConfigContext): ExpoConfig => {
  const kakaoAppKey = process.env.KAKAO_NATIVE_APP_KEY ?? '';
  if (process.env.EAS_BUILD && process.env.EAS_BUILD_PROFILE === 'production'
    && (!kakaoAppKey || !process.env.EXPO_PUBLIC_POSTHOG_KEY)) {
    throw new Error('EAS production 환경 변수에 KAKAO_NATIVE_APP_KEY 와 EXPO_PUBLIC_POSTHOG_KEY 필요');
  }
  return {
    ...config,
    plugins: config.plugins?.map((p) => (Array.isArray(p) && p[0] === KAKAO ? [KAKAO, { ...p[1], kakaoAppKey }] : p)),
  } as ExpoConfig;
};
