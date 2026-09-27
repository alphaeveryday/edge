import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

interface Props {
  like: number;
  reply: number;
  repost?: number;
  liked?: boolean;
  reposted?: boolean;
  size?: 'md' | 'lg';
  onLike?: () => void;
  onReply?: () => void;
  onRepost?: () => void;
}

export function PostActions({ like, reply, repost, liked, reposted, size = 'md', onLike, onReply, onRepost }: Props) {
  const lg = size === 'lg';
  const fs = lg ? 15 : 14;
  const ic = lg ? 20 : 18;
  const likeC = liked ? colors.up : colors.textFaint;
  const rpC = reposted ? colors.primary : colors.textFaint;
  return (
    <View style={styles.row}>
      <Pressable onPress={onLike} hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
        <Svg width={ic} height={ic} viewBox="0 0 24 24">
          <Path d="M12 21s-7-4.6-9.3-9.3C.9 8 3 4.5 6.5 4.5c2 0 3.5 1 4.5 2.6 1-1.6 2.5-2.6 4.5-2.6 3.5 0 5.6 3.5 3.8 7.2C19 16.4 12 21 12 21z" fill={liked ? colors.up : 'none'} stroke={likeC} strokeWidth={1.8} strokeLinejoin="round" />
        </Svg>
        <Text style={[styles.count, { fontSize: fs, color: likeC }]}>{like}</Text>
      </Pressable>
      <Pressable onPress={onReply} hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
        <Svg width={ic} height={ic} viewBox="0 0 24 24">
          <Path d="M4 5.5h16v10H9l-5 4v-4H4z" fill="none" stroke={colors.textFaint} strokeWidth={1.8} strokeLinejoin="round" />
        </Svg>
        <Text style={[styles.count, { fontSize: fs, color: colors.textFaint }]}>{reply}</Text>
      </Pressable>
      {repost !== undefined && (
        <Pressable onPress={onRepost} hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
          <Svg width={ic} height={ic} viewBox="0 0 24 24">
            <Path d="M7 7h9a3 3 0 0 1 3 3v2M17 17H8a3 3 0 0 1-3-3v-2M14 4l3 3-3 3M10 20l-3-3 3-3" fill="none" stroke={rpC} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
          </Svg>
          <Text style={[styles.count, { fontSize: fs, color: rpC }]}>{repost}</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  count: { fontFamily: fam.semibold },
});
