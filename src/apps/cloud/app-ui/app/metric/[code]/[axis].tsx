import { useLocalSearchParams, useRouter } from 'expo-router';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Axis, Dir } from '@/api';
import { Chevron, PageScroll } from '@/components/ui';
import { AxisNav } from '@/features/analysis/AxisNav';
import { useMetric } from '@/features/analysis/queries';
import { QueryState } from '@/components/state';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function MetricPage() {
  const styles = useStyles();
  const colors = useColors();
  const TILE: Record<Dir | 'none', { bg: string; border: string; c: string }> = {
    help: { bg: 'rgba(240,68,82,0.08)', border: 'rgba(240,68,82,0.25)', c: colors.upDeep },
    burden: { bg: 'rgba(49,130,246,0.08)', border: 'rgba(49,130,246,0.25)', c: colors.downDeep },
    neutral: { bg: colors.card, border: colors.line, c: colors.text },
    none: { bg: colors.card, border: colors.line, c: colors.text },
  };
  const { code, axis } = useLocalSearchParams<{ code: string; axis: Axis }>();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useMetric(code, axis);
  const refresh = usePullRefresh();
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <AxisNav code={code} axis={axis} dir={q.data?.dir} />
      <QueryState query={q} rows={3} pending={{ title: `${axis} 지표는 준비 중이에요`, sub: '이 ETF에 맞는 지표가 정리되면 올라와요' }}>
        {(m) => (
        <PageScroll refreshControl={refresh} contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
          <Text style={styles.headline}>{m.verdict}</Text>
          {m.tiles.some((t) => t.dir) && (
            <View style={styles.legend}>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.up }]} /><Text style={styles.legendText}>도움</Text></View>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.lineStrong }]} /><Text style={styles.legendText}>중립</Text></View>
              <View style={styles.legendItem}><View style={[styles.sw, { backgroundColor: colors.down }]} /><Text style={styles.legendText}>부담</Text></View>
            </View>
          )}
          <View style={[styles.grid, { marginTop: m.tiles.some((t) => t.dir) ? 14 : 22 }]}>
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

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  body: { paddingTop: 16, paddingHorizontal: 20, paddingBottom: 40 },
  headline: { fontFamily: fam.extrabold, fontSize: 21, lineHeight: 28, letterSpacing: -0.6, color: colors.text },
  legend: { flexDirection: 'row', gap: 12, marginTop: 22 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  sw: { width: 9, height: 9, borderRadius: 3 },
  legendText: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tile: { borderRadius: radius.card, borderWidth: 1, gap: 6 },
  tileLabel: { fontFamily: fam.bold, color: colors.textSub },
  tileValue: { fontFamily: fam.monoExtraBold, letterSpacing: -1, lineHeight: 36 },
  tileNote: { fontFamily: fam.regular, fontSize: 13, lineHeight: 20, color: colors.textSub },
  detail: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, paddingVertical: 16, borderTopWidth: 1, borderTopColor: colors.surface },
  detailText: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
}));
