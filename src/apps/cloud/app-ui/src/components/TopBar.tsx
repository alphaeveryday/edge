import { useRouter } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useUnreadCount } from '@/features/notification/queries';
import { colors } from '@/theme/tokens';
import { IconButton } from './ui';

// 탭 화면 상단 흰 바와 검색·알림·메뉴 버튼
export function TopBar() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: unread } = useUnreadCount();
  return (
    <View style={[styles.root, { paddingTop: top + 6, height: top + TOP_BAR_H }]}>
      <IconButton icon="search" size={34} onPress={() => router.push('/search')} />
      <IconButton icon="bell" size={34} badge={unread || undefined} onPress={() => router.push('/notifications')} />
      <IconButton icon="menu" size={34} onPress={() => router.push('/menu')} />
    </View>
  );
}

export const TOP_BAR_H = 48;

const styles = StyleSheet.create({
  root: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, paddingRight: 16, backgroundColor: colors.white },
});
