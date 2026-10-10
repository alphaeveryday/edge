import { focusManager, MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { DarkTheme, DefaultTheme, router, Stack, ThemeProvider, usePathname } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import * as SystemUI from 'expo-system-ui';
import { useEffect } from 'react';
import { AppState } from 'react-native';
import { isApiError } from '@/api';
import { Toast } from '@/components/Toast';
import { trackScreen } from '@/lib/analytics';
import { useSession } from '@/store/session';
import { useColors, useScheme, useThemePref } from '@/theme/theme';
import { fontAssets } from '@/theme/typography';

SplashScreen.preventAutoHideAsync();
// 앱 복귀 시 띄워 둔 화면의 조회 다시 받기
focusManager.setEventListener((onFocus) => {
  const sub = AppState.addEventListener('change', (s) => onFocus(s === 'active'));
  return () => sub.remove();
});
// 시작 화면 최소 노출 1초
const splashMin = new Promise((r) => setTimeout(r, 1000));
// 로그인 중 쓰기 요청 401 시 세션 해제와 로그인 화면 이동
// 로그인 실패 401 은 폼 소관
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
  const colors = useColors();
  const [loaded, error] = useFonts(fontAssets);
  const restore = useSession((s) => s.restore);
  const restored = useSession((s) => s.restored);
  const restoreTheme = useThemePref((s) => s.restore);
  const themeRestored = useThemePref((s) => s.restored);
  const scheme = useScheme();
  const pathname = usePathname();
  useEffect(() => { trackScreen(pathname); }, [pathname]);
  useEffect(() => { restore(); restoreTheme(); }, [restore, restoreTheme]);
  // 화면 전환·키보드 뒤로 드러나는 맨 뒤 창의 배경색
  useEffect(() => { SystemUI.setBackgroundColorAsync(colors.bg); }, [colors.bg]);
  useEffect(() => {
    if ((loaded || error) && restored && themeRestored) splashMin.then(() => SplashScreen.hideAsync());
  }, [loaded, error, restored, themeRestored]);
  if (!loaded && !error) return null;
  // 끌어서 닫을 때 화면 뒤로 드러나는 스택 바탕의 팔레트 색
  const base = scheme === 'dark' ? DarkTheme : DefaultTheme;
  const navTheme = { ...base, colors: { ...base.colors, background: colors.bg } };
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider value={navTheme}>
        <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
          <Stack.Screen name="index" />
          <Stack.Screen name="community/write" options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="menu" options={{ animation: 'slide_from_right', fullScreenGestureEnabled: true }} />
          <Stack.Screen name="login" options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="auth/signup" options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="auth/reset" options={{ presentation: 'fullScreenModal' }} />
        </Stack>
        <Toast />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
