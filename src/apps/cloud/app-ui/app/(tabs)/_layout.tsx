import { Tabs } from 'expo-router';
import { colors } from '@/theme/tokens';

export default function TabsLayout() {
  return (
    <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: colors.primary, tabBarInactiveTintColor: colors.textFaint }}>
      <Tabs.Screen name="home" options={{ title: '홈' }} />
      <Tabs.Screen name="watch" options={{ title: '관심' }} />
      <Tabs.Screen name="explore" options={{ title: '탐색' }} />
      <Tabs.Screen name="community" options={{ title: '커뮤니티' }} />
    </Tabs>
  );
}
