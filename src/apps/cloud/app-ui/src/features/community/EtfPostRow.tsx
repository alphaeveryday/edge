import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { Post } from '@/api';
import { Avatar, PostActions, SectorIcon } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useToggleLike } from './queries';

// ETF 커뮤니티와 전체 커뮤니티 공용 게시물 카드
export function EtfPostRow({ post, onQuoteTag, showTag }: { post: Post; onQuoteTag?: () => void; showTag?: boolean }) {
  const router = useRouter();
  const like = useToggleLike();
  const open = () => router.push(`/post/${post.id}`);
  return (
    <Pressable onPress={open} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
      <View style={styles.head}>
        <Avatar label={post.author.name} bg={post.author.avatarBg} size={40} />
        <View style={{ flex: 1, gap: 2 }}>
          <Text numberOfLines={1} style={styles.name}>{post.author.name}</Text>
          <Text style={styles.time}>{post.time}</Text>
        </View>
        {!!post.quoteTag && (
          <Pressable onPress={onQuoteTag} style={styles.quoteTag}>
            <Text style={styles.quoteTagText}>{post.quoteTag}</Text>
          </Pressable>
        )}
      </View>
      {showTag && !!post.etf.short && (
        <View style={styles.tag}>
          <SectorIcon theme={post.etf.theme} bg={post.etf.logoBg} size={14} />
          <Text style={styles.tagText}>{post.etf.short}</Text>
        </View>
      )}
      {!!post.title && <Text style={styles.title}>{post.title}</Text>}
      <Text numberOfLines={3} style={styles.body}>{post.body}</Text>
      <View style={{ marginTop: 2, alignSelf: 'flex-start' }}>
        <PostActions like={post.like} reply={post.reply} liked={post.liked} onLike={() => like.mutate(post.id)} onReply={open} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { gap: 10, paddingTop: 18, paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1, borderBottomColor: colors.surface },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3 },
  time: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  quoteTag: { backgroundColor: colors.primarySoft, borderRadius: 8, paddingVertical: 5, paddingHorizontal: 9 },
  quoteTagText: { fontFamily: fam.bold, fontSize: 12, color: colors.primary },
  tag: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: colors.surface, borderRadius: 8, paddingVertical: 5, paddingHorizontal: 9 },
  tagText: { fontFamily: fam.bold, fontSize: 12, color: colors.textSub },
  title: { fontFamily: fam.extrabold, fontSize: 17, lineHeight: 24, letterSpacing: -0.34, color: colors.text },
  body: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: '#333D4B' },
});
