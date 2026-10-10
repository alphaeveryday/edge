import { useEffect, useState } from 'react';
import * as AppleAuthentication from 'expo-apple-authentication';
import { CryptoDigestAlgorithm, digestStringAsync, randomUUID } from 'expo-crypto';

// 애플은 nonce 해시를 받고 서버는 원문으로 대조
// 사용자 취소는 null
export async function appleLogin(): Promise<{ idToken: string; nonce: string; authorizationCode?: string } | null> {
  const nonce = randomUUID();
  try {
    const c = await AppleAuthentication.signInAsync({
      requestedScopes: [AppleAuthentication.AppleAuthenticationScope.EMAIL],
      nonce: await digestStringAsync(CryptoDigestAlgorithm.SHA256, nonce),
    });
    if (!c.identityToken) throw new Error('애플 로그인 토큰이 없어요');
    return { idToken: c.identityToken, nonce, authorizationCode: c.authorizationCode ?? undefined };
  } catch (e) {
    if ((e as { code?: string }).code === 'ERR_REQUEST_CANCELED') return null;
    throw e;
  }
}

// iOS 13 이상 한정 노출
export const useAppleAvailable = () => {
  const [ok, setOk] = useState(false);
  useEffect(() => { AppleAuthentication.isAvailableAsync().then(setOk, () => setOk(false)); }, []);
  return ok;
};
