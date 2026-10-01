import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { Axis } from '@/api';
import { IconButton, LinkRow, Sticker } from '@/components/ui';
import { DailySheet } from '@/features/analysis/DailySheet';
import { DisclaimerSheet } from '@/features/auth/DisclaimerSheet';
import { dirSignal } from '@/features/analysis/dir';
import { axisHref } from '@/features/analysis/axisHref';
import { useDaily } from '@/features/analysis/queries';
import { QueryState } from '@/components/state';
import { colors, signal as SIG, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function EtfBrief() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const router = useRouter();
  const [date, setDate] = useState<string>();
  const [open, setOpen] = useState(false);
  const q = useDaily(code, date);
  const openMetric = (axis: Axis) => router.push(axisHref(code, axis));
  return (
    <QueryState query={q} rows={3} pending={{ title: '오늘 분석은 08:30에 올라와요', sub: '발행되면 여기에서 바로 볼 수 있어요' }}>
      {(d) => { const now = SIG[d.now]; return (
    <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 32 }}>
      <View style={styles.strip}>
        <IconButton icon="prev" size={28} soft color={colors.textSub} />
        <View style={styles.days}>
          {d.dates.map((x) => {
            const on = x.key === d.date;
            return (
              <Pressable key={x.key} onPress={() => x.hasDaily && setDate(x.key)} style={[styles.day, on && styles.dayOn]}>
                <Text style={[styles.dayW, { color: on ? colors.white : colors.textFaint }]}>{x.w}</Text>
                <Text style={[styles.dayD, { color: on ? colors.white : x.hasDaily ? colors.text : colors.textDisabled }]}>{x.d}</Text>
                <View style={[styles.dayLine, { backgroundColor: on ? 'rgba(255,255,255,0.6)' : x.hasDaily ? colors.up : 'transparent' }]} />
              </Pressable>
            );
          })}
        </View>
        <IconButton icon="next" size={28} soft color={colors.textSub} />
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
      <DailySheet code={code} daily={d} open={open} onClose={() => setOpen(false)} />
      <DisclaimerSheet />
    </ScrollView>
      ); }}
    </QueryState>
  );
}

const styles = StyleSheet.create({
  strip: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14, marginHorizontal: 20 },
  days: { flex: 1, flexDirection: 'row', gap: 4 },
  day: { flex: 1, height: 42, borderRadius: radius.control, alignItems: 'center', justifyContent: 'center', gap: 1, backgroundColor: colors.card },
  dayOn: { backgroundColor: colors.text },
  dayW: { fontFamily: fam.regular, fontSize: 10 },
  dayD: { fontFamily: fam.monoExtraBold, fontSize: 13 },
  dayLine: { width: 12, height: 3, borderRadius: 999 },
  headTitle: { fontFamily: fam.bold, fontSize: 18, color: colors.text, letterSpacing: -0.5, marginTop: 20, marginHorizontal: 20 },
  article: { marginTop: 20, marginHorizontal: 22 },
  question: { fontFamily: fam.extrabold, fontSize: 24, lineHeight: 33, letterSpacing: -0.7, color: colors.text },
  dateline: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginTop: 12 },
  card: { marginTop: 20, borderRadius: radius.card, backgroundColor: colors.white, borderWidth: 1, borderColor: colors.line, overflow: 'hidden', shadowColor: colors.text, shadowOpacity: 0.06, shadowRadius: 10, shadowOffset: { width: 0, height: 6 } },
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
  cardFoot: { borderTopWidth: 1, borderTopColor: colors.surface, paddingHorizontal: 0 },
});
