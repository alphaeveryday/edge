import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { LinkRow, PageScroll } from '@/components/ui';
import { DisclaimerSheet } from '@/features/disclaimer/DisclaimerSheet';
import { LineChart } from '@/features/etf/LineChart';
import { MoveSheet } from '@/features/etf/MoveSheet';
import { isApiError } from '@/api';
import { useChart, useEtf, useMove } from '@/features/etf/queries';
import { ErrorView, Loading } from '@/components/state';
import { createStyles } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function EtfSummary() {
  const styles = useStyles();
  const { code } = useLocalSearchParams<{ code: string }>();
  const { data: etf } = useEtf(code);
  const chartQ = useChart(code);
  const chart = chartQ.data;
  const moveQ = useMove(code);
  const move = moveQ.data;
  // 오늘 움직임이 없는 ETF 의 안내 문구와 시트 생략
  const none = isApiError(moveQ.error, 'NOT_READY');
  const [open, setOpen] = useState(false);
  const pull = usePullRefresh();
  if (chartQ.isError) return <ErrorView onRetry={() => chartQ.refetch()} />;
  if (!etf || !chart) return <Loading rows={3} />;
  return (
    <PageScroll {...pull.scroll} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 22 }}>
      {pull.indicator}
      {chart && <LineChart data={chart} name={etf.name} price={etf.price} changePct={etf.changePct} />}
      <View style={styles.divider} />
      <Pressable disabled={none} onPress={() => setOpen(true)} style={({ pressed }) => [styles.why, pressed && { opacity: 0.6 }]}>
        <View style={styles.whyHead}>
          <Svg width={15} height={15} viewBox="0 0 16 16">
            <Path d="M8 1.2l1.5 4.1 4.1 1.5-4.1 1.5L8 12.4 6.5 8.3 2.4 6.8l4.1-1.5z" fill="#4B7BF5" />
            <Path d="M13 11l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" fill="#8134AF" />
          </Svg>
          <Text style={styles.whyTitle}>왜 움직였을까?</Text>
          <View style={{ flex: 1 }} />
          {!!move?.ago && <Text style={styles.ago}>{move.ago}</Text>}
        </View>
        {none
          ? <Text style={styles.whyEmpty}>가격이 움직인 이유를 아직 찾지 못했어요.</Text>
          : <Text numberOfLines={2} style={styles.whyText}>{move?.text}</Text>}
        {!none && <Text style={styles.whyFoot}>{move?.foot}</Text>}
      </Pressable>
      {!none && (
        <View style={{ marginTop: 4, marginHorizontal: 20 }}>
          <LinkRow variant="accent" label="자세히 보기" onPress={() => setOpen(true)} />
        </View>
      )}
      <MoveSheet etf={etf} move={open && move ? move : null} onClose={() => setOpen(false)} />
      <DisclaimerSheet />
    </PageScroll>
  );
}

const useStyles = createStyles((colors) => ({
  divider: { height: 10, backgroundColor: colors.surface },
  why: { paddingTop: 16, paddingHorizontal: 20, paddingBottom: 14, gap: 11 },
  whyHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  whyTitle: { fontFamily: fam.extrabold, fontSize: 15, color: colors.primary, letterSpacing: -0.3 },
  ago: { fontFamily: fam.bold, fontSize: 12, color: colors.textMuted, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
  whyText: { fontFamily: fam.regular, fontSize: 16, lineHeight: 26, color: colors.text },
  whyEmpty: { fontFamily: fam.regular, fontSize: 16, lineHeight: 26, color: colors.textMuted },
  whyFoot: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginTop: 2 },
}));
