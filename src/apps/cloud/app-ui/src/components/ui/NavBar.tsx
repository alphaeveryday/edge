import { Pressable, StyleSheet, Text, View } from 'react-native';
import { IconButton, type IconName } from './IconButton';
import { colors } from '@/theme/tokens';
import { fam, type } from '@/theme/typography';

interface Props {
  title: string;
  backIcon?: 'back' | 'close';
  onBack?: (() => void) | null;
  rightIcon?: IconName;
  rightBadge?: string | number;
  rightLabel?: string;
  rightColor?: string;
  onRight?: () => void;
}

export function NavBar({ title, backIcon = 'back', onBack, rightIcon, rightBadge, rightLabel, rightColor = colors.primary, onRight }: Props) {
  return (
    <View style={styles.root}>
      {onBack === null ? <View style={styles.spacer} /> : <IconButton icon={backIcon} onPress={onBack} />}
      <Text numberOfLines={1} style={styles.title}>{title}</Text>
      {rightIcon ? (
        <IconButton icon={rightIcon} color={colors.textSub} badge={rightBadge} onPress={onRight} />
      ) : rightLabel ? (
        <Pressable onPress={onRight} style={styles.rightLabel}>
          <Text style={[styles.rightText, { color: rightColor }]}>{rightLabel}</Text>
        </Pressable>
      ) : (
        <View style={styles.spacer} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { height: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 8 },
  spacer: { width: 38 },
  // 좌우 버튼 폭과 무관한 화면 가운데 정렬
  title: { position: 'absolute', left: 88, right: 88, pointerEvents: 'none', textAlign: 'center', ...type.navTitle, color: colors.text },
  rightLabel: { minWidth: 38, paddingVertical: 8, paddingHorizontal: 6, alignItems: 'flex-end' },
  rightText: { fontFamily: fam.bold, fontSize: 13.5 },
});
