import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { BottomSheet, Chevron, NavBar, SectionHead, SectorIcon, SheetHead, Sticker } from '@/components/ui';
import { dirLabel } from '@/features/analysis/dir';
import { useIssue } from '@/features/issue/queries';
import { QueryState } from '@/components/state';
import { useMembership, useSetMembership } from '@/features/watch/queries';
import { chgColor, pct, won } from '@/lib/format';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const EFF = { help: { c: colors.up, bg: 'rgba(240,68,82,0.12)', line: '#F7A1A8' }, neutral: { c: colors.neutral, bg: colors.surface, line: colors.line }, burden: { c: colors.down, bg: 'rgba(49,130,246,0.12)', line: '#8FBBFA' } } as const;
const EFF_LABEL = { help: '상승', neutral: '중립', burden: '하락' } as const;

function AffectedRow({ etf }: { etf: { code: string; name: string; theme: string; logoBg: string; price: number; changePct: number; signal: 'strongUp' | 'up' | 'neutral' | 'down' | 'strongDown' } }) {
  const router = useRouter();
  const { data: mine } = useMembership(etf.code);
  const set = useSetMembership();
  const inWatch = (mine ?? []).length > 0;
  return (
    <Pressable onPress={() => router.push(`/etf/${etf.code}/brief`)} style={({ pressed }) => [styles.aff, pressed && { opacity: 0.6 }]}>
      <SectorIcon theme={etf.theme} bg={etf.logoBg} size={38} />
      <View style={{ flex: 1 }}>
        <View style={styles.affHead}>
          <Text style={styles.affName}>{etf.name}</Text>
          <Sticker signal={etf.signal} size={24} radius={999} />
        </View>
        <Text style={styles.affQuote}>{won(etf.price)} <Text style={{ color: chgColor(etf.changePct) }}>{pct(etf.changePct)}</Text></Text>
      </View>
      <Pressable hitSlop={6} onPress={() => set.mutate({ code: etf.code, groups: inWatch ? [] : ['base'] })} style={{ padding: 4 }}>
        <Svg width={20} height={20} viewBox="0 0 24 24">
          <Path d="M12 21s-7-4.6-9.3-9.3C.9 8 3 4.5 6.5 4.5c2 0 3.5 1 4.5 2.6 1-1.6 2.5-2.6 4.5-2.6 3.5 0 5.6 3.5 3.8 7.2C19 16.4 12 21 12 21z" fill={inWatch ? colors.up : 'none'} stroke={inWatch ? colors.up : colors.textDisabled} strokeWidth={1.8} strokeLinejoin="round" />
        </Svg>
      </Pressable>
    </Pressable>
  );
}

