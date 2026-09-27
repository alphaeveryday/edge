import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Avatar, Chevron, NavBar, ToggleRow } from '@/components/ui';
import { useMe } from '@/features/community/queries';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Profile() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: me } = useMe();
  const logout = useSession((s) => s.logout);
  const toast = useToast((s) => s.show);
  const [notif, setNotif] = useState(true);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="계정" onBack={() => router.back()} />
      <ScrollView showsVerticalScrollIndicator={false}>
        <Pressable onPress={() => router.push('/profile/community')} style={({ pressed }) => [styles.me, pressed && { opacity: 0.6 }]}>
          {me && <Avatar label={me.nick} bg={me.avatarBg} size={52} />}
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{me?.nick}</Text>
            <Text style={styles.email}>jisoo.kim@gmail.com</Text>
          </View>
          <Chevron size={16} color={colors.textDisabled} />
        </Pressable>
        <Text style={styles.cap}>알림</Text>
        <View style={styles.card}>
          <ToggleRow label="관심 ETF 온도 변화 알림" sub="온도가 바뀌는 순간에만 알려드려요" on={notif} onToggle={() => setNotif((v) => !v)} />
        </View>
        <Pressable onPress={() => { logout(); router.replace('/login'); toast('로그아웃했어요'); }} style={styles.logout}>
          <Text style={styles.logoutText}>로그아웃</Text>
        </Pressable>
        <Text style={styles.version}>ETF Orca v0.1.0</Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.card },
  me: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 20 },
  name: { fontFamily: fam.extrabold, fontSize: 19, color: colors.text },
  email: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  cap: { fontFamily: fam.bold, fontSize: 13, color: colors.textFaint, paddingTop: 24, paddingHorizontal: 24, paddingBottom: 8 },
  card: { backgroundColor: colors.white, borderRadius: 14, marginHorizontal: 20, paddingHorizontal: 16 },
  logout: { alignItems: 'center', paddingTop: 26, paddingBottom: 6 },
  logoutText: { fontFamily: fam.semibold, fontSize: 15, color: colors.up },
  version: { textAlign: 'center', fontFamily: fam.regular, fontSize: 12, color: colors.textDisabled, paddingBottom: 28 },
});
