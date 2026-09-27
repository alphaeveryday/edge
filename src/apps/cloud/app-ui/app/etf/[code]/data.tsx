import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Chip, LinkRow, SectionHead } from '@/components/ui';
import { dirLabel } from '@/features/analysis/dir';
import { HeatMap } from '@/features/etf/HeatMap';
import { useEtfDetail } from '@/features/etf/queries';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const DIR_C = { help: colors.up, neutral: colors.neutral, burden: colors.down } as const;
const TAG = { help: { bg: 'rgba(240,68,82,0.12)', c: colors.upDeep }, neutral: { bg: colors.surface, c: colors.neutralDeep }, burden: { bg: 'rgba(49,130,246,0.12)', c: colors.downDeep } } as const;
const LEGEND_TEMP = [{ c: colors.down, t: '부담' }, { c: '#8FBBFA', t: '' }, { c: colors.neutral, t: '중립' }, { c: '#F7A1A8', t: '' }, { c: colors.up, t: '도움' }];

export default function EtfData() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const { data: d } = useEtfDetail(code);
  const [target, setTarget] = useState<'stock' | 'theme'>('stock');
  const [mode, setMode] = useState<'temp' | 'chg'>('temp');
  const [comp, setComp] = useState<'stock' | 'theme'>('stock');
  const [more, setMore] = useState(false);
  if (!d) return null;
  const rows = comp === 'stock' ? d.holdings : d.themeRows;
  const shown = more ? rows : rows.slice(0, 3);
  return (
    <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 32 }}>
      <View style={styles.insight}>
        <View style={styles.insightHead}>
          <View style={[styles.dot, { backgroundColor: DIR_C[d.insight.dir] }]} />
          <Text style={[styles.insightCap, { color: DIR_C[d.insight.dir] }]}>구성 해석</Text>
        </View>
        <Text style={styles.insightText}>{d.insight.text}</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.h2}>히트맵</Text>
        <View style={styles.chips}>
          <Chip label="종목" on={target === 'stock'} onPress={() => setTarget('stock')} />
          <Chip label="테마" on={target === 'theme'} onPress={() => setTarget('theme')} />
          <View style={styles.vline} />
          <Chip label="투자 온도" on={mode === 'temp'} onPress={() => setMode('temp')} />
          <Chip label="오늘 등락" on={mode === 'chg'} onPress={() => setMode('chg')} />
        </View>
        <View style={styles.legendRow}>
          <View style={styles.legendItem}><View style={styles.swGrad} /><Text style={styles.legendText}><Text style={styles.legendB}>{mode === 'temp' ? '색·태그' : '색·숫자'}</Text> = {mode === 'temp' ? '투자 온도' : '오늘 등락률'}</Text></View>
          <View style={styles.legendItem}><View style={styles.swBox} /><Text style={styles.legendText}><Text style={styles.legendB}>{mode === 'temp' ? '크기·숫자' : '크기'}</Text> = {mode === 'temp' ? 'ETF 내 비중' : '비중·기여도'}</Text></View>
        </View>
        <HeatMap cells={target === 'stock' ? d.stocks : d.themes} mode={mode} />
        <View style={styles.scale}>
          {LEGEND_TEMP.map((l, i) => (
            <View key={i} style={styles.scaleItem}><View style={[styles.scaleBar, { backgroundColor: l.c }]} /><Text style={styles.scaleText}>{l.t}</Text></View>
          ))}
        </View>
      </View>

      <View style={styles.divider} />
      <View style={styles.section}>
        <View style={styles.h2Row}>
          <Text style={styles.h2}>구성 상태</Text>
          <Text style={styles.h2Meta}>비중 순</Text>
        </View>
        <View style={styles.seg}>
          {(['stock', 'theme'] as const).map((k) => {
            const on = comp === k;
            return (
              <Pressable key={k} onPress={() => { setComp(k); setMore(false); }} style={[styles.segItem, on && styles.segOn]}>
                <Text style={[styles.segText, { color: on ? colors.text : colors.textMuted }]}>{k === 'stock' ? `종목 ${d.stockCount}` : `테마 ${d.themeRows.length}`}</Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={styles.compSummary}>{comp === 'stock' ? '오늘 재료가 실리는 종목부터 보여드려요.' : '재료가 실리는 테마와 아닌 테마를 나눠요.'}</Text>
        <View style={{ marginTop: 4 }}>
          {shown.map((h) => (
            <View key={h.name} style={styles.hold}>
              <View style={styles.holdHead}>
                <Text numberOfLines={1} style={styles.holdName}>{h.name}</Text>
                <Text style={styles.holdW}>{h.weight}%</Text>
                <View style={{ flex: 1 }} />
                <Text style={[styles.holdTag, { backgroundColor: TAG[h.dir].bg, color: TAG[h.dir].c }]}>{dirLabel[h.dir]}</Text>
              </View>
              <Text style={styles.holdDesc}>{h.desc}</Text>
            </View>
          ))}
        </View>
        {rows.length > 3 && (
          <View style={{ marginTop: 10 }}>
            <LinkRow variant="card" muted label={more ? '접기' : `${rows.length - 3}개 더 보기`} open={more} onPress={() => setMore((v) => !v)} />
          </View>
        )}
      </View>

      <View style={styles.divider} />
      <View style={[styles.section, { marginTop: 30, paddingTop: 24, borderTopWidth: 1, borderTopColor: colors.surface }]}>
        <View style={{ marginHorizontal: -20 }}><SectionHead title="기본 정보" /></View>
        <View style={styles.infoGrid}>
          {d.info.map((s) => (
            <View key={s.k} style={styles.infoTile}>
              <Text style={styles.infoK}>{s.k}</Text>
              <Text style={styles.infoV}>{s.v}</Text>
            </View>
          ))}
        </View>
        {!!d.blurb && <Text style={styles.blurb}>{d.blurb}</Text>}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  insight: { marginTop: 20, marginHorizontal: 20, paddingVertical: 16, paddingHorizontal: 17, borderRadius: 16, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line },
  insightHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  dot: { width: 8, height: 8, borderRadius: 999 },
  insightCap: { fontFamily: fam.extrabold, fontSize: 12 },
  insightText: { fontFamily: fam.semibold, fontSize: 15, lineHeight: 25, color: '#333D4B', marginTop: 9 },
  section: { marginTop: 22, marginHorizontal: 20 },
  h2: { fontFamily: fam.extrabold, fontSize: 20, letterSpacing: -0.6, color: colors.text },
  h2Row: { flexDirection: 'row', alignItems: 'baseline', gap: 9 },
  h2Meta: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  chips: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 12 },
  vline: { width: 1, height: 18, backgroundColor: colors.line },
  legendRow: { flexDirection: 'row', gap: 14, marginTop: 12 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  swGrad: { width: 18, height: 12, borderRadius: 3, backgroundColor: '#B98FC0' },
  swBox: { width: 18, height: 12, borderRadius: 3, borderWidth: 1.5, borderColor: colors.textFaint },
  legendText: { fontFamily: fam.regular, fontSize: 12, color: colors.textSub },
  legendB: { fontFamily: fam.bold, color: colors.text },
  scale: { flexDirection: 'row', gap: 4, marginTop: 10 },
  scaleItem: { flex: 1, alignItems: 'center', gap: 3 },
  scaleBar: { width: '100%', height: 6, borderRadius: 3 },
  scaleText: { fontFamily: fam.regular, fontSize: 11, color: colors.textMuted },
  divider: { height: 8, backgroundColor: colors.card, marginTop: 28 },
  seg: { flexDirection: 'row', gap: 3, padding: 3, marginTop: 12, borderRadius: 10, backgroundColor: colors.surface },
  segItem: { flex: 1, minHeight: 38, alignItems: 'center', justifyContent: 'center', borderRadius: 8 },
  segOn: { backgroundColor: colors.white, shadowColor: '#000', shadowOpacity: 0.06, shadowRadius: 3, shadowOffset: { width: 0, height: 1 } },
  segText: { fontFamily: fam.bold, fontSize: 14 },
  compSummary: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, marginTop: 12 },
  hold: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  holdHead: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 28 },
  holdName: { fontFamily: fam.bold, fontSize: 15, color: colors.text, flexShrink: 1 },
  holdW: { fontFamily: fam.mono, fontSize: 13, color: colors.textMuted },
  holdTag: { fontFamily: fam.bold, fontSize: 12, borderRadius: 999, paddingVertical: 4, paddingHorizontal: 9, overflow: 'hidden' },
  holdDesc: { fontFamily: fam.regular, fontSize: 14, lineHeight: 20, color: colors.textSub, marginTop: 5 },
  infoGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 14 },
  infoTile: { width: '48%', backgroundColor: colors.card, borderRadius: 12, paddingVertical: 12, paddingHorizontal: 14 },
  infoK: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  infoV: { fontFamily: fam.monoExtraBold, fontSize: 17, color: colors.text, marginTop: 4, letterSpacing: -0.3 },
  blurb: { fontFamily: fam.regular, fontSize: 14, lineHeight: 24, color: colors.textSub, marginTop: 14 },
});
