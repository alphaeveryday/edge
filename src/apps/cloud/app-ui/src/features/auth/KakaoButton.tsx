import { Pressable, StyleSheet, Text } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { radius } from '@/theme/tokens';
import { type } from '@/theme/typography';

// 카카오 디자인 가이드의 고정 색, 야간 모드에도 불변
const BG = '#FEE500';
const FG = 'rgba(0,0,0,0.85)';

export function KakaoButton({ onPress, disabled }: { onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled}
      style={({ pressed }) => [styles.btn, (pressed || disabled) && { opacity: 0.7 }]}>
      <Svg width={18} height={18} viewBox="0 0 18 18">
        <Path fill="#000" d="M9 1.5C4.58 1.5 1 4.33 1 7.82c0 2.25 1.5 4.22 3.75 5.34l-.8 2.94c-.07.26.22.47.45.32l3.5-2.32c.36.04.73.06 1.1.06 4.42 0 8-2.83 8-6.34S13.42 1.5 9 1.5z" />
      </Svg>
      <Text style={[styles.label, { color: FG }]}>카카오 로그인</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: { height: 54, borderRadius: radius.button, backgroundColor: BG, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, alignSelf: 'stretch' },
  label: type.button,
});
