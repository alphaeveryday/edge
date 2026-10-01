import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { Post } from '@/api';
import { Avatar, PostActions, SectorIcon } from '@/components/ui';
import { colors, PAGE_X, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useToggleLike } from './queries';

export function PostRow({ post }: { post: Post }) {
  const router = useRouter();
  const like = useToggleLike();
  const open = () => router.push(`/post/${post.id}`);
  return (
    <Pressable onPress={open} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
      <Avatar label={post.author.name} bg={post.author.avatarBg} size={38} />
      <View style={styles.body}>
        <View style={styles.head}>
          <Text numberOfLines={1} style={styles.name}>{post.author.name}</Text>
          <Text numberOfLines={1} style={styles.handle}>{post.author.handle}</Text>
          <Text style={styles.handle}>· {post.time}</Text>
        </View>
        <View style={styles.tag}>
          <SectorIcon theme={post.etf.theme} bg={post.etf.logoBg} size={14} />
          <Text style={styles.tagText}>{post.etf.short}</Text>
        </View>
        <Text style={styles.text}>{post.body}</Text>
        <View style={styles.actions}>
          <PostActions like={post.like} reply={post.reply} liked={post.liked} onLike={() => like.mutate(post.id)} onReply={open} />
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: 11, paddingTop: 14, paddingBottom: 12, paddingHorizontal: PAGE_X, borderBottomWidth: 1, borderBottomColor: colors.surface },
  body: { flex: 1, gap: 6 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  name: { fontFamily: fam.extrabold, fontSize: 15, color: colors.text, letterSpacing: -0.3, maxWidth: 110 },
  handle: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted, flexShrink: 1 },
  tag: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: colors.surface, borderRadius: radius.tag, paddingVertical: 3, paddingHorizontal: 7 },
  tagText: { fontFamily: fam.bold, fontSize: 12, color: colors.textSub },
  text: { fontFamily: fam.regular, fontSize: 15, lineHeight: 23.7, color: colors.text },
  actions: { marginTop: 2, alignSelf: 'flex-start' },
});
