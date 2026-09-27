import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { colors } from '@/theme/tokens';

const queryClient = new QueryClient();

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="story/[etf]" options={{ presentation: 'fullScreenModal', animation: 'fade' }} />
        <Stack.Screen name="community/write" options={{ presentation: 'modal' }} />
      </Stack>
    </QueryClientProvider>
  );
}
