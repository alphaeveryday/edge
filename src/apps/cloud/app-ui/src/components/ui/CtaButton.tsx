import { Pressable, StyleSheet, Text } from 'react-native';
import { colors, radius } from '@/theme/tokens';
import { type } from '@/theme/typography';

type Tone = 'primary' | 'dark' | 'danger' | 'soft';
const T: Record<Tone | 'disabled', [string, string]> = {
  primary: [colors.primary, colors.white],
  dark: [colors.text, colors.white],
  danger: [colors.up, colors.white],
  soft: [colors.surface, colors.textSub],
  disabled: [colors.surface, colors.textDisabled],
};

interface Props {
  label: string;
  tone?: Tone;
  size?: 'md' | 'sm';
  disabled?: boolean;
  grow?: boolean;
  onPress?: () => void;
}

export function CtaButton({ label, tone = 'primary', size = 'md', disabled, grow, onPress }: Props) {
  const [bg, fg] = T[disabled ? 'disabled' : tone];
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      style={({ pressed }) => [styles.btn, { height: size === 'sm' ? 46 : 54, backgroundColor: bg, flex: grow ? 1 : undefined }, pressed && !disabled && { transform: [{ scale: 0.985 }] }]}
    >
      <Text style={[styles.label, { color: fg }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: { borderRadius: radius.button, alignItems: 'center', justifyContent: 'center', alignSelf: 'stretch' },
  label: type.button,
});
