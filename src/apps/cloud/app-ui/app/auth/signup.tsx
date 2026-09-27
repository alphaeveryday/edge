import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function Signup() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { themes, etfs } = useOnboarding();
  const { onboarded, login, finishOnboarding } = useSession();
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
  const signup = useMutation({
    mutationFn: () => api.auth.signup({ email: email.trim(), password: pw, nick: nick.trim() }),
    onSuccess: async () => {
      if (!onboarded) await api.onboarding.complete({ themes, etfs });
      login();
      finishOnboarding();
      router.dismissAll();
      router.replace('/(tabs)/home');
      toast('가입을 마쳤어요');
    },
    onError: (e) => setErr(isApiError(e) ? e.message : '가입에 실패했어요'),
  });
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="가입" backIcon="close" onBack={() => router.back()} />
      <PageTitle title="이메일로 가입해요" sub="비밀번호는 8자 이상, 닉네임은 2자 이상이에요." />
      <View style={styles.form}>
        <TextInput value={email} onChangeText={(v) => { setEmail(v); setErr(''); }} placeholder="이메일" placeholderTextColor={colors.textFaint} keyboardType="email-address" autoCapitalize="none" style={styles.input} />
        <TextInput value={pw} onChangeText={setPw} placeholder="비밀번호" placeholderTextColor={colors.textFaint} secureTextEntry style={styles.input} />
        <TextInput value={pw2} onChangeText={setPw2} placeholder="비밀번호 확인" placeholderTextColor={colors.textFaint} secureTextEntry style={styles.input} />
        <TextInput value={nick} onChangeText={setNick} placeholder="닉네임" placeholderTextColor={colors.textFaint} style={styles.input} />
        {(problems[0] || err) ? <Text style={styles.err}>{err || problems[0]}</Text> : null}
        <Text style={styles.terms}>가입하면 이용약관과 개인정보 처리방침에 동의한 것으로 봐요.</Text>
        <CtaButton label="가입하기" tone="dark" disabled={!ready} onPress={() => signup.mutate()} />
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  form: { gap: 12, marginTop: 26, paddingHorizontal: 24 },
  input: { height: 54, borderRadius: 14, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 16, color: colors.text },
  err: { fontFamily: fam.regular, fontSize: 13, color: colors.up, marginTop: -4 },
  terms: { fontFamily: fam.regular, fontSize: 12.5, lineHeight: 19, color: colors.textFaint },
});
