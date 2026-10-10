import { GoogleSignin, isSuccessResponse } from '@react-native-google-signin/google-signin';
import { Platform } from 'react-native';

// 웹 클라이언트 ID 가 서버 aud, iOS 클라이언트 ID 는 iOS SDK 용
// 둘 다 EAS 환경 변수, 없으면 버튼 숨김
const WEB = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
const IOS = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;

export const googleAvailable = Platform.OS === 'web' || (!!WEB && (Platform.OS !== 'ios' || !!IOS));

let configured = false;

// 무료 API 라 nonce 없음, 서버도 대조 생략
// 다음 로그인의 계정 선택을 위해 받은 즉시 구글 세션 종료
// 사용자 취소는 null
export async function googleLogin(): Promise<{ idToken: string } | null> {
  // 웹 미리보기는 SDK 없이 mock 서버 확인용 값
  if (Platform.OS === 'web') return { idToken: 'web-preview' };
  if (!configured) {
    GoogleSignin.configure({ webClientId: WEB, iosClientId: IOS });
    configured = true;
  }
  await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
  const r = await GoogleSignin.signIn();
  if (!isSuccessResponse(r)) return null;
  await GoogleSignin.signOut().catch(() => {});
  if (!r.data.idToken) throw new Error('구글 로그인 토큰이 없어요');
  return { idToken: r.data.idToken };
}
