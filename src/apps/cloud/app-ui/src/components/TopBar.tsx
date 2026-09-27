import { useRouter } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useUnreadCount } from '@/features/notification/queries';
import { IconButton } from './ui';

// 홈 등 탭 화면 우상단의 떠 있는 검색·알림·메뉴 버튼
export function TopBar() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: unread } = useUnreadCount();
  return (
    <View style={[styles.root, { top: top + 6 }]}>
      <IconButton icon="search" size={34} floating onPress={() => router.push('/search')} />
      <IconButton icon="bell" size={34} floating badge={unread || undefined} onPress={() => router.push('/notifications')} />
      <IconButton icon="menu" size={34} floating onPress={() => router.push('/menu')} />
    </View>
  );
}

export const TOP_BAR_H = 48;

const styles = StyleSheet.create({
  root: { position: 'absolute', right: 16, zIndex: 30, flexDirection: 'row', alignItems: 'center', gap: 8 },
});
