import { StyleSheet, Text, View } from 'react-native';
import type { HeatCell } from '@/api';
import { dirLabel } from '@/features/analysis/dir';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

type Mode = 'temp' | 'chg';

const TEMP_BG = { help: '#FBD5D8', neutral: '#E9EBEF', burden: '#CFE0FB' } as const;
const TEMP_TAG = { help: colors.up, neutral: colors.neutral, burden: colors.down } as const;
const chgBg = (v: number) => (v >= 2 ? '#F9C2C7' : v > 0 ? '#FCE3E5' : v <= -2 ? '#BFD7FA' : v < 0 ? '#DCE9FC' : '#E9EBEF');

// 비중 순으로 두 줄에 나눠 담는 단순 트리맵
export function HeatMap({ cells, mode }: { cells: HeatCell[]; mode: Mode }) {
  const sorted = [...cells].sort((a, b) => b.weight - a.weight);
  const total = sorted.reduce((a, c) => a + c.weight, 0);
  const rows: HeatCell[][] = [[], []];
  let acc = 0;
  for (const c of sorted) {
    rows[acc < total / 2 ? 0 : 1].push(c);
    acc += c.weight;
  }
  const rowW = rows.map((r) => r.reduce((a, c) => a + c.weight, 0));
  return (
    <View style={styles.root}>
      {rows.map((r, i) =>
        r.length ? (
          <View key={i} style={[styles.row, { flex: Math.max(rowW[i], 1) }]}>
            {r.map((c) => {
              const big = c.weight >= 15;
              return (
                <View key={c.name} style={[styles.cell, { flex: c.weight, backgroundColor: mode === 'temp' ? TEMP_BG[c.dir ?? 'neutral'] : chgBg(c.changePct), padding: big ? 10 : 6 }]}>
                  <Text numberOfLines={2} style={[styles.name, { fontSize: big ? 14 : 12 }]}>{c.name}</Text>
                  <Text style={[styles.num, { fontSize: big ? 20 : 15, color: mode === 'chg' ? (c.changePct < 0 ? colors.downDeep : colors.upDeep) : colors.text }]}>
                    {mode === 'temp' ? c.weight : (c.changePct > 0 ? '+' : '') + c.changePct.toFixed(1)}<Text style={styles.unit}>%</Text>
                  </Text>
                  {mode === 'temp' && big && c.dir && <Text style={[styles.tag, { backgroundColor: TEMP_TAG[c.dir] }]}>{dirLabel[c.dir]}</Text>}
                </View>
              );
            })}
          </View>
        ) : null,
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { height: 320, gap: 3, marginTop: 12 },
  row: { flexDirection: 'row', gap: 3 },
  cell: { borderRadius: 8, alignItems: 'center', justifyContent: 'center', gap: 4, overflow: 'hidden' },
  name: { fontFamily: fam.bold, color: colors.text, letterSpacing: -0.4, textAlign: 'center', lineHeight: 16 },
  num: { fontFamily: fam.monoExtraBold, letterSpacing: -0.3 },
  unit: { fontSize: 11, fontFamily: fam.monoBold },
  tag: { fontFamily: fam.bold, fontSize: 11, color: colors.white, borderRadius: 999, paddingVertical: 2, paddingHorizontal: 7, overflow: 'hidden' },
});
