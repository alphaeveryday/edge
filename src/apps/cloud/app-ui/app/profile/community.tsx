import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Avatar, NavBar, PostActions, SectionHead, SectorIcon } from '@/components/ui';
import { useMe, useMyPosts, useToggleLike, useUpdateMe } from '@/features/community/queries';
import { colors, PAGE_X, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const COLORS = [colors.primary, colors.up, colors.down, colors.positive, colors.warn];

export default function CommunityProfile() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: me } = useMe();
  const { data: posts } = useMyPosts();
  const update = useUpdateMe();
  const like = useToggleLike();
  const [editing, setEditing] = useState(false);
  const [nick, setNick] = useState('');
  const [handle, setHandle] = useState('');
  const [bg, setBg] = useState('');
  useEffect(() => { if (me) { setNick(me.nick); setHandle(me.handle); setBg(me.avatarBg); } }, [me]);
  const toggleEdit = () => {
    if (editing) update.mutate({ nick: nick.trim() || me?.nick, handle: handle.trim() || me?.handle, avatarBg: bg });
    setEditing((v) => !v);
  };
  const likes = (posts ?? []).reduce((a, p) => a + p.like, 0);
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="내 프로필" onBack={() => router.back()} rightLabel={editing ? '완료' : '수정'} onRight={toggleEdit} />
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
        <View style={styles.head}>
          {me && <Avatar label={nick || me.nick} bg={bg || me.avatarBg} size={84} />}
          {editing ? (
            <>
              <View style={styles.swatches}>
                {COLORS.map((c) => <Pressable key={c} onPress={() => setBg(c)} style={[styles.swatch, { backgroundColor: c }, bg === c && styles.swatchOn]} />)}
              </View>
              <View style={{ width: '100%', gap: 8, marginTop: 6 }}>
                <TextInput value={nick} onChangeText={setNick} placeholder="닉네임" placeholderTextColor={colors.textFaint} style={styles.input} />
                <TextInput value={handle} onChangeText={setHandle} placeholder="@아이디" placeholderTextColor={colors.textFaint} autoCapitalize="none" style={[styles.input, { fontFamily: fam.mono }]} />
              </View>
            </>
          ) : (
            <View style={{ alignItems: 'center', gap: 4 }}>
              <Text style={styles.nick}>{me?.nick}</Text>
              <Text style={styles.handle}>{me?.handle}</Text>
            </View>
          )}
          <View style={styles.stats}>
            {[[posts?.length ?? 0, '글'], [likes, '좋아요'], [1, '투표']].map(([v, l]) => (
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
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  head: { alignItems: 'center', gap: 14, paddingTop: 22, paddingHorizontal: 20, paddingBottom: 6 },
  swatches: { flexDirection: 'row', gap: 8 },
  swatch: { width: 28, height: 28, borderRadius: 999 },
  swatchOn: { borderWidth: 3, borderColor: colors.white, shadowColor: colors.text, shadowOpacity: 1, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } },
  input: { height: 54, borderRadius: radius.field, backgroundColor: colors.surface, paddingHorizontal: 14, fontFamily: fam.regular, fontSize: 15, color: colors.text },
  nick: { fontFamily: fam.extrabold, fontSize: 19, color: colors.text, letterSpacing: -0.5 },
  handle: { fontFamily: fam.mono, fontSize: 13, color: colors.textMuted },
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
});
