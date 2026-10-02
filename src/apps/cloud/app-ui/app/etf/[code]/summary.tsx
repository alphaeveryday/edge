import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { LinkRow, PageScroll } from '@/components/ui';
import { LineChart } from '@/features/etf/LineChart';
import { MoveSheet } from '@/features/etf/MoveSheet';
import { useChart, useEtf, useMove } from '@/features/etf/queries';
import { ErrorView, Loading } from '@/components/state';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function EtfSummary() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const { data: etf } = useEtf(code);
  const chartQ = useChart(code);
  const chart = chartQ.data;
  const { data: move } = useMove(code);
  const [open, setOpen] = useState(false);
  const refresh = usePullRefresh();
  if (chartQ.isError) return <ErrorView onRetry={() => chartQ.refetch()} />;
  if (!etf || !chart) return <Loading rows={3} />;
  return (
    <PageScroll refreshControl={refresh} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 22 }}>
      {chart && <LineChart data={chart} name={etf.name} price={etf.price} changePct={etf.changePct} />}
      <View style={styles.divider} />
      <Pressable onPress={() => setOpen(true)} style={({ pressed }) => [styles.why, pressed && { opacity: 0.6 }]}>
        <View style={styles.whyHead}>
          <Svg width={15} height={15} viewBox="0 0 16 16">
            <Path d="M8 1.2l1.5 4.1 4.1 1.5-4.1 1.5L8 12.4 6.5 8.3 2.4 6.8l4.1-1.5z" fill="#4B7BF5" />
            <Path d="M13 11l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" fill="#8134AF" />
          </Svg>
          <Text style={styles.whyTitle}>왜 움직였을까?</Text>
          <View style={{ flex: 1 }} />
          <Text style={styles.ago}>{move?.ago}</Text>
        </View>
        <Text numberOfLines={2} style={styles.whyText}>{move?.text}</Text>
        <Text style={styles.whyFoot}>{move?.foot}</Text>
      </Pressable>
      <View style={{ marginTop: 4, marginHorizontal: 20 }}>
        <LinkRow variant="accent" label="자세히 보기" onPress={() => setOpen(true)} />
      </View>
      <MoveSheet etf={etf} move={open && move ? move : null} onClose={() => setOpen(false)} />
    </PageScroll>
  );
}

const styles = StyleSheet.create({
  divider: { height: 10, backgroundColor: colors.surface },
  why: { paddingTop: 16, paddingHorizontal: 20, paddingBottom: 14, gap: 11 },
  whyHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  whyTitle: { fontFamily: fam.extrabold, fontSize: 15, color: colors.primary, letterSpacing: -0.3 },
  ago: { fontFamily: fam.bold, fontSize: 12, color: colors.textMuted, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
  whyText: { fontFamily: fam.regular, fontSize: 16, lineHeight: 26, color: colors.text },
  whyFoot: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginTop: 2 },
});
