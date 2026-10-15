import { useLocalSearchParams, useRouter } from 'expo-router';
import { View } from 'react-native';
import { IconButton, PageScroll, useBottomGap } from '@/components/ui';
import { EtfPostRow } from '@/features/community/EtfPostRow';
import { VoteCard } from '@/features/community/VoteCard';
import { useEtfPosts, useVoteStat } from '@/features/community/queries';
import { useRequireLogin } from '@/store/session';
import { usePullRefresh } from '@/lib/usePullRefresh';
import { loadMore } from '@/lib/usePages';
import { createStyles } from '@/theme/theme';

export default function EtfCommunity() {
  const styles = useStyles();
  const { code } = useLocalSearchParams<{ code: string }>();
  const router = useRouter();
  const requireLogin = useRequireLogin();
  const { data: stat } = useVoteStat(code);
  const postQ = useEtfPosts(code);
  const posts = postQ.data;
  const gap = useBottomGap();
  const pull = usePullRefresh(loadMore(postQ));
  return (
    <View style={{ flex: 1 }}>
      <PageScroll {...pull.scroll} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 90 }}>
        {pull.indicator}
        {stat && <View style={styles.vote}><VoteCard stat={stat} entry="etf_community" /></View>}
        {posts?.map((p) => <EtfPostRow key={p.id} post={p} onQuoteTag={() => router.replace(`/etf/${code}/brief`)} />)}
      </PageScroll>
      <View style={[styles.fab, { bottom: gap }]}>
        <IconButton icon="plus" size={56} fab onPress={() => requireLogin('글쓰기', () => router.push({ pathname: '/community/write', params: { code } }))} />
      </View>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  vote: { marginTop: 14, marginHorizontal: 16, marginBottom: 6 },
  fab: { position: 'absolute', right: 18 },
}));
