import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Animated, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { TOP_BAR_H, TopBar } from '@/components/TopBar';
import { Chip, LinkRow, PageTitle, SectionHead } from '@/components/ui';
import { EtfPostRow } from '@/features/community/EtfPostRow';
import { useHotPosts } from '@/features/community/queries';
import { EtfRow } from '@/features/etf/EtfRow';
import { EdgeCard } from '@/features/home/EdgeCard';
import { useHomeBrief, usePrefetchBriefs } from '@/features/home/queries';
import { analysisAsOf } from '@/lib/format';
import { useScrollFocus } from '@/lib/useScrollFocus';
import { useSwapFade } from '@/lib/useSwapFade';
import { api, isApiError } from '@/api';
import { useToast } from '@/store/toast';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useWatchGroup } from '@/store/watch';

export default function Home() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { group, setGroup } = useWatchGroup();
  const [showAll, setShowAll] = useState(false);
  const focus = useScrollFocus(showAll, top + TOP_BAR_H);
  const brief = useHomeBrief(group);
  const posts = useHotPosts();
  const qc = useQueryClient();
  const toast = useToast((s) => s.show);
  // 발행본이 없으면 이동 대신 토스트
  const open = async (code: string) => {
    try {
      await qc.fetchQuery({ queryKey: ['etf', 'move', code], queryFn: () => api.etf.move(code) });
      router.push(`/etf/${code}/summary`);
    } catch (e) {
      if (isApiError(e, 'NOT_READY')) toast('아직 AI 분석이 준비되지 않았어요', 'error');
      else toast('불러오지 못했어요', 'error');
    }
  };
  const b = brief.data;
  usePrefetchBriefs(b?.groups.map((g) => g.key));
  const fade = useSwapFade(b?.group, brief.isPlaceholderData);
  const rows = b ? (showAll ? b.etfs : b.etfs.slice(0, 3)) : [];
  const more = (b?.etfs.length ?? 0) > 3;
  const groupLabel = b?.groups.find((g) => g.key === b.group)?.label ?? '';

  return (
    <View style={styles.root}>
      <TopBar />
      <ScrollView ref={focus.scroll} contentContainerStyle={{ paddingTop: top + TOP_BAR_H, paddingBottom: 28 }} showsVerticalScrollIndicator={false}>
        <PageTitle title="내 종목 브리핑" meta={analysisAsOf()} />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {b?.groups.map((g) => <Chip key={g.key} label={g.label} on={g.key === group} onPress={() => setGroup(g.key)} />)}
        </ScrollView>
        <Animated.View style={{ opacity: fade }}>
          {b && <EdgeCard title={`${groupLabel} 그룹 전망 강도`} band={b.band} changePct={b.changePct} />}
          <View ref={focus.anchor} style={styles.rows}>
            {rows.map((e) => <EtfRow key={e.code} etf={e} onPress={() => open(e.code)} />)}
            {more && (
              <View style={{ marginTop: 12 }}>
                <LinkRow variant="card" muted label={showAll ? '접기' : `${b!.etfs.length - 3}개 더 보기`} open={showAll} onPress={() => setShowAll((v) => !v)} />
              </View>
            )}
            {b && b.etfs.length === 0 && <Text style={styles.empty}>관심 ETF가 없어요 · 탐색에서 담아 보세요</Text>}
          </View>
        </Animated.View>

        <View style={styles.divider} />
        <View style={{ paddingTop: 22 }}>
          <SectionHead title="커뮤니티 인기글" />
        </View>
        {posts.data?.map((p) => <EtfPostRow key={p.id} post={p} showTag />)}
        <View style={{ marginTop: 12, marginHorizontal: PAGE_X }}>
          <LinkRow variant="card" muted label="더보기" open={false} onPress={() => router.push('/(tabs)/community')} />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  chips: { flexDirection: 'row', gap: 6, paddingTop: 12, paddingHorizontal: PAGE_X },
  rows: { paddingTop: 18, paddingHorizontal: PAGE_X },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textSub, paddingVertical: 34 },
  divider: { height: 10, backgroundColor: colors.surface, marginTop: 26 },
});
