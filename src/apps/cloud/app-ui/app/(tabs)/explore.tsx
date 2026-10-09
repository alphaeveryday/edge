import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { isApiError } from '@/api';
import { TopBar } from '@/components/TopBar';
import { PageTitle, SectionHead, SectorIcon, Sticker } from '@/components/ui';
import { DailySheet } from '@/features/analysis/DailySheet';
import { useDaily } from '@/features/analysis/queries';
import { analysisAsOf } from '@/lib/format';
import { useRank } from '@/features/explore/queries';
import { useToast } from '@/store/toast';
import { createStyles } from '@/theme/theme';
import { PAGE_X, radius } from '@/theme/tokens';
import { Loading } from '@/components/state';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';

export default function Explore() {
  const styles = useStyles();
  const router = useRouter();
  const toast = useToast((s) => s.show);
  const q = useRank();
  const rows = q.data ?? [];
  // 순위 순서로 넘기는 분석 상세 시트
  const [sel, setSel] = useState<number | null>(null);
  const cur = sel === null ? undefined : rows[sel];
  // 닫히는 동안의 마지막 ETF 유지
  const last = useRef(cur);
  if (cur) last.current = cur;
  const view = cur ?? last.current;
  const daily = useDaily(cur?.etf.code ?? '', undefined, !!cur);
  useEffect(() => {
    if (!daily.error) return;
    toast(isApiError(daily.error, 'NOT_READY') ? '아직 AI 분석이 준비되지 않았어요' : '불러오지 못했어요', 'error');
    setSel(null);
  }, [daily.error, toast]);
  const nextIdx = sel === null || rows.length < 2 ? null : (sel + 1) % rows.length;
  const pull = usePullRefresh();
  return (
    <View style={styles.root}>
      <TopBar />
      <ScrollView {...pull.scroll} contentContainerStyle={{ paddingBottom: 28 }} showsVerticalScrollIndicator={false}>
        {pull.indicator}
        <PageTitle title="탐색" meta={analysisAsOf()} />
        <View style={{ paddingTop: 22 }}>
          <SectionHead title="AI가 보는 오늘 순위" />
        </View>
        <Text style={styles.lead}>재료가 확인된 ETF부터 위에 있어요.</Text>
        {q.isPending && <Loading rows={5} />}
        <View style={{ paddingTop: 4, paddingHorizontal: PAGE_X }}>
          {rows.map((r, i) => {
            const top3 = r.rank <= 3;
            return (
              <Pressable key={r.etf.code} onPress={() => setSel(i)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
                <View style={styles.rowHead}>
                  <Text style={[styles.rank, top3 ? styles.rankTop : styles.rankPlain]}>{top3 ? `${r.rank}위` : String(r.rank)}</Text>
                  <SectorIcon theme={r.etf.theme} bg={r.etf.logoBg} size={20} />
                  <Text numberOfLines={1} style={styles.name}>{r.etf.name}</Text>
                  <View style={{ flex: 1 }} />
                  <Sticker signal={r.etf.signal} size={24} radius={8} />
                </View>
                <Text numberOfLines={1} style={styles.title}>{r.title}</Text>
                <View style={styles.chips}>
                  {r.chips.map((c) => <Text key={c} numberOfLines={1} style={styles.chip}>{c}</Text>)}
                </View>
              </Pressable>
            );
          })}
        </View>
      </ScrollView>
      {view && (
        <DailySheet
          code={view.etf.code}
          daily={daily.data}
          open={!!cur}
          onClose={() => setSel(null)}
          withVote
          linkEtf
          next={nextIdx === null ? undefined : { code: rows[nextIdx].etf.code, name: rows[nextIdx].etf.name }}
          onNext={nextIdx === null ? undefined : () => setSel(nextIdx)}
        />
      )}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  lead: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, paddingTop: 6, paddingHorizontal: PAGE_X },
  row: { gap: 13, paddingVertical: 24, borderBottomWidth: 1, borderBottomColor: colors.line },
  rowHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  rank: { fontFamily: fam.monoExtraBold, fontSize: 12, textAlign: 'center', overflow: 'hidden' },
  rankTop: { color: colors.bg, backgroundColor: colors.text, borderRadius: radius.tag, paddingVertical: 3, paddingHorizontal: 7 },
  rankPlain: { color: colors.textFaint, width: 20 },
  name: { fontFamily: fam.bold, fontSize: 12, color: colors.textMuted, flexShrink: 1 },
  title: { fontFamily: fam.extrabold, fontSize: 18, lineHeight: 25, letterSpacing: -0.5, color: colors.text },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { maxWidth: '100%', fontFamily: fam.bold, fontSize: 12, color: colors.textSub, backgroundColor: colors.card, borderRadius: radius.tag, paddingVertical: 5, paddingHorizontal: 9, overflow: 'hidden' },
}));
