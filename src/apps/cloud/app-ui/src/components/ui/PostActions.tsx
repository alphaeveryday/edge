import { Pressable, Text, View } from 'react-native';
import { Icon } from './Icon';
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
        <Icon name="heart" color={likeC} fill={liked ? colors.up : 'none'} size={ic} />
        <Text style={[styles.count, { fontSize: fs, color: likeC }]}>{like}</Text>
      </Pressable>
      <Pressable onPress={onReply} hitSlop={6} style={({ pressed }) => [styles.item, pressed && { opacity: 0.6 }]}>
        <Icon name="message-circle" color={colors.textFaint} size={ic} />
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
