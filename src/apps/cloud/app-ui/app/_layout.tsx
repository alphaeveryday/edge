import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { Toast } from '@/components/Toast';
import { colors } from '@/theme/tokens';
import { fontAssets } from '@/theme/typography';

SplashScreen.preventAutoHideAsync();
const queryClient = new QueryClient();

export default function RootLayout() {
  const [loaded, error] = useFonts(fontAssets);
  useEffect(() => {
    if (loaded || error) SplashScreen.hideAsync();
  }, [loaded, error]);
  if (!loaded && !error) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="story/[etf]" options={{ presentation: 'fullScreenModal', animation: 'fade' }} />
        <Stack.Screen name="community/write" options={{ presentation: 'modal' }} />
      </Stack>
      <Toast />
    </QueryClientProvider>
  );
}
