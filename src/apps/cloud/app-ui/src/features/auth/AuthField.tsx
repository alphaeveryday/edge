import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View, type TextInputProps } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 로그인·가입 공용 입력칸. 비밀번호는 보기 토글
export function AuthField({ secure, invalid, ...rest }: TextInputProps & { secure?: boolean; invalid?: boolean }) {
  const [shown, setShown] = useState(false);
  return (
    <View style={styles.wrap}>
      <TextInput
        placeholderTextColor={colors.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry={secure && !shown}
        style={[styles.input, secure && { paddingRight: 52 }, invalid && styles.invalid]}
        {...rest}
      />
      {secure && (
        <Pressable onPress={() => setShown((v) => !v)} accessibilityLabel={shown ? '비밀번호 숨기기' : '비밀번호 보기'} style={styles.eye}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={shown ? colors.textSub : colors.textFaint} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
            <Path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
            <Circle cx={12} cy={12} r={3} />
            {shown && <Path d="M4 20L20 4" />}
          </Svg>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { justifyContent: 'center' },
  input: { height: 54, borderRadius: 14, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 16, color: colors.text },
  invalid: { borderWidth: 1.5, borderColor: colors.up },
  eye: { position: 'absolute', right: 6, width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
});
