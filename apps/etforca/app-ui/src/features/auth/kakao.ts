import { login } from '@react-native-seoul/kakao-login';
import { randomUUID } from 'expo-crypto';
import { Platform } from 'react-native';

// 카카오톡 앱 우선의 카카오 로그인
// 서버 대조용 로그인마다 새 nonce
// 사용자 취소는 null
export async function kakaoLogin(): Promise<{ idToken: string; nonce: string } | null> {
  const nonce = randomUUID();
  // 웹 미리보기는 SDK 없이 mock 서버 확인용 값
  if (Platform.OS === 'web') return { idToken: 'web-preview', nonce };
  try {
    const t = await login({ nonce });
    return { idToken: t.idToken, nonce };
  } catch (e) {
    if (e instanceof Error && /cancel/i.test(e.message)) return null;
    throw e;
  }
}
