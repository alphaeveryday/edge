import { Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CommunityIcon, ExploreIcon, HomeIcon, WatchIcon } from '@/components/TabIcons';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function TabsLayout() {
  const { bottom } = useSafeAreaInsets();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.text,
        tabBarInactiveTintColor: colors.textMuted,
        // 탭 내용 49 위아래 여백, 시스템 하단 영역 위 8
        tabBarStyle: { height: 63 + bottom, paddingTop: 6, paddingBottom: bottom + 8, backgroundColor: colors.white, borderTopWidth: 1, borderTopColor: colors.tabBarLine },
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
