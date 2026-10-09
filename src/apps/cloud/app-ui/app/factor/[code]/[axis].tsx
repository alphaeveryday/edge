import { useLocalSearchParams } from 'expo-router';
import { Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { Axis } from '@/api';
import { PageScroll, Sticker } from '@/components/ui';
import { AxisNav } from '@/features/analysis/AxisNav';
import { dirSignal } from '@/features/analysis/dir';
import { useFactor } from '@/features/analysis/queries';
import { QueryState } from '@/components/state';
import { createStyles, useSignal } from '@/theme/theme';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function FactorPage() {
  const styles = useStyles();
  const SIG = useSignal();
  const { code, axis } = useLocalSearchParams<{ code: string; axis: Axis }>();
  const { top } = useSafeAreaInsets();
  const q = useFactor(code, axis);
  const f = q.data;
  const refresh = usePullRefresh();
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <AxisNav code={code} axis={axis} dir={f?.dir} />
      <QueryState query={q} rows={3} pending={{ title: `${axis} 요인 상세는 준비 중이에요`, sub: '재료가 확인되면 이 축의 근거를 정리해 올려요' }}>
        {(f) => (
        <PageScroll refreshControl={refresh} contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
          <Text style={styles.headline}>{f.headline}</Text>
          {f.events && (
            <View style={{ gap: 20, marginTop: 22 }}>
              {f.events.map((e) => (
                <View key={e.k} style={styles.event}>
                  <Text style={[styles.mark, { color: SIG[dirSignal[e.dir]].color }]}>{SIG[dirSignal[e.dir]].mark}</Text>
                  <View style={{ flex: 1, gap: 5 }}>
                    <Text style={styles.eventK}>{e.k}</Text>
                    <Text style={styles.eventBody}>{e.body}</Text>
                  </View>
                </View>
              ))}
            </View>
          )}
          {f.chartInds && (
            <View style={{ marginTop: 22 }}>
              {f.chartInds.map((c) => (
                <View key={c.name} style={styles.ind}>
                  <Sticker signal={dirSignal[c.dir]} size={28} radius={10} showLabel={false} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.indName}>{c.name}</Text>
                    <Text style={styles.indD}>{c.d}</Text>
                  </View>
                </View>
              ))}
              {f.judg && (
                <>
                  <Text style={styles.judgTitle}>단기 판단 기준</Text>
                  <View style={{ gap: 9, marginTop: 10 }}>
                    {f.judg.map((j) => (
                      <View key={j} style={styles.judg}>
                        <View style={styles.judgDot} />
                        <Text style={styles.judgText}>{j}</Text>
                      </View>
                    ))}
                  </View>
                </>
              )}
            </View>
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
  event: { flexDirection: 'row', gap: 10 },
  mark: { width: 12, textAlign: 'center', fontFamily: fam.extrabold, fontSize: 12, marginTop: 5 },
  eventK: { fontFamily: fam.extrabold, fontSize: 16, lineHeight: 22, letterSpacing: -0.3, color: colors.text },
  eventBody: { fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub },
  ind: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  indName: { fontFamily: fam.bold, fontSize: 15, color: colors.text },
  indD: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.textSub, marginTop: 3 },
  judgTitle: { fontFamily: fam.bold, fontSize: 15, color: colors.text, marginTop: 18 },
  judg: { flexDirection: 'row', gap: 10 },
  judgDot: { width: 5, height: 5, borderRadius: 999, backgroundColor: colors.text, marginTop: 10 },
  judgText: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub },
}));
