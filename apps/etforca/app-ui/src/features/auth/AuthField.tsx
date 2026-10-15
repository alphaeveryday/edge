import { useState } from 'react';
import { Pressable, TextInput, View, type TextInputProps } from 'react-native';
import { Icon } from '@/components/ui/Icon';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 로그인·가입 공용 입력칸
// 비밀번호의 보기 토글
export function AuthField({ secure, invalid, ...rest }: TextInputProps & { secure?: boolean; invalid?: boolean }) {
  const styles = useStyles();
  const colors = useColors();
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
          <Icon name={shown ? 'eye-off' : 'eye'} color={shown ? colors.textSub : colors.textFaint} size={20} />
        </Pressable>
      )}
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  wrap: { justifyContent: 'center' },
  input: { height: 54, borderRadius: radius.field, borderWidth: 1, borderColor: colors.lineStrong, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 16, color: colors.text },
  invalid: { borderWidth: 1.5, borderColor: colors.up },
  eye: { position: 'absolute', right: 6, width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
}));
