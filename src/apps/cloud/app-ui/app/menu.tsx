import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Circle, Path } from 'react-native-svg';
import { Avatar, Chevron, IconButton, ListRow, PageScroll, type ListIcon } from '@/components/ui';
import { useMe } from '@/features/community/queries';
import { useUnreadCount } from '@/features/notification/queries';
import { useSession } from '@/store/session';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const GROUPS: { icon: ListIcon; label: string; href: string }[][] = [
  [{ icon: 'chart', label: '오늘의 분석', href: '/(tabs)/home' }],
  [{ icon: 'star', label: '관심 종목', href: '/(tabs)/watch' }, { icon: 'search', label: '종목 찾기', href: '/search' }],
  [{ icon: 'comm', label: '커뮤니티', href: '/(tabs)/community' }, { icon: 'bell', label: '알림', href: '/notifications' }],
];

export default function Menu() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const loggedIn = useSession((s) => s.loggedIn);
  const { data: me } = useMe();
  const { data: unread } = useUnreadCount();
  const go = (href: string) => { router.back(); setTimeout(() => router.push(href as never), 0); };
  return (
    <View style={styles.root}>
      <View style={[styles.topBar, { paddingTop: top + 6, height: top + 54 }]}>
        <IconButton icon="search" size={34} onPress={() => go('/search')} />
        <IconButton icon="bell" size={34} badge={unread || undefined} onPress={() => go('/notifications')} />
        <IconButton icon="close" size={34} onPress={() => router.back()} />
      </View>
      <PageScroll contentContainerStyle={{ paddingTop: top + 54, paddingHorizontal: 16, paddingBottom: 30 }} showsVerticalScrollIndicator={false}>
        {loggedIn && me ? (
          <Pressable onPress={() => go('/profile')} style={({ pressed }) => [styles.me, pressed && { opacity: 0.6 }]}>
            <Avatar label={me.nick} bg={me.avatarBg} size={44} />
            <Text style={styles.meName}>{me.nick}</Text>
            <Chevron size={16} color={colors.textFaint} />
          </Pressable>
        ) : (
          <Pressable onPress={() => go('/login')} style={({ pressed }) => [styles.me, pressed && { opacity: 0.6 }]}>
            <View style={styles.guest}>
              <Svg width={22} height={22} viewBox="0 0 22 22"><Circle cx={11} cy={8} r={3.6} fill={colors.white} /><Path d="M4 18.5c1.2-3.4 3.8-5 7-5s5.8 1.6 7 5" fill={colors.white} /></Svg>
            </View>
            <View style={{ flex: 1, gap: 3 }}>
              <Text style={styles.meName}>로그인하고 시작하기</Text>
              <Text style={styles.meSub}>관심 종목과 알림을 저장해요</Text>
            </View>
            <Chevron size={16} color={colors.textFaint} />
          </Pressable>
        )}
        {GROUPS.map((g, i) => (
          <View key={i} style={{ paddingTop: 10 }}>
            {g.map((m) => <ListRow key={m.label} icon={m.icon} label={m.label} onPress={() => go(m.href)} />)}
            <View style={styles.sep} />
          </View>
        ))}
      </PageScroll>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  topBar: { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 30, flexDirection: 'row', justifyContent: 'flex-end', gap: 8, paddingRight: 16, backgroundColor: colors.white },
  me: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: radius.card, backgroundColor: colors.surface },
  meName: { flex: 1, fontFamily: fam.extrabold, fontSize: 16, color: colors.text, letterSpacing: -0.3 },
  meSub: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  guest: { width: 44, height: 44, borderRadius: 999, backgroundColor: colors.lineStrong, alignItems: 'center', justifyContent: 'center' },
  sep: { height: 1, backgroundColor: colors.surface, marginTop: 10, marginHorizontal: 8 },
});
