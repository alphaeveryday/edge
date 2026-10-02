import { useLocalSearchParams, useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Axis, Dir } from '@/api';
import { Chevron, NavBar, PageScroll, Sticker } from '@/components/ui';
import { dirSignal } from '@/features/analysis/dir';
import { useMetric } from '@/features/analysis/queries';
import { QueryState } from '@/components/state';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const TILE: Record<Dir | 'none', { bg: string; border: string; c: string }> = {
  help: { bg: 'rgba(240,68,82,0.08)', border: 'rgba(240,68,82,0.25)', c: colors.upDeep },
  burden: { bg: 'rgba(49,130,246,0.08)', border: 'rgba(49,130,246,0.25)', c: colors.downDeep },
  neutral: { bg: colors.card, border: colors.line, c: colors.text },
  none: { bg: colors.card, border: colors.line, c: colors.text },
};

export default function MetricPage() {
  const { code, axis } = useLocalSearchParams<{ code: string; axis: Axis }>();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useMetric(code, axis);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title={`${axis} 지표`} onBack={() => router.back()} />
      <QueryState query={q} rows={3} pending={{ title: `${axis} 지표는 준비 중이에요`, sub: '이 ETF에 맞는 지표가 정리되면 올라와요' }}>
        {(m) => (
        <PageScroll contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
          <View style={styles.verdictRow}>
            <Sticker signal={dirSignal[m.dir]} size={30} radius={11} label={m.axis} />
            <Text style={styles.verdict}>{m.verdict}</Text>
          </View>
          {m.tiles.some((t) => t.dir) && (
            <View style={styles.legend}>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.up }]} /><Text style={styles.legendText}>도움</Text></View>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.lineStrong }]} /><Text style={styles.legendText}>중립</Text></View>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.down }]} /><Text style={styles.legendText}>부담</Text></View>
            </View>
          )}
          <View style={styles.grid}>
            {m.tiles.map((t) => {
              const s = TILE[t.dir ?? 'none'];
              return (
                <View key={t.label} style={[styles.tile, { backgroundColor: s.bg, borderColor: s.border, width: t.wide ? '100%' : '48.5%', padding: t.wide ? 18 : 14 }]}>
                  <Text style={[styles.tileLabel, { fontSize: t.wide ? 14 : 13 }]}>{t.label}</Text>
                  <Text style={[styles.tileValue, { color: s.c, fontSize: t.wide ? 34 : 22 }]}>{t.value}</Text>
                  {!!t.note && <Text style={styles.tileNote}>{t.note}</Text>}
                </View>
              );
            })}
          </View>
          {m.hasDetail && (
            <Pressable onPress={() => router.push(`/factor/${code}/${axis}`)} style={styles.detail}>
              <Text style={styles.detailText}>요인 상세 보기</Text>
              <Chevron size={14} color={colors.textDisabled} />
            </Pressable>
          )}
        </PageScroll>
        )}
      </QueryState>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  body: { paddingTop: 12, paddingHorizontal: 20, paddingBottom: 34 },
  verdictRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  verdict: { flex: 1, fontFamily: fam.extrabold, fontSize: 18, letterSpacing: -0.5, color: colors.text },
  legend: { flexDirection: 'row', gap: 12, marginTop: 16 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  sw: { width: 9, height: 9, borderRadius: 3 },
  legendText: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 14 },
  tile: { borderRadius: radius.card, borderWidth: 1, gap: 6 },
  tileLabel: { fontFamily: fam.bold, color: colors.textSub },
  tileValue: { fontFamily: fam.monoExtraBold, letterSpacing: -1, lineHeight: 36 },
  tileNote: { fontFamily: fam.regular, fontSize: 13, lineHeight: 20, color: colors.textSub },
  detail: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, paddingVertical: 16, borderTopWidth: 1, borderTopColor: colors.surface },
  detailText: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
});
