import { Pressable, StyleSheet, Text } from 'react-native';
import { radius } from '@/theme/tokens';
import { type } from '@/theme/typography';

// 카카오 버튼과 같은 크기의 애플 디자인 가이드 직접 구현 버튼
// 시스템 글꼴의 애플 로고 문자(U+F8FF), 야간 모드는 흰 배경
export function AppleButton({ dark, onPress, disabled }: { dark: boolean; onPress: () => void; disabled?: boolean }) {
  const fg = dark ? '#000' : '#fff';
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button" accessibilityLabel="Apple로 로그인"
      style={({ pressed }) => [styles.btn, { backgroundColor: dark ? '#fff' : '#000' }, (pressed || disabled) && { opacity: 0.7 }]}>
      <Text style={[styles.logo, { color: fg }]}>{''}</Text>
      <Text style={[styles.label, { color: fg }]}>Apple로 로그인</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: { height: 54, borderRadius: radius.button, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, alignSelf: 'stretch' },
  logo: { fontSize: 19, marginTop: -2 },
  label: { fontSize: type.button.fontSize, letterSpacing: type.button.letterSpacing, fontWeight: '800' },
});
