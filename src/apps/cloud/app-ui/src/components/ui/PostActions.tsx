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
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  count: { fontFamily: fam.semibold },
}));
