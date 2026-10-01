import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Notification, NotiKind } from '@/api';
import { Avatar, NavBar, TabItem } from '@/components/ui';
import { useNotifications, useReadAll, useReadNoti } from '@/features/notification/queries';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const KIND: Record<NotiKind, { label: string; c: string; bg: string; glyph: string }> = {
  watch: { label: '관심', c: colors.warnDeep, bg: colors.warn, glyph: '★' },
  comm: { label: '커뮤니티', c: colors.downDeep, bg: colors.down, glyph: '▣' },
};
const TABS: { k: NotiKind | 'all'; label: string }[] = [{ k: 'all', label: '전체' }, { k: 'watch', label: '관심' }, { k: 'comm', label: '커뮤니티' }];

export default function Notifications() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const [tab, setTab] = useState<NotiKind | 'all'>('all');
  const { data } = useNotifications(tab);
  const { data: all } = useNotifications('all');
  const read = useReadNoti();
  const readAll = useReadAll();
  const hasUnread = (k: NotiKind | 'all') => (all ?? []).some((n) => !n.read && (k === 'all' || n.kind === k));
  const open = (n: Notification) => {
    read.mutate(n.id);
    if (n.postId) router.push(`/post/${n.postId}`);
    else if (n.etf) router.push(`/etf/${n.etf}/brief`);
  };
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="알림" onBack={() => router.back()} rightLabel="모두 읽음" rightColor={colors.textSub} onRight={() => readAll.mutate()} />
      <View style={styles.tabs}>
        {TABS.map((t) => <TabItem key={t.k} grow={false} label={t.label} on={tab === t.k} dot={t.k !== 'all' && hasUnread(t.k)} onPress={() => setTab(t.k)} />)}
      </View>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>
        {data?.map((n) => {
          const k = KIND[n.kind];
          return (
            <Pressable key={n.id} onPress={() => open(n)} style={({ pressed }) => [styles.row, !n.read && styles.unread, pressed && { opacity: 0.6 }]}>
              <Avatar label={k.glyph} bg={k.bg} size={26} />
              <View style={{ flex: 1, gap: 6 }}>
                <View style={styles.head}>
                  <Text style={[styles.kind, { color: k.c }]}>{k.label}</Text>
                  <View style={{ flex: 1 }} />
                  <Text style={styles.time}>{n.time}</Text>
                </View>
                <Text style={[styles.title, { fontFamily: n.read ? fam.semibold : fam.extrabold }]}>{n.title}</Text>
                <Text style={styles.body}>{n.body}</Text>
              </View>
            </Pressable>
          );
        })}
        {data && data.length === 0 && <Text style={styles.empty}>이 종류의 알림이 아직 없어요</Text>}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  tabs: { flexDirection: 'row', paddingTop: 6, paddingHorizontal: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  row: { flexDirection: 'row', gap: 12, paddingTop: 16, paddingHorizontal: 16, paddingBottom: 15, borderBottomWidth: 1, borderBottomColor: colors.surface },
  unread: { backgroundColor: colors.unreadBg },
  head: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  kind: { fontFamily: fam.bold, fontSize: 13 },
  time: { fontFamily: fam.mono, fontSize: 12, color: colors.textFaint },
  title: { fontSize: 15, lineHeight: 22, color: colors.text, letterSpacing: -0.3 },
  body: { fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textMuted },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textFaint, paddingVertical: 50 },
});
