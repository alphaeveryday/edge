import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import type { Post } from '@/api';
import { Avatar, PostActions } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useToggleLike } from './queries';

// ETF 커뮤니티·전체 커뮤니티 공용 게시물 카드 (제목·인용 태그·리포스트 포함)
export function EtfPostRow({ post, onQuoteTag }: { post: Post; onQuoteTag?: () => void }) {
  const router = useRouter();
  const like = useToggleLike();
  const open = () => router.push(`/post/${post.id}`);
  return (
    <Pressable onPress={open} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
      {post.repostOf && (
        <View style={styles.rpLine}>
          <Svg width={14} height={14} viewBox="0 0 24 24"><Path d="M7 7h9a3 3 0 0 1 3 3v2M17 17H8a3 3 0 0 1-3-3v-2M14 4l3 3-3 3M10 20l-3-3 3-3" fill="none" stroke={colors.textFaint} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" /></Svg>
          <Text style={styles.rpBy}>{post.author.name}님이 리포스트</Text>
        </View>
      )}
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
      {!!post.title && <Text style={styles.title}>{post.title}</Text>}
      <Text numberOfLines={3} style={styles.body}>{post.body}</Text>
      {post.repostOf && (
        <View style={styles.quote}>
          <View style={styles.quoteHead}>
            <Avatar label={post.repostOf.name} bg={post.repostOf.avatarBg} size={22} />
            <Text style={styles.quoteName}>{post.repostOf.name}</Text>
            <Text style={styles.quoteTime}>{post.repostOf.time}</Text>
          </View>
          <Text numberOfLines={3} style={styles.quoteBody}>{post.repostOf.body}</Text>
        </View>
      )}
      <View style={{ marginTop: 2, alignSelf: 'flex-start' }}>
        <PostActions like={post.like} reply={post.reply} repost={post.repost} liked={post.liked} onLike={() => like.mutate(post.id)} onReply={open} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { gap: 10, paddingTop: 18, paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1, borderBottomColor: colors.surface },
  rpLine: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  rpBy: { fontFamily: fam.semibold, fontSize: 13, color: colors.textFaint },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  name: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3 },
  time: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint },
  quoteTag: { backgroundColor: colors.primarySoft, borderRadius: 8, paddingVertical: 5, paddingHorizontal: 9 },
  quoteTagText: { fontFamily: fam.bold, fontSize: 12, color: colors.primary },
  title: { fontFamily: fam.extrabold, fontSize: 17, lineHeight: 24, letterSpacing: -0.34, color: colors.text },
  body: { fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: '#333D4B' },
  quote: { borderWidth: 1, borderColor: colors.line, borderRadius: 14, paddingVertical: 12, paddingHorizontal: 14, gap: 6 },
  quoteHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  quoteName: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
  quoteTime: { fontFamily: fam.regular, fontSize: 12, color: colors.textFaint },
  quoteBody: { fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: '#333D4B' },
});
