import { Pressable, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

interface Props {
  like: number;
  reply: number;
  liked?: boolean;
  size?: 'md' | 'lg';
  onLike?: () => void;
  onReply?: () => void;
}

export function PostActions({ like, reply, liked, size = 'md', onLike, onReply }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const lg = size === 'lg';
  const fs = lg ? 15 : 14;
  const ic = lg ? 20 : 18;
  const likeC = liked ? colors.up : colors.textFaint;
  return (
    <View style={styles.row}>
      <Pressable onPress={onLike} accessibilityRole="button" accessibilityLabel="좋아요" hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
        <Svg width={ic} height={ic} viewBox="0 0 24 24">
          <Path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" fill={liked ? colors.up : 'none'} stroke={likeC} strokeWidth={1.8} strokeLinejoin="round" />
        </Svg>
        <Text style={[styles.count, { fontSize: fs, color: likeC }]}>{like}</Text>
      </Pressable>
      <Pressable onPress={onReply} hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
        <Svg width={ic} height={ic} viewBox="0 0 24 24">
          <Path d="M4 5.5h16v10H9l-5 4v-4H4z" fill="none" stroke={colors.textFaint} strokeWidth={1.8} strokeLinejoin="round" />
        </Svg>
        <Text style={[styles.count, { fontSize: fs, color: colors.textFaint }]}>{reply}</Text>
      </Pressable>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  count: { fontFamily: fam.semibold },
}));
