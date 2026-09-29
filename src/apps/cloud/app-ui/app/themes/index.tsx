import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { NavBar, PageTitle, SectionHead, SectorIcon } from '@/components/ui';
import { useThemeFeed } from '@/features/explore/queries';
import { colors, PAGE_X } from '@/theme/tokens';
import { Loading } from '@/components/state';
import { fam } from '@/theme/typography';

export default function Themes() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useThemeFeed();
  const data = q.data;
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="테마" onBack={() => router.back()} />
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 30 }}>
        <PageTitle title="테마" />
        <View style={{ paddingTop: 26 }}><SectionHead title="테마 분석" meta={`${data?.length ?? 0}개 테마`} /></View>
        <View style={styles.sorts}>
          <View style={[styles.sort, styles.sortOn]}><Text style={styles.sortText}>전체</Text></View>
        </View>
        {q.isPending && <Loading rows={5} />}
        <View style={{ paddingTop: 4, paddingHorizontal: PAGE_X }}>
          {data?.map((t) => (
            <Pressable key={t.key} onPress={() => router.push(`/themes/${encodeURIComponent(t.key)}`)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
              <View style={[styles.thumb, { backgroundColor: t.bg }]}><SectorIcon theme={t.key} bg={t.bg} size={44} /></View>
              <View style={{ flex: 1 }}>
                <View style={styles.nameRow}>
                  <Text style={styles.name}>{t.label}</Text>
                  <Text style={styles.count}>ETF {t.count}종</Text>
                </View>
                <Text numberOfLines={2} style={styles.headline}>{t.headline}</Text>
              </View>
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  sorts: { flexDirection: 'row', gap: 18, paddingTop: 16, paddingHorizontal: PAGE_X, borderBottomWidth: 1, borderBottomColor: colors.surface },
  sort: { paddingBottom: 12, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  sortOn: { borderBottomColor: colors.text },
  sortText: { fontSize: 15, color: colors.text, fontFamily: fam.extrabold },
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: colors.surface },
  thumb: { width: 62, height: 62, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  name: { fontFamily: fam.bold, fontSize: 16, color: colors.text },
  count: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  headline: { fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textSub, marginTop: 6 },
});
