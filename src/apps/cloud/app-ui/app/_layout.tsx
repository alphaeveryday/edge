import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { router, Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { isApiError } from '@/api';
import { Toast } from '@/components/Toast';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';
import { fontAssets } from '@/theme/typography';

SplashScreen.preventAutoHideAsync();
// 시작 화면 최소 노출 1초
const splashMin = new Promise((r) => setTimeout(r, 1000));
// 로그인 중 쓰기 요청 401 시 세션 해제와 로그인 화면 이동. 로그인 실패 401 은 폼이 처리
const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (e) => {
      const s = useSession.getState();
      if (!isApiError(e, 'UNAUTHORIZED') || !s.loggedIn) return;
      s.expire();
      router.push({ pathname: '/login', params: { reason: '만료' } });
    },
  }),
});

export default function RootLayout() {
  const [loaded, error] = useFonts(fontAssets);
  const restore = useSession((s) => s.restore);
  const restored = useSession((s) => s.restored);
  useEffect(() => { restore(); }, [restore]);
  useEffect(() => {
    if ((loaded || error) && restored) splashMin.then(() => SplashScreen.hideAsync());
  }, [loaded, error, restored]);
  if (!loaded && !error) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.white } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="community/write" options={{ presentation: 'fullScreenModal' }} />
        <Stack.Screen name="menu" options={{ animation: 'slide_from_right' }} />
        <Stack.Screen name="login" options={{ presentation: 'fullScreenModal' }} />
        <Stack.Screen name="auth/signup" options={{ presentation: 'fullScreenModal' }} />
        <Stack.Screen name="auth/reset" options={{ presentation: 'fullScreenModal' }} />
      </Stack>
      <Toast />
    </QueryClientProvider>
  );
}
