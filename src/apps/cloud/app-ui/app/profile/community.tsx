import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Avatar, NavBar, PageScroll, PostActions, SectionHead, SectorIcon } from '@/components/ui';
import { useMe, useMyPosts, useToggleLike, useUpdateMe } from '@/features/community/queries';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';
import { loadMore } from '@/lib/usePages';

export default function CommunityProfile() {
  const styles = useStyles();
  const colors = useColors();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: me } = useMe();
  const mine = useMyPosts();
  const posts = mine.data;
  const update = useUpdateMe();
  const like = useToggleLike();
  const [editing, setEditing] = useState(false);
  const [nick, setNick] = useState('');
  useEffect(() => { if (me) setNick(me.nick); }, [me]);
  const toggleEdit = () => {
    if (editing) update.mutate({ nick: nick.trim() || me?.nick });
    setEditing((v) => !v);
  };
  const likes = (posts ?? []).reduce((a, p) => a + p.like, 0);
  const pull = usePullRefresh(loadMore(mine));
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="내 프로필" onBack={() => router.back()} rightLabel={editing ? '완료' : '수정'} onRight={toggleEdit} />
      <PageScroll {...pull.scroll} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }} keyboardShouldPersistTaps="handled" disableScrollViewPanResponder={false}>
        {pull.indicator}
        <View style={styles.head}>
          {me && <Avatar label={nick || me.nick} bg={me.avatarBg} size={84} />}
          {editing ? (
            <View style={{ width: '100%', marginTop: 6 }}>
              <TextInput value={nick} onChangeText={setNick} placeholder="닉네임" placeholderTextColor={colors.textFaint} style={styles.input} />
            </View>
          ) : (
            <Text style={styles.nick}>{me?.nick}</Text>
          )}
          <View style={styles.stats}>
            {[[posts?.length ?? 0, '글'], [likes, '좋아요']].map(([v, l]) => (
              <View key={String(l)} style={{ alignItems: 'center', gap: 2 }}>
                <Text style={styles.statV}>{v}</Text>
                <Text style={styles.statL}>{l}</Text>
              </View>
            ))}
          </View>
        </View>
        <View style={styles.divider} />
        <View style={{ paddingTop: 22 }}><SectionHead title="내가 쓴 글" meta={`${posts?.length ?? 0}개`} /></View>
        {posts?.map((p) => (
          <Pressable key={p.id} onPress={() => router.push(`/post/${p.id}`)} style={({ pressed }) => [styles.post, pressed && { opacity: 0.6 }]}>
            <View style={styles.postHead}>
              {!!p.etf.short && (
                <View style={styles.tag}>
                  <SectorIcon theme={p.etf.theme} bg={p.etf.logoBg} size={14} />
                  <Text style={styles.tagText}>{p.etf.short}</Text>
                </View>
              )}
              <View style={{ flex: 1 }} />
              <Text style={styles.time}>{p.time}</Text>
            </View>
            <Text style={styles.body}>{p.body}</Text>
            <PostActions like={p.like} reply={p.reply} liked={p.liked} onLike={() => like.mutate(p.id)} />
          </Pressable>
        ))}
        {posts && posts.length === 0 && <Text style={styles.empty}>아직 쓴 글이 없어요{'\n'}종목 커뮤니티에서 첫 글을 남겨 보세요</Text>}
      </PageScroll>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  head: { alignItems: 'center', gap: 14, paddingTop: 22, paddingHorizontal: 20, paddingBottom: 6 },
  input: { height: 54, borderRadius: radius.field, backgroundColor: colors.surface, paddingHorizontal: 14, fontFamily: fam.regular, fontSize: 15, color: colors.text },
  nick: { fontFamily: fam.extrabold, fontSize: 19, color: colors.text, letterSpacing: -0.5 },
  stats: { flexDirection: 'row', gap: 28, marginTop: 4 },
  statV: { fontFamily: fam.monoExtraBold, fontSize: 17, color: colors.text },
  statL: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  divider: { height: 10, backgroundColor: colors.surface, marginTop: 20 },
  post: { gap: 8, paddingTop: 14, paddingHorizontal: PAGE_X, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  postHead: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tag: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 3, paddingHorizontal: 7 },
  tagText: { fontFamily: fam.bold, fontSize: 12, color: colors.textSub },
  time: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  body: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.text },
  empty: { textAlign: 'center', fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textMuted, paddingVertical: 40 },
}));