export default function LiveIssue() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const q = useIssue(id);
  const [points, setPoints] = useState(false);
  const [rel, setRel] = useState(true);
  const [src, setSrc] = useState(false);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="이슈" onBack={() => router.back()} />
      <QueryState query={q} rows={3}>
        {(d) => { const e = EFF[d.effect.dir]; return (<>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }}>
        <Text style={styles.kicker}>오늘 · 관련 ETF {d.affected.length}개</Text>
        <Text style={styles.title}>{d.title}</Text>
        <View style={styles.quoteWrap}>
          <View style={styles.quoteBar} />
          <View style={{ flex: 1 }}>
            <Text style={styles.body}>{d.body}</Text>
            <Pressable onPress={() => setPoints((v) => !v)} style={styles.toggle}>
              <Text style={styles.toggleText}>주목할 포인트</Text>
              <Chevron size={12} color={colors.textFaint} dir={points ? 'up' : 'down'} />
            </Pressable>
            {points && (
              <View style={{ gap: 14, marginTop: 14 }}>
                {d.points.map((p) => (
                  <View key={p} style={styles.point}>
                    <Svg width={15} height={15} viewBox="0 0 15 15" style={{ marginTop: 4 }}><Path d="M2.5 8l3.5 3.5 6.5-7" stroke={colors.down} strokeWidth={2.2} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
                    <Text style={styles.pointText}>{p}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        </View>
        <View style={{ paddingTop: 20, paddingHorizontal: PAGE_X }}>
          <Pressable onPress={() => setSrc(true)} style={({ pressed }) => [styles.srcBtn, pressed && { opacity: 0.6 }]}>
            <View style={styles.srcDot}><View style={styles.srcDotIn} /></View>
            <Text style={styles.srcText}>출처 보기</Text>
            <Chevron size={14} color={colors.textFaint} />
          </Pressable>
        </View>
        <View style={styles.rule} />
        <View style={{ paddingTop: 26 }}><SectionHead title="어떤 영향을 줄까?" /></View>
        <View style={styles.effect}>
          <View style={styles.effRail}>
            <View style={[styles.effDot, { backgroundColor: e.c }]} />
            <View style={[styles.effLine, { backgroundColor: e.line }]} />
          </View>
          <View style={{ flex: 1 }}>
            <View style={styles.effHead}>
              <Text style={styles.effTheme}>{d.effect.theme}</Text>
              <Text style={[styles.effTag, { color: e.c, backgroundColor: e.bg }]}>{EFF_LABEL[d.effect.dir]}</Text>
            </View>
            <Text style={styles.effBody}>{d.effect.body}</Text>
            <Pressable onPress={() => setRel((v) => !v)} style={styles.relToggle}>
              <Text style={styles.toggleText}>연관된 ETF</Text>
              <Chevron size={12} color={colors.textFaint} dir={rel ? 'up' : 'down'} />
              <View style={{ flex: 1 }} />
              <Text style={styles.relMeta}>관심 먼저 · {d.affected.length}개</Text>
            </Pressable>
            {rel && <View style={{ marginTop: 14 }}>{d.affected.map((a) => <AffectedRow key={a.code} etf={a} />)}</View>}
          </View>
        </View>
      </ScrollView>
      <BottomSheet open={src} onClose={() => setSrc(false)}>
        <SheetHead title="출처" sub={`${d.sources.length}건 · 탭하면 기사로 이동`} onClose={() => setSrc(false)} />
        <ScrollView style={{ marginTop: 8 }} showsVerticalScrollIndicator={false}>
          {d.sources.map((s) => (
            <Pressable key={s.url} onPress={() => Linking.openURL(s.url)} style={({ pressed }) => [styles.srcRow, pressed && { opacity: 0.6 }]}>
              <View style={styles.thumb}><Text style={styles.thumbText}>이미지</Text></View>
              <View style={{ flex: 1, gap: 5 }}>
                <Text numberOfLines={2} style={styles.srcTitle}>{s.title}</Text>
                <Text style={styles.srcPub}>{s.pub}</Text>
              </View>
              <Svg width={14} height={14} viewBox="0 0 14 14"><Path d="M3 11L11 3M5 3h6v6" stroke={colors.textDisabled} strokeWidth={1.6} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
            </Pressable>
          ))}
        </ScrollView>
      </BottomSheet>
        </>); }}
      </QueryState>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  kicker: { fontFamily: fam.semibold, fontSize: 13, color: colors.textMuted, paddingTop: 14, paddingHorizontal: PAGE_X },
  title: { fontFamily: fam.bold, fontSize: 24, lineHeight: 32, letterSpacing: -0.7, color: colors.text, paddingTop: 8, paddingHorizontal: PAGE_X },
  quoteWrap: { flexDirection: 'row', gap: 14, marginTop: 18, marginHorizontal: PAGE_X },
  quoteBar: { width: 3, borderRadius: 999, backgroundColor: colors.line },
  body: { fontFamily: fam.regular, fontSize: 16, lineHeight: 27, color: '#333D4B' },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 16 },
  toggleText: { fontFamily: fam.medium, fontSize: 15, color: colors.textMuted },
  point: { flexDirection: 'row', gap: 10 },
  pointText: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub },
  srcBtn: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 9, paddingHorizontal: 15 },
  srcDot: { width: 15, height: 15, borderRadius: 999, backgroundColor: colors.up, alignItems: 'center', justifyContent: 'center' },
  srcDotIn: { width: 5, height: 5, borderRadius: 1, backgroundColor: colors.white },
  srcText: { fontFamily: fam.medium, fontSize: 14, color: '#333D4B' },
  rule: { height: 1, backgroundColor: colors.surface, marginTop: 30 },
  effect: { flexDirection: 'row', gap: 14, marginTop: 26, marginHorizontal: PAGE_X },
  effRail: { width: 12, alignItems: 'center', paddingTop: 4 },
  effDot: { width: 10, height: 10, borderRadius: 999 },
  effLine: { flex: 1, width: 2, marginTop: 4, marginBottom: 24 },
  effHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  effTheme: { fontFamily: fam.bold, fontSize: 17, color: colors.text },
  effTag: { fontFamily: fam.bold, fontSize: 13, borderRadius: 7, paddingVertical: 4, paddingHorizontal: 9, overflow: 'hidden' },
  effBody: { fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub, marginTop: 10 },
  relToggle: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 16 },
  relMeta: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  aff: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 11 },
  affHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  affName: { fontFamily: fam.medium, fontSize: 16, color: colors.text },
  affQuote: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted, marginTop: 3 },
  srcRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  thumb: { width: 72, height: 54, borderRadius: 10, backgroundColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  thumbText: { fontFamily: fam.mono, fontSize: 8, color: colors.textFaint },
  srcTitle: { fontFamily: fam.semibold, fontSize: 14, lineHeight: 20, color: colors.text },
  srcPub: { fontFamily: fam.regular, fontSize: 12, color: colors.textFaint },
});
