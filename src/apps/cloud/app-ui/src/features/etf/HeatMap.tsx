import { Text, View } from 'react-native';
import type { HeatCell } from '@/api';
import { dirLabel } from '@/features/analysis/dir';
import { createStyles, useColors } from '@/theme/theme';
import type { Palette } from '@/theme/tokens';
import { fam } from '@/theme/typography';

type Mode = 'temp' | 'chg';

const chgBg = (h: Palette['heat'], v: number) => (v >= 2 ? h.up2 : v > 0 ? h.up1 : v <= -2 ? h.down2 : v < 0 ? h.down1 : h.flat);

// 비중 순으로 두 줄에 나눠 담는 단순 트리맵
export function HeatMap({ cells, mode }: { cells: HeatCell[]; mode: Mode }) {
  const styles = useStyles();
  const colors = useColors();
  const TEMP_TAG = { help: colors.up, neutral: colors.neutral, burden: colors.down } as const;
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
                <View key={c.name} style={[styles.cell, { flex: c.weight, backgroundColor: mode === 'temp' ? colors.heat[c.dir ?? 'neutral'] : chgBg(colors.heat, c.changePct), padding: big ? 10 : 6 }]}>
                  <Text numberOfLines={2} style={[styles.name, { fontSize: big ? 14 : 12 }]}>{c.name}</Text>
                  <Text numberOfLines={1} adjustsFontSizeToFit style={[styles.num, { fontSize: big ? 20 : 15, color: mode === 'chg' ? (c.changePct < 0 ? colors.downDeep : colors.upDeep) : colors.text }]}>
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

const useStyles = createStyles((colors) => ({
  root: { height: 320, gap: 3, marginTop: 12 },
  row: { flexDirection: 'row', gap: 3 },
  cell: { borderRadius: 8, alignItems: 'center', justifyContent: 'center', gap: 4, overflow: 'hidden' },
  name: { fontFamily: fam.bold, color: colors.text, letterSpacing: -0.4, textAlign: 'center', lineHeight: 16 },
  num: { fontFamily: fam.monoExtraBold, letterSpacing: -0.3 },
  unit: { fontSize: 11, fontFamily: fam.monoBold },
  tag: { fontFamily: fam.bold, fontSize: 11, color: colors.onPrimary, borderRadius: 999, paddingVertical: 2, paddingHorizontal: 7, overflow: 'hidden' },
}));
