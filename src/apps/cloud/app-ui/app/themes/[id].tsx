import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';
import type { ThemeDetail as ThemeDetailT } from '@/api';
import { Avatar, BottomSheet, NavBar, SheetHead } from '@/components/ui';
import { useThemeDetail } from '@/features/explore/queries';
import { QueryState } from '@/components/state';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const CW = 322, CH = 118;

export default function ThemeDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const key = decodeURIComponent(id);
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useThemeDetail(key);
  const [sheet, setSheet] = useState(false);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="테마 분석" onBack={() => router.back()} rightIcon="share" />
      <QueryState query={q} rows={4} pending={{ title: `${key} 테마 분석은 준비 중이에요`, sub: '이 테마를 움직이는 지표가 정해지면 올라와요' }}>
        {(d) => <ThemeBody d={d} sheet={sheet} setSheet={setSheet} />}
      </QueryState>
    </View>
  );
}

function ThemeBody({ d, sheet, setSheet }: { d: ThemeDetailT; sheet: boolean; setSheet: (v: boolean) => void }) {
  const m = d.metric;
  const lo = Math.min(...m.vals, m.thresh) - 5, hi = Math.max(...m.vals, m.thresh) + 5;
  const y = (v: number) => CH - 8 - ((v - lo) / (hi - lo)) * (CH - 16);
  const x = (i: number) => 10 + (i / (m.vals.length - 1)) * (CW - 20);
  const pts = m.vals.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const mc = m.dir === 'help' ? colors.up : m.dir === 'burden' ? colors.down : colors.textSub;
  return (
    <>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 44 }}>
        <Text style={styles.kicker}>테마 분석 · {d.label}</Text>
        <Text style={styles.headline}>{d.headline}</Text>
        <Pressable onPress={() => setSheet(true)} style={({ pressed }) => [styles.stockPill, pressed && { opacity: 0.6 }]}>
          <View style={styles.dots}>
            {d.stocks.slice(0, 3).map((s, i) => (
              <View key={s.name} style={[styles.dot, { backgroundColor: s.logoBg, marginLeft: i ? -5 : 0 }]}><Text style={styles.dotText}>{s.name.slice(0, 1)}</Text></View>
            ))}
          </View>
          <Text style={styles.stockPillText}>편입 종목 {d.stocks.length}개</Text>
          <Text style={styles.stockPillArrow}>﹀</Text>
        </Pressable>
        <Text style={styles.intro}>{d.intro}</Text>
        <Text style={styles.updated}><Text style={{ color: colors.primary }}>•</Text> {d.updated} · {d.countLabel}</Text>
        <View style={styles.today}>
          <View style={styles.todayHead}>
            <View style={styles.todayDot} />
            <Text style={styles.todayCap}>오늘 반영된 것</Text>
            <View style={{ flex: 1 }} />
            <Text style={styles.todayTime}>08:30 기준</Text>
          </View>
          <Text style={styles.todayLine}>{d.todayLine}</Text>
          <Text style={styles.todayEffect}>{d.todayEffect}</Text>
        </View>

        <View style={styles.h2Row}><Text style={styles.h2No}>01</Text><Text style={styles.h2}>무엇이 중요한가</Text></View>
        <Text style={styles.lead}>{d.importantLead}</Text>
        <Text style={styles.why}>{d.importantWhy}</Text>
        <View style={styles.metric}>
          <View style={styles.metricHead}>
            <Text numberOfLines={1} style={styles.metricName}>{m.name}</Text>
            <Text style={[styles.metricNow, { color: mc }]}>{m.now}</Text>
          </View>
          <Svg width="100%" height={CH} viewBox={`0 0 ${CW} ${CH}`} style={{ marginTop: 14 }}>
            <Line x1={0} x2={CW} y1={y(m.thresh)} y2={y(m.thresh)} stroke="#C9CED8" strokeWidth={1.5} strokeDasharray="5 4" />
            <Polyline points={pts} fill="none" stroke={colors.down} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
            <Circle cx={x(m.vals.length - 1)} cy={y(m.vals[m.vals.length - 1])} r={5} fill={colors.down} stroke={colors.white} strokeWidth={2.5} />
          </Svg>
          <View style={styles.xLabels}>{m.xLabels.map((t) => <Text key={t} style={styles.xLabel}>{t}</Text>)}</View>
          <View style={styles.metricFoot}>
            <View style={styles.refLine} />
            <Text style={styles.refLabel}>{m.refLabel}</Text>
            <Text style={[styles.refState, { color: mc }]}>{m.state}</Text>
          </View>
        </View>

        <View style={styles.h2Row}><Text style={styles.h2No}>02</Text><Text style={styles.h2}>우리의 전망</Text></View>
        <Text style={styles.thesis}><Text style={styles.thesisHl}>{d.thesis}</Text></Text>
        <View style={{ marginTop: 20, marginHorizontal: PAGE_X, gap: 16 }}>
          {[
            { t: d.surface, c: '#C9CED8', style: styles.pSurface },
            { t: d.structure, c: colors.down, style: styles.pStructure },
            { t: d.structureWhy, c: '#C9CED8', style: styles.pBody },
            { t: d.soWhat, c: '#C9CED8', style: styles.pBody },
          ].map((p, i) => (
            <View key={i} style={styles.p}>
              <View style={[styles.pDot, { backgroundColor: p.c }]} />
              <Text style={[styles.pText, p.style]}>{p.t}</Text>
            </View>
          ))}
        </View>
      </ScrollView>
      <BottomSheet open={sheet} onClose={() => setSheet(false)}>
        <SheetHead title={`${d.label} 편입 종목`} sub={`${d.stocks.length}개`} onClose={() => setSheet(false)} />
        <ScrollView style={{ marginTop: 8 }} showsVerticalScrollIndicator={false}>
          {d.stocks.map((s) => (
            <View key={s.name} style={styles.stockRow}>
              <Avatar label={s.name} bg={s.logoBg} size={36} />
              <View style={{ flex: 1 }}>
                <Text numberOfLines={1} style={styles.stockName}>{s.name}</Text>
                <Text style={styles.stockEtfs}>{s.etfs}</Text>
              </View>
            </View>
          ))}
        </ScrollView>
      </BottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  kicker: { fontFamily: fam.bold, fontSize: 13, color: colors.primary, paddingTop: 6, paddingHorizontal: PAGE_X },
  headline: { fontFamily: fam.extrabold, fontSize: 23, lineHeight: 30, letterSpacing: -0.46, color: colors.text, paddingTop: 8, paddingHorizontal: PAGE_X },
  stockPill: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 12, marginHorizontal: PAGE_X, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 7, paddingLeft: 9, paddingRight: 13 },
  dots: { flexDirection: 'row' },
  dot: { width: 18, height: 18, borderRadius: 999, borderWidth: 1.5, borderColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  dotText: { fontFamily: fam.monoBold, fontSize: 7, color: colors.white },
  stockPillText: { fontFamily: fam.semibold, fontSize: 13, color: colors.textSub },
  stockPillArrow: { fontSize: 10, color: colors.textFaint },
  intro: { fontFamily: fam.regular, fontSize: 15, lineHeight: 26, color: '#333D4B', paddingTop: 12, paddingHorizontal: PAGE_X },
  updated: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, paddingTop: 12, paddingHorizontal: PAGE_X },
  today: { marginTop: 18, marginHorizontal: PAGE_X, backgroundColor: colors.primarySoft, borderRadius: 16, paddingVertical: 16, paddingHorizontal: 17 },
  todayHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  todayDot: { width: 7, height: 7, borderRadius: 999, backgroundColor: colors.down },
  todayCap: { fontFamily: fam.bold, fontSize: 13, color: colors.downDeep },
  todayTime: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  todayLine: { fontFamily: fam.semibold, fontSize: 15, lineHeight: 25, color: colors.text, marginTop: 10 },
  todayEffect: { fontFamily: fam.regular, fontSize: 14, lineHeight: 24, color: colors.textSub, marginTop: 7 },
  h2Row: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 34, marginHorizontal: PAGE_X, paddingTop: 22, borderTopWidth: 1, borderTopColor: colors.line },
  h2No: { fontFamily: fam.bold, fontSize: 13, color: '#C9CED8' },
  h2: { fontFamily: fam.extrabold, fontSize: 20, letterSpacing: -0.6, color: colors.text },
  lead: { fontFamily: fam.bold, fontSize: 16, lineHeight: 27, color: colors.text, marginTop: 16, marginHorizontal: PAGE_X },
  why: { fontFamily: fam.regular, fontSize: 15, lineHeight: 27, color: colors.textSub, marginTop: 12, marginHorizontal: PAGE_X },
  metric: { marginTop: 18, marginHorizontal: PAGE_X, borderWidth: 1, borderColor: colors.line, borderRadius: 16, padding: 16 },
  metricHead: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  metricName: { flex: 1, fontFamily: fam.semibold, fontSize: 13, color: colors.textSub },
  metricNow: { fontFamily: fam.monoExtraBold, fontSize: 20, letterSpacing: -0.4 },
  xLabels: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
  xLabel: { fontFamily: fam.mono, fontSize: 11, color: colors.textFaint },
  metricFoot: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.surface },
  refLine: { width: 14, height: 2, backgroundColor: '#C9CED8' },
  refLabel: { flex: 1, fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  refState: { fontFamily: fam.bold, fontSize: 13 },
  thesis: { marginTop: 14, marginHorizontal: PAGE_X, fontFamily: fam.extrabold, fontSize: 17, lineHeight: 27, letterSpacing: -0.34, color: colors.text },
  thesisHl: { backgroundColor: '#FFE27A' },
  p: { flexDirection: 'row', gap: 10 },
  pDot: { width: 5, height: 5, borderRadius: 999, marginTop: 11 },
  pText: { flex: 1, fontSize: 15, lineHeight: 28 },
  pSurface: { fontFamily: fam.regular, color: colors.textMuted },
  pStructure: { fontFamily: fam.semibold, color: colors.text },
  pBody: { fontFamily: fam.regular, color: '#333D4B' },
  stockRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: colors.surface },
  stockName: { fontFamily: fam.semibold, fontSize: 15, color: colors.text },
  stockEtfs: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, marginTop: 3 },
});
