import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { TOP_BAR_H, TopBar } from '@/components/TopBar';
import { Chip, IconButton, PageTitle, SectorIcon } from '@/components/ui';
import { EtfPostRow } from '@/features/community/EtfPostRow';
import { VoteCard } from '@/features/community/VoteCard';
import { useFeed, useVoteStat } from '@/features/community/queries';
import { useEtf } from '@/features/etf/queries';
import { useRank } from '@/features/explore/queries';
import { useRequireLogin } from '@/store/session';
import { colors, PAGE_X } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Community() {
  const router = useRouter();
  const requireLogin = useRequireLogin();
  const { top } = useSafeAreaInsets();
  const [scope, setScope] = useState<'all' | 'mine'>('all');
  const { data: posts } = useFeed(scope);
  // 탐색 1위 ETF 대상의 오늘의 투표
  const voteCode = useRank().data?.[0]?.etf.code ?? '';
  const { data: stat } = useVoteStat(voteCode, !!voteCode);
  const { data: voteEtf } = useEtf(voteCode);
  return (
    <View style={styles.root}>
      <TopBar />
      <ScrollView contentContainerStyle={{ paddingTop: top + TOP_BAR_H, paddingBottom: 90 }} showsVerticalScrollIndicator={false}>
        <PageTitle title="커뮤니티" />
        <View style={styles.chips}>
          <Chip label="전체" on={scope === 'all'} onPress={() => setScope('all')} />
          <Chip label="내 관심" on={scope === 'mine'} onPress={() => setScope('mine')} />
        </View>
        {stat && voteEtf && (
          <View style={styles.voteWrap}>
            <View style={styles.voteHead}>
              <SectorIcon theme={voteEtf.theme} bg={voteEtf.logoBg} size={18} />
              <Text style={styles.voteHeadText}>{voteEtf.name.replace(/^(TIGER|KODEX|PLUS|HANARO|SOL)\s*/, '')} · 오늘의 투표</Text>
            </View>
            <VoteCard stat={stat} />
          </View>
        )}
        {posts?.map((p) => <EtfPostRow key={p.id} post={p} showTag />)}
        {posts && posts.length === 0 && <Text style={styles.empty}>관심 ETF를 담으면 그 ETF의 글이 모여요</Text>}
      </ScrollView>
      <View style={styles.fab}>
        <IconButton icon="plus" size={56} fab onPress={() => requireLogin('글쓰기', () => router.push('/community/write'))} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  chips: { flexDirection: 'row', gap: 6, paddingTop: 12, paddingHorizontal: PAGE_X },
  voteWrap: { marginTop: 16, marginHorizontal: 16, marginBottom: 6 },
  voteHead: { flexDirection: 'row', alignItems: 'center', gap: 7, marginBottom: 10, paddingHorizontal: 2 },
  voteHeadText: { fontFamily: fam.extrabold, fontSize: 12.5, color: colors.textSub },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textSub, paddingVertical: 44 },
  fab: { position: 'absolute', right: 18, bottom: 18 },
});
