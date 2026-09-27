import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Chevron, Chip, NavBar, PageTitle, SectorIcon } from '@/components/ui';
import { useIssues } from '@/features/issue/queries';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Issues() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const [tab, setTab] = useState<'all' | 'mine'>('all');
  const { data } = useIssues(tab);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="이슈" onBack={() => router.back()} />
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>
        <View style={styles.titleRow}>
          <View style={{ flex: 1 }}><PageTitle title="이슈" sub="내 ETF를 많이 움직인 순" /></View>
          <View style={styles.live}>
            <View style={styles.liveDot} />
            <Text style={styles.liveText}>10분 전 업데이트</Text>
          </View>
        </View>
        <View style={styles.chips}>
          <Chip label="전체" on={tab === 'all'} onPress={() => setTab('all')} />
          <Chip label="내 관심" on={tab === 'mine'} onPress={() => setTab('mine')} />
        </View>
        <View style={styles.list}>
          {data?.map((r) => (
            <Pressable key={r.id} onPress={() => router.push(`/issues/${r.id}`)} style={({ pressed }) => [styles.card, pressed && { opacity: 0.6 }]}>
              <View style={styles.rankCol}>
                <Text style={styles.rank}>{r.rank}</Text>
                {r.delta > 0 && <Text style={[styles.delta, { color: colors.up }]}>▲{r.delta}</Text>}
                {r.delta < 0 && <Text style={[styles.delta, { color: colors.down }]}>▼{-r.delta}</Text>}
                {r.delta === 0 && <Text style={[styles.delta, { color: '#C9CED8' }]}>–</Text>}
              </View>
              <View style={styles.mid}>
                <Text numberOfLines={1} style={styles.title}>{r.title}</Text>
                <Text numberOfLines={1} style={styles.kw}>{r.kw}</Text>
                {r.etf && (
                  <View style={styles.etfRow}>
                    <SectorIcon theme={r.etf.theme} bg={r.etf.logoBg} size={18} />
                    <Text style={styles.etfName}>{r.etf.name}</Text>
                    <Text numberOfLines={1} style={styles.etfSub}>{r.etf.sub}</Text>
                  </View>
                )}
              </View>
              <Chevron size={16} color={colors.textDisabled} />
            </Pressable>
          ))}
          {data && data.length === 0 && <Text style={styles.empty}>관심 ETF를 추가하면 관련 이슈가 여기에 모여요</Text>}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  titleRow: { flexDirection: 'row', alignItems: 'flex-start' },
  live: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 20, marginRight: PAGE_X },
  liveDot: { width: 6, height: 6, borderRadius: 999, backgroundColor: '#34D399' },
  liveText: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  chips: { flexDirection: 'row', gap: 6, paddingTop: 18, paddingHorizontal: PAGE_X },
  list: { gap: 10, paddingTop: 16, paddingHorizontal: PAGE_X },
  card: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: colors.card, borderRadius: 18, paddingTop: 18, paddingRight: 16, paddingBottom: 16, paddingLeft: 18 },
  rankCol: { width: 20, alignSelf: 'flex-start', alignItems: 'center', gap: 2, paddingTop: 1 },
  rank: { fontFamily: fam.monoBold, fontSize: 17, color: colors.text },
  delta: { fontFamily: fam.monoBold, fontSize: 10 },
  mid: { flex: 1, gap: 7 },
  title: { fontFamily: fam.bold, fontSize: 17, color: colors.text, letterSpacing: -0.34 },
  kw: { fontFamily: fam.regular, fontSize: 14, color: colors.textSub },
  etfRow: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 2 },
  etfName: { fontFamily: fam.semibold, fontSize: 13, color: colors.text },
  etfSub: { flex: 1, fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textFaint, paddingVertical: 40 },
});
