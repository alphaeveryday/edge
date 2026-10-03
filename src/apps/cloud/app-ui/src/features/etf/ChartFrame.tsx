import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Line, Path } from 'react-native-svg';
import type { ChartData } from '@/api';
import { chgColor, pct, won } from '@/lib/format';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const W = 354, H = 262, PLOT_W = 296;
const MA20 = colors.chartMa20;
const TICKS = 5;

// 등간격 최대 5개의 축 라벨
// 양 끝 라벨의 안쪽 배치
function ticks(n: number) {
  if (n <= TICKS) return Array.from({ length: n }, (_, i) => i);
  return Array.from({ length: TICKS }, (_, k) => Math.round(((n - 1) * (k + 0.5)) / TICKS));
}

export interface Plot {
  x: (i: number) => number;
  y: (v: number) => number;
}

// 이동평균선을 포함한 차트 틀
// 캔들과 선 본체는 children 소관
export function ChartFrame({ data, name, price, changePct, lo, hi, children }: {
  data: ChartData; name: string; price: number; changePct: number; lo: number; hi: number; children: (p: Plot) => ReactNode;
}) {
  const pad = (hi - lo) * 0.08 || hi * 0.01 || 1;
  const y = (v: number) => H - ((v - (lo - pad)) / (hi - lo + pad * 2)) * H;
  const step = PLOT_W / data.candles.length;
  const x = (i: number) => step * i + step / 2;
  const path = (arr: (number | null)[]) => arr.map((v, i) => (v == null ? null : `${i && arr[i - 1] != null ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)).filter(Boolean).join(' ');
  const grid = [0.2, 0.5, 0.8].map((f) => ({ f, v: Math.round(lo - pad + (hi - lo + pad * 2) * (1 - f)) }));
  const last5 = data.ma5[data.ma5.length - 1];
  const last20 = data.ma20[data.ma20.length - 1];
  return (
    <View style={styles.root}>
      <View style={styles.plot}>
        <View style={styles.legend} pointerEvents="none">
          <View style={styles.legendRow}>
            <Text style={styles.legendName}>{name} · {data.range}</Text>
            <Text style={[styles.legendQuote, { color: chgColor(changePct) }]}>{won(price)} {pct(changePct)}</Text>
          </View>
          <View style={[styles.legendRow, { gap: 10 }]}>
            <View style={styles.maItem}><View style={[styles.maLine, { backgroundColor: colors.textFaint }]} /><Text style={styles.maText}>MA5 <Text style={styles.maVal}>{last5 ? last5.toLocaleString('en-US') : '-'}</Text></Text></View>
            <View style={styles.maItem}><View style={[styles.maLine, { backgroundColor: MA20 }]} /><Text style={styles.maText}>MA20 <Text style={styles.maVal}>{last20 ? last20.toLocaleString('en-US') : '-'}</Text></Text></View>
          </View>
        </View>
        {grid.map((g) => (
          <Text key={g.f} style={[styles.gridLabel, { top: `${g.f * 100}%` }]}>{(g.v / 10000).toFixed(2)}만</Text>
        ))}
        <Svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={styles.svg}>
          {grid.map((g) => <Line key={g.f} x1={0} x2={PLOT_W} y1={H * g.f} y2={H * g.f} stroke={colors.surface} strokeWidth={1} />)}
          <Line x1={PLOT_W} x2={PLOT_W} y1={0} y2={H} stroke={colors.line} strokeWidth={1} />
          {children({ x, y })}
          <Path d={path(data.ma5)} fill="none" stroke={colors.textFaint} strokeWidth={1.6} strokeLinejoin="round" />
          <Path d={path(data.ma20)} fill="none" stroke={MA20} strokeWidth={2} strokeLinejoin="round" />
        </Svg>
      </View>
      <View style={styles.axis}>
        {ticks(data.axis.length).map((i) => (
          <Text key={i} style={[styles.axisLabel, { left: `${((i + 0.5) / data.axis.length) * (PLOT_W / W) * 100}%` }]}>{data.axis[i]}</Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: colors.white, borderBottomWidth: 1, borderBottomColor: colors.line },
  plot: { paddingTop: 12 },
  svg: { width: '100%', height: 340 },
  legend: { position: 'absolute', left: 16, top: 12, zIndex: 2, gap: 4 },
  legendRow: { flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  legendName: { fontFamily: fam.bold, fontSize: 12, color: colors.text },
  legendQuote: { fontFamily: fam.monoBold, fontSize: 12 },
  maItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  maLine: { width: 10, height: 2 },
  maText: { fontFamily: fam.mono, fontSize: 12, color: colors.textFaint },
  maVal: { fontFamily: fam.monoBold, color: colors.textSub },
  gridLabel: { position: 'absolute', right: 8, marginTop: -7, fontFamily: fam.mono, fontSize: 11, color: colors.textFaint, zIndex: 2 },
  axis: { height: 26, backgroundColor: colors.card, borderTopWidth: 1, borderTopColor: colors.line },
  axisLabel: { position: 'absolute', top: 6, marginLeft: -16, width: 32, textAlign: 'center', fontFamily: fam.mono, fontSize: 10, color: colors.textFaint },
});
