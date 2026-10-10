import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { Axis } from '@/api';
import { IconButton, LinkRow, PageScroll, Sticker } from '@/components/ui';
import { track } from '@/lib/analytics';
import { DailySheet } from '@/features/analysis/DailySheet';
import { DisclaimerNote } from '@/features/disclaimer/DisclaimerNote';
import { DisclaimerSheet } from '@/features/disclaimer/DisclaimerSheet';
import { dirSignal } from '@/features/analysis/dir';
import { axisHref } from '@/features/analysis/axisHref';
import { useDaily } from '@/features/analysis/queries';
import { QueryState } from '@/components/state';
import { kstToday } from '@/lib/format';
import { createStyles, useColors, useSignal } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function EtfBrief() {
  const styles = useStyles();
  const colors = useColors();
  const SIG = useSignal();
  const { code } = useLocalSearchParams<{ code: string }>();
  const router = useRouter();
  const [pick, setPick] = useState<{ date?: string; week?: string }>({});
  const [open, setOpen] = useState(false);
  const q = useDaily(code, pick);
  const today = kstToday();
  const openMetric = (axis: Axis) => router.push(axisHref(code, axis));
  const pull = usePullRefresh();
  return (
    <QueryState query={q} rows={3} pending={{ title: '오늘 분석은 08:30에 올라와요', sub: '발행되면 여기에서 바로 볼 수 있어요' }}>
      {(d) => { const now = SIG[d.now]; return (
    <PageScroll {...pull.scroll} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 32 }}>
      {pull.indicator}
      <View style={styles.strip}>
        <IconButton icon="prev" size={28} soft={!!d.prevWeek} disabled={!d.prevWeek} color={d.prevWeek ? colors.textSub : colors.textDisabled} onPress={() => d.prevWeek && setPick({ week: d.prevWeek })} />
        <View style={styles.days}>
          {d.dates.map((x, _, all) => {
            const on = x.key === d.date;
            const soon = !x.hasDaily && x.key > today;
            return (
              <Pressable key={x.key} onPress={() => {
                if (!x.hasDaily) return;
                // 가장 최근 분석 외 날짜만 기록
                if (x.key !== d.date && (d.nextWeek || all.some((y) => y.hasDaily && y.key > x.key))) track('analysis_past_viewed', { etf: code, date: x.key });
                setPick({ date: x.key });
              }} style={[styles.day, soon && styles.daySoon, on && styles.dayOn]}>
                <Text style={[styles.dayW, { color: on ? colors.bg : colors.textFaint }]}>{x.w}</Text>
                <Text style={[styles.dayD, { color: on ? colors.bg : x.hasDaily ? colors.text : colors.textDisabled }]}>{x.d}</Text>
                <View style={[styles.dayLine, { backgroundColor: on ? colors.bg : x.hasDaily ? colors.up : 'transparent', opacity: on ? 0.6 : 1 }]} />
              </Pressable>
            );
          })}
        </View>
        <IconButton icon="next" size={28} soft={!!d.nextWeek} disabled={!d.nextWeek} color={d.nextWeek ? colors.textSub : colors.textDisabled} onPress={() => d.nextWeek && setPick({ week: d.nextWeek })} />
      </View>
      <Text style={styles.headTitle}>{d.headTitle}</Text>
      <View style={styles.article}>
        <Text style={styles.question}>{d.question}</Text>
        <Text style={styles.dateline}>{d.dateline}</Text>
        <Pressable onPress={() => setOpen(true)} style={({ pressed }) => [styles.card, pressed && { opacity: 0.6 }]}>
          <View style={[styles.cardBar, { backgroundColor: now.color }]} />
          <View style={styles.cardBody}>
            <Text style={styles.cardCap}>단기 전망</Text>
            <View style={styles.verdictRow}>
              <Sticker signal={d.now} size={30} radius={11} showLabel={false} />
              <Text style={[styles.verdict, { color: now.color }]}>{now.label}</Text>
            </View>
            {d.prev && d.prev !== d.now && (
              <View style={styles.changed}>
                <Text style={styles.prevLabel}>{SIG[d.prev].label}</Text>
                <Text style={styles.arrow}>→</Text>
                <Text style={[styles.nowLabel, { color: now.color }]}>{now.label}</Text>
              </View>
            )}
            <Text style={styles.synth}>{d.synth}</Text>
            <View style={styles.axes}>
              {d.axes.map((a) => (
                <Pressable key={a.axis} onPress={() => a.hasPage && openMetric(a.axis)} style={styles.axis}>
                  <Sticker signal={dirSignal[a.dir]} showLabel={false} />
                  <Text style={styles.axisLabel}>{a.axis}</Text>
                </Pressable>
              ))}
            </View>
          </View>
          <View style={styles.cardFoot}>
            <LinkRow variant="accent" label="분석 자세히 보기" onPress={() => setOpen(true)} />
          </View>
        </Pressable>
      </View>
      <DisclaimerNote style={styles.note} />
      <DailySheet code={code} daily={d} open={open} onClose={() => setOpen(false)} entry="etf_page" />
      <DisclaimerSheet />
    </PageScroll>
      ); }}
    </QueryState>
  );
}

