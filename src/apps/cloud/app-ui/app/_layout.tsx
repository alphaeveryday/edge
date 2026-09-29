import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { isApiError } from '@/api';
import { Toast } from '@/components/Toast';
import { LoginGateSheet } from '@/features/auth/LoginGateSheet';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';
import { fontAssets } from '@/theme/typography';

SplashScreen.preventAutoHideAsync();
// 쓰기 요청 401 시 세션 해제와 로그인 유도 시트
const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (e) => {
      if (!isApiError(e, 'UNAUTHORIZED')) return;
      const s = useSession.getState();
      s.expire();
      s.openGate('로그인');
    },
  }),
});

export default function RootLayout() {
  const [loaded, error] = useFonts(fontAssets);
  const restore = useSession((s) => s.restore);
  const restored = useSession((s) => s.restored);
  useEffect(() => { restore(); }, [restore]);
  useEffect(() => {
    if ((loaded || error) && restored) SplashScreen.hideAsync();
  }, [loaded, error, restored]);
  if (!loaded && !error) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="community/write" options={{ presentation: 'modal' }} />
        <Stack.Screen name="menu" options={{ animation: 'slide_from_right' }} />
        <Stack.Screen name="auth/signup" options={{ presentation: 'modal' }} />
        <Stack.Screen name="auth/reset" options={{ presentation: 'modal' }} />
      </Stack>
      <LoginGateSheet />
      <Toast />
    </QueryClientProvider>
  );
}
