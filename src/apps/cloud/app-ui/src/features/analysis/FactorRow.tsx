import { Pressable, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import type { Axis, Dir } from '@/api';
import { Sticker } from '@/components/ui';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { dirSignal } from './dir';

// 가장 긴 축 라벨인 매크로 기준의 스티커 폭
const STICKER_W = 72;

interface Props {
  axis: Axis;
  dir: Dir;
  summary: string;
  hasPage: boolean;
  onSelect?: () => void;
  onHint?: () => void;
}

export function FactorRow({ axis, dir, summary, hasPage, onSelect, onHint }: Props) {
  const styles = useStyles();
  const colors = useColors();
  return (
    <Pressable onPress={onSelect} style={({ pressed }) => [styles.row, pressed && hasPage && { opacity: 0.6 }]}>
      <Pressable onPress={onHint} hitSlop={4}>
        <Sticker signal={dirSignal[dir]} size={30} radius={11} label={axis} minWidth={STICKER_W} />
      </Pressable>
      <Text style={styles.summary}>{summary}</Text>
      {hasPage && (
        <Svg width={16} height={16} viewBox="0 0 16 16">
          <Path d="M6 3.5l4.5 4.5L6 12.5" stroke={colors.textDisabled} strokeWidth={1.8} fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </Svg>
      )}
    </Pressable>
  );
}

const useStyles = createStyles((colors) => ({
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 16, borderRadius: radius.card, backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.line },
  summary: { flex: 1, fontFamily: fam.semibold, fontSize: 15, lineHeight: 22, color: colors.text },
}));