const useStyles = createStyles((colors) => ({
  strip: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14, marginHorizontal: 20 },
  days: { flex: 1, flexDirection: 'row', gap: 4 },
  day: { flex: 1, height: 42, borderRadius: radius.control, alignItems: 'center', justifyContent: 'center', gap: 1, backgroundColor: colors.card },
  daySoon: { backgroundColor: 'transparent', borderWidth: 1, borderStyle: 'dashed', borderColor: colors.lineStrong },
  dayOn: { backgroundColor: colors.text },
  dayW: { fontFamily: fam.regular, fontSize: 10 },
  dayD: { fontFamily: fam.monoExtraBold, fontSize: 13 },
  dayLine: { width: 12, height: 3, borderRadius: 999 },
  headTitle: { fontFamily: fam.bold, fontSize: 18, color: colors.text, letterSpacing: -0.5, marginTop: 20, marginHorizontal: 20 },
  article: { marginTop: 20, marginHorizontal: 22 },
  question: { fontFamily: fam.extrabold, fontSize: 24, lineHeight: 33, letterSpacing: -0.7, color: colors.text },
  dateline: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginTop: 12 },
  card: { marginTop: 20, borderRadius: radius.card, backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.line, overflow: 'hidden', shadowColor: colors.text, shadowOpacity: 0.06, shadowRadius: 10, shadowOffset: { width: 0, height: 6 } },
  cardBar: { height: 4 },
  cardBody: { paddingTop: 22, paddingHorizontal: 20, paddingBottom: 24, gap: 14 },
  cardCap: { fontFamily: fam.extrabold, fontSize: 12, color: colors.textMuted, letterSpacing: 0.7 },
  verdictRow: { flexDirection: 'row', alignItems: 'center', gap: 11, marginTop: -4 },
  verdict: { fontFamily: fam.extrabold, fontSize: 26, letterSpacing: -0.8, lineHeight: 31 },
  changed: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  prevLabel: { fontFamily: fam.bold, fontSize: 14, color: colors.textMuted },
  arrow: { fontFamily: fam.regular, fontSize: 13, color: colors.textDisabled },
  nowLabel: { fontFamily: fam.extrabold, fontSize: 14 },
  synth: { fontFamily: fam.bold, fontSize: 16, lineHeight: 27, color: colors.text, paddingTop: 14, borderTopWidth: 1, borderTopColor: colors.surface },
  axes: { flexDirection: 'row', gap: 14, paddingTop: 14, borderTopWidth: 1, borderTopColor: colors.surface },
  axis: { flex: 1, alignItems: 'center', gap: 6 },
  axisLabel: { fontFamily: fam.bold, fontSize: 11, color: colors.textSub },
  note: { marginTop: 24, marginHorizontal: 22 },
  cardFoot: { borderTopWidth: 1, borderTopColor: colors.surface, paddingHorizontal: 0 },
}));
