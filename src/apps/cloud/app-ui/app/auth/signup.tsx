import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar } from '@/components/ui';
import { AuthField } from '@/features/auth/AuthField';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// 이메일 가입. 로그인 화면에서 진입, 닫기와 성공은 로그인 화면까지 함께 닫고 원래 화면으로
export default function Signup() {
  const router = useRouter();
  const { top, bottom } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [nick, setNick] = useState('');
  const [err, setErr] = useState('');
  const problems = [
    email && !EMAIL.test(email) ? '이메일 형식을 확인해 주세요' : '',
    pw && pw.length < 8 ? '비밀번호는 8자 이상이에요' : '',
    pw2 && pw !== pw2 ? '비밀번호가 서로 달라요' : '',
  ].filter(Boolean);
  const ready = EMAIL.test(email) && pw.length >= 8 && pw === pw2 && nick.trim().length >= 2;
  const leave = () => {
    if (router.canDismiss()) router.dismiss(2);
    else router.replace('/(tabs)/home');
  };
  const submit = useMutation({
    mutationFn: () => api.auth.signup({ email: email.trim(), password: pw, nick: nick.trim() }),
    onSuccess: () => { login(); leave(); toast('가입을 마쳤어요'); },
    onError: (e) => setErr(isApiError(e) ? e.message : '가입에 실패했어요'),
  });
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  const message = err || problems[0];
  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="" onBack={null} rightIcon="close" onRight={leave} />
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 20 }}>
        <Text style={styles.brand}>ETF Orca</Text>
        <View style={styles.form}>
          <AuthField value={email} onChangeText={clear(setEmail)} placeholder="이메일" keyboardType="email-address" textContentType="emailAddress" autoComplete="email" />
          <AuthField value={pw} onChangeText={clear(setPw)} placeholder="비밀번호 (8자 이상)" secure textContentType="newPassword" autoComplete="new-password" />
          <AuthField value={pw2} onChangeText={clear(setPw2)} placeholder="비밀번호 확인" secure invalid={!!pw2 && pw !== pw2} textContentType="newPassword" autoComplete="new-password" />
          <AuthField value={nick} onChangeText={clear(setNick)} placeholder="닉네임 (2자 이상)" textContentType="nickname" autoComplete="nickname" />
          {!!message && <Text style={styles.err}>{message}</Text>}
        </View>
      </ScrollView>
      <View style={[styles.foot, { paddingBottom: Math.max(bottom, 16) + 10 }]}>
        <Text style={styles.terms}>가입하면 이용약관과 개인정보 처리방침에 동의한 것으로 봐요.</Text>
        <CtaButton label="가입하기" tone="dark" disabled={!ready || submit.isPending} onPress={() => submit.mutate()} />
        <View style={styles.toLogin}>
          <Text style={styles.toLoginText}>이미 계정이 있나요?</Text>
          <Pressable onPress={() => router.back()} hitSlop={8}><Text style={styles.toLoginLink}>로그인</Text></Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  brand: { alignSelf: 'center', marginTop: 48, fontFamily: fam.extrabold, fontSize: 32, letterSpacing: -1, color: colors.primary },
  form: { gap: 12, marginTop: 40, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
  foot: { gap: 14, paddingTop: 12, paddingHorizontal: 24 },
  terms: { textAlign: 'center', fontFamily: fam.regular, fontSize: 12.5, lineHeight: 19, color: colors.textMuted },
  toLogin: { flexDirection: 'row', justifyContent: 'center', gap: 6 },
  toLoginText: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  toLoginLink: { fontFamily: fam.bold, fontSize: 14, color: colors.primary },
});
