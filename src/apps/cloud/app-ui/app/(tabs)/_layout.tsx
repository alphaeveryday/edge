import { Tabs } from 'expo-router';
import { CommunityIcon, ExploreIcon, HomeIcon, WatchIcon } from '@/components/TabIcons';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.text,
        tabBarInactiveTintColor: colors.textFaint,
        tabBarStyle: { backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: 'rgba(0,0,0,0.07)' },
        tabBarLabelStyle: { fontFamily: fam.semibold, fontSize: 11 },
      }}
    >
      <Tabs.Screen name="home" options={{ title: '홈', tabBarIcon: ({ color }) => <HomeIcon color={color} /> }} />
      <Tabs.Screen name="watch" options={{ title: '관심', tabBarIcon: ({ color }) => <WatchIcon color={color} /> }} />
      <Tabs.Screen name="explore" options={{ title: '탐색', tabBarIcon: ({ color }) => <ExploreIcon color={color} /> }} />
      <Tabs.Screen name="community" options={{ title: '커뮤니티', tabBarIcon: ({ color }) => <CommunityIcon color={color} /> }} />
    </Tabs>
  );
}
