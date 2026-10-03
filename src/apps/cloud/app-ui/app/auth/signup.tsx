import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { BottomBar, CtaButton, NavBar } from '@/components/ui';
import { openPrivacy, openTerms } from '@/lib/links';
import { AuthField } from '@/features/auth/AuthField';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// 이메일 인증 코드 확인 후 가입
// 닫기와 성공 시 로그인 화면까지 함께 닫는 원래 화면 복귀
export default function Signup() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [nick, setNick] = useState('');
  const [err, setErr] = useState('');
  const problems = [
    email && !EMAIL.test(email) ? '이메일 형식을 확인해 주세요' : '',
    pw && pw.length < 8 ? '비밀번호는 8자 이상이에요' : '',
    pw2 && pw !== pw2 ? '비밀번호가 서로 달라요' : '',
  ].filter(Boolean);
  const ready = sent && /^\d{6}$/.test(code) && EMAIL.test(email) && pw.length >= 8 && pw === pw2 && nick.trim().length >= 2;
  const leave = () => {
    if (router.canDismiss()) router.dismiss(2);
    else router.replace('/(tabs)/home');
  };
  const submit = useMutation({
    mutationFn: () => api.auth.signup({ email: email.trim(), password: pw, nick: nick.trim(), code }),
    onSuccess: () => { login(); leave(); toast('가입을 마쳤어요'); },
    onError: (e) => setErr(isApiError(e) ? e.message : '가입에 실패했어요'),
  });
  const sendCode = useMutation({
    mutationFn: () => api.auth.sendSignupCode(email.trim()),
    onSuccess: () => {
      if (sent) Alert.alert('코드를 다시 보냈어요', '메일함을 확인해 주세요. 1분 안에는 다시 보낼 수 없어요.');
      setSent(true);
    },
    onError: (e) => setErr(isApiError(e) ? e.message : '코드를 보내지 못했어요'),
  });
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  // 인증한 이메일 변경 시 코드 발송부터 재시작
  const changeEmail = (v: string) => { setEmail(v); setErr(''); setSent(false); setCode(''); };
  const message = err || problems[0];
  return (
    <KeyboardAvoidingView behavior="padding" style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="" onBack={null} rightIcon="close" onRight={leave} />
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 20 }}>
        <Text style={styles.brand}>ETF Orca</Text>
        <View style={styles.form}>
          <View style={styles.emailRow}>
            <View style={{ flex: 1 }}>
              <AuthField value={email} onChangeText={changeEmail} placeholder="이메일" keyboardType="email-address" textContentType="emailAddress" autoComplete="email" />
            </View>
            <View style={{ width: 112 }}>
              <CtaButton label={sent ? '다시 보내기' : '인증 코드 받기'} tone="soft" disabled={!EMAIL.test(email) || sendCode.isPending} onPress={() => sendCode.mutate()} />
            </View>
          </View>
          {sent && (
            <>
              <Text style={styles.hint}>{`${email.trim()} 으로 6자리 코드를 보냈어요. 10분 안에 입력해 주세요.`}</Text>
              <AuthField value={code} onChangeText={clear((v) => setCode(v.replace(/\D/g, '').slice(0, 6)))} placeholder="인증 코드 6자리" keyboardType="number-pad" textContentType="oneTimeCode" autoComplete="one-time-code" maxLength={6} />
            </>
          )}
          <AuthField value={pw} onChangeText={clear(setPw)} placeholder="비밀번호 (8자 이상)" secure textContentType="newPassword" autoComplete="new-password" />
          <AuthField value={pw2} onChangeText={clear(setPw2)} placeholder="비밀번호 확인" secure invalid={!!pw2 && pw !== pw2} textContentType="newPassword" autoComplete="new-password" />
          <AuthField value={nick} onChangeText={clear(setNick)} placeholder="닉네임 (2자 이상)" textContentType="nickname" autoComplete="nickname" />
          {!!message && <Text style={styles.err}>{message}</Text>}
        </View>
      </ScrollView>
      <BottomBar style={styles.foot}>
        <Text style={styles.terms}>가입하면 <Text style={styles.termsLink} onPress={openTerms}>이용약관</Text>과 <Text style={styles.termsLink} onPress={openPrivacy}>개인정보 처리방침</Text>에 동의한 것으로 봐요.</Text>
        <CtaButton label="가입하기" tone="dark" disabled={!ready || submit.isPending} onPress={() => submit.mutate()} />
        <View style={styles.toLogin}>
          <Text style={styles.toLoginText}>이미 계정이 있나요?</Text>
          <Pressable onPress={() => router.back()} hitSlop={8}><Text style={styles.toLoginLink}>로그인</Text></Pressable>
        </View>
      </BottomBar>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  brand: { alignSelf: 'center', marginTop: 48, fontFamily: fam.extrabold, fontSize: 32, letterSpacing: -1, color: colors.primary },
  form: { gap: 12, marginTop: 40, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
  emailRow: { flexDirection: 'row', gap: 8 },
  hint: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.textMuted },
  foot: { gap: 14, paddingTop: 12, paddingHorizontal: 24, borderTopWidth: 1, borderTopColor: colors.surface },
  terms: { textAlign: 'center', fontFamily: fam.regular, fontSize: 12.5, lineHeight: 19, color: colors.textMuted },
  termsLink: { fontFamily: fam.semibold, color: colors.textSub, textDecorationLine: 'underline' },
  toLogin: { flexDirection: 'row', justifyContent: 'center', gap: 6 },
  toLoginText: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  toLoginLink: { fontFamily: fam.bold, fontSize: 14, color: colors.primary },
});
