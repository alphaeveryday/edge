import { useLocalSearchParams, useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Chevron, NavBar, RowQuote, SectorIcon, Sticker } from '@/components/ui';
import { useRank } from '@/features/explore/queries';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function ExploreCompare() {
  const { theme } = useLocalSearchParams<{ theme?: string }>();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data } = useRank();
  const list = (data ?? []).filter((r) => !theme || r.etf.theme === theme || (theme === 'AI·반도체' && r.etf.code === 'GRID'));
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title={theme ? `${theme} ETF ${list.length}종` : '테마 ETF'} onBack={() => router.back()} />
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 28 }}>
        <Text style={styles.lead}>전망이 좋은 순이에요</Text>
        <View style={{ paddingTop: 6, paddingHorizontal: PAGE_X }}>
          {list.map((r, i) => (
            <Pressable key={r.etf.code} disabled={!r.ready} onPress={() => router.push(`/etf/${r.etf.code}/brief`)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
              <Text style={[styles.rank, i < 3 ? styles.rankTop : styles.rankPlain]}>{i + 1}</Text>
              <SectorIcon theme={r.etf.theme} bg={r.etf.logoBg} size={32} />
              <View style={styles.mid}>
                <View style={styles.nameRow}>
                  <Text numberOfLines={1} style={styles.name}>{r.etf.name}</Text>
                  {r.ready && <Chevron size={16} color={colors.textDisabled} />}
                </View>
                <View style={styles.sub}>
                  <Sticker signal={r.etf.signal} size={24} radius={8} />
                  <RowQuote price={r.etf.price} changePct={r.etf.changePct} />
                  <View style={{ flex: 1 }} />
                  {!r.ready && <Text style={styles.notReady}>데일리 준비 중</Text>}
                </View>
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
  lead: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub, paddingTop: 14, paddingHorizontal: PAGE_X },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: colors.surface },
  rank: { fontFamily: fam.monoBold, fontSize: 13, borderRadius: 7, paddingVertical: 3, paddingHorizontal: 7, overflow: 'hidden' },
  rankTop: { color: colors.white, backgroundColor: colors.text },
  rankPlain: { color: colors.textFaint, backgroundColor: colors.surface },
  mid: { flex: 1, gap: 6 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  name: { flex: 1, fontFamily: fam.bold, fontSize: 15, color: colors.text },
  sub: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  notReady: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub },
});
