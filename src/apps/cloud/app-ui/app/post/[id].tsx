import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Avatar, BottomBar, Dialog, NavBar, PostActions, SectorIcon } from '@/components/ui';
import { useDeletePost, useMe, usePost, useReplies, useReply, useToggleLike } from '@/features/community/queries';
import { ReportSheet, type ReportTarget } from '@/features/community/ReportSheet';
import { track } from '@/lib/analytics';
import { useRequireLogin } from '@/store/session';
import { useToast } from '@/store/toast';
import { createStyles, useColors } from '@/theme/theme';
import { PAGE_X, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { usePullRefresh } from '@/lib/usePullRefresh';
import { loadMore } from '@/lib/usePages';

export default function Post() {
  const styles = useStyles();
  const colors = useColors();
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: p } = usePost(id);
  const replyQ = useReplies(id);
  const replies = replyQ.data;
  const like = useToggleLike();
  const reply = useReply(id);
  const del = useDeletePost();
  const toast = useToast((s) => s.show);
  const [draft, setDraft] = useState('');
  const [more, setMore] = useState(false);
  const [target, setTarget] = useState<ReportTarget | null>(null);
  const [reveal, setReveal] = useState(false);
  const { data: me } = useMe();
  const requireLogin = useRequireLogin();
  const openReport = (t: ReportTarget) => requireLogin('신고', () => setTarget(t));
  const send = () => {
    const t = draft.trim();
    if (!t) return;
    requireLogin('답글', () => reply.mutate(t, { onSuccess: () => { track('community_reply_created'); setDraft(''); } }));
  };
  const leave = () => (router.canGoBack() ? router.back() : router.replace('/(tabs)/community'));
  const remove = () => del.mutate(id, { onSuccess: () => { setMore(false); leave(); toast('글을 지웠어요'); } });
  const pull = usePullRefresh(loadMore(replyQ));
  return (
    <KeyboardAvoidingView behavior="padding" style={[styles.root, { paddingTop: top + 8 }]}>
      <View style={styles.navWrap}>
        <NavBar title="게시물" onBack={() => router.back()} rightLabel={p?.mine ? '삭제' : '신고'} rightColor={colors.textSub} onRight={() => (p?.mine ? setMore(true) : p && openReport({ type: 'post', id: p.id, handle: p.author.handle, name: p.author.name }))} />
      </View>
      <ScrollView {...pull.scroll} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 20 }} keyboardShouldPersistTaps="handled" disableScrollViewPanResponder={false}>
        {pull.indicator}
        {p?.blocked && !reveal && (
          <View style={[styles.post, styles.cover]}>
            <Text style={styles.coverText}>차단한 사용자의 글이에요</Text>
            <Pressable onPress={() => setReveal(true)} hitSlop={8}><Text style={styles.coverLink}>보기</Text></Pressable>
          </View>
        )}
        {p && (!p.blocked || reveal) && (
          <View style={styles.post}>
            <View style={styles.head}>
              <Avatar label={p.author.name} bg={p.author.avatarBg} size={42} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text numberOfLines={1} style={styles.name}>{p.author.name}</Text>
                <Text style={styles.time}>{p.time}</Text>
              </View>
            </View>
            <Text style={styles.body}>{p.title ? `${p.title}\n${p.body}` : p.body}</Text>
            {!!p.etf.short && (
              <Pressable onPress={() => router.push(`/etf/${p.etf.code}/brief`)} style={({ pressed }) => [styles.tag, pressed && { opacity: 0.6 }]}>
                <SectorIcon theme={p.etf.theme} bg={p.etf.logoBg} size={16} />
                <Text style={styles.tagText}>{p.etf.short}</Text>
              </Pressable>
            )}
            <Text style={styles.views}>조회 {(p.views ?? 0).toLocaleString('ko-KR')}</Text>
            <View style={styles.actions}>
              <PostActions size="lg" like={p.like} reply={p.reply} liked={p.liked} onLike={() => like.mutate(p.id)} />
            </View>
          </View>
        )}
        {replies?.map((r) => (
          <View key={r.id} style={styles.reply}>
            <Avatar label={r.author.name} bg={r.author.avatarBg} size={36} />
            <View style={{ flex: 1, gap: 5 }}>
              <View style={styles.replyHead}>
                <Text numberOfLines={1} style={styles.replyName}>{r.author.name}</Text>
                <Text style={styles.replyTime}>{r.time}</Text>
                <View style={{ flex: 1 }} />
                {r.author.handle !== me?.handle && (
                  <Pressable onPress={() => openReport({ type: 'reply', id: r.id, handle: r.author.handle, name: r.author.name })} hitSlop={10} accessibilityLabel="답글 신고">
                    <Text style={styles.replyMore}>⋯</Text>
                  </Pressable>
                )}
              </View>
              <Text style={styles.replyBody}>{r.body}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
      <BottomBar style={styles.composer}>
        <TextInput value={draft} onChangeText={setDraft} placeholder="답글 쓰기" placeholderTextColor={colors.textFaint} style={styles.input} onSubmitEditing={send} />
        <Pressable onPress={send} disabled={!draft.trim()}>
          <Text style={[styles.send, { color: draft.trim() ? colors.primary : colors.textDisabled }]}>게시</Text>
        </Pressable>
      </BottomBar>
      <ReportSheet target={target} onClose={() => setTarget(null)} onBlocked={() => target?.type === 'post' && leave()} />
      <Dialog open={more} title="이 글을 지울까요?" sub="지운 글은 되돌릴 수 없어요. 태그한 종목 커뮤니티에서도 함께 사라져요." confirmLabel="지우기" danger busy={del.isPending} onConfirm={remove} onClose={() => setMore(false)} />
    </KeyboardAvoidingView>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  navWrap: { borderBottomWidth: 1, borderBottomColor: colors.surface },
  post: { paddingTop: 16, paddingHorizontal: PAGE_X, paddingBottom: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  name: { fontFamily: fam.extrabold, fontSize: 15, color: colors.text, letterSpacing: -0.3 },
  time: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  body: { fontFamily: fam.regular, fontSize: 18, lineHeight: 29, color: colors.text, marginTop: 14 },
  tag: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 14, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 6, paddingHorizontal: 10 },
  tagText: { fontFamily: fam.bold, fontSize: 13, color: colors.textSub },
  views: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginTop: 14 },
  actions: { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.surface },
  reply: { flexDirection: 'row', gap: 11, paddingTop: 14, paddingHorizontal: PAGE_X, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: colors.surface },
  replyHead: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  replyName: { fontFamily: fam.extrabold, fontSize: 14, color: colors.text, maxWidth: 110 },
  replyTime: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  replyMore: { fontFamily: fam.bold, fontSize: 16, color: colors.textFaint, paddingHorizontal: 4 },
  cover: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 22 },
  coverText: { fontFamily: fam.medium, fontSize: 15, color: colors.textMuted },
  coverLink: { fontFamily: fam.bold, fontSize: 15, color: colors.primary },
  replyBody: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.text },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 10, paddingHorizontal: 16, borderTopWidth: 1, borderTopColor: colors.surface, backgroundColor: colors.bg },
  input: { flex: 1, height: 44, backgroundColor: colors.surface, borderRadius: 999, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 15, color: colors.text },
  send: { fontFamily: fam.extrabold, fontSize: 15 },
}));
