import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Login() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { themes, etfs } = useOnboarding();
  const { onboarded, login, finishOnboarding } = useSession();
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const done = async () => {
    if (!onboarded) await api.onboarding.complete({ themes, etfs });
    login();
    finishOnboarding();
    if (router.canGoBack() && onboarded) router.back();
    else router.replace('/(tabs)/home');
  };
  const social = useMutation({ mutationFn: (p: 'apple' | 'google') => api.auth.social(p), onSuccess: done });
  const emailLogin = useMutation({
    mutationFn: () => api.auth.login(email.trim(), pw),
    onSuccess: done,
    onError: (e) => setErr(isApiError(e) ? e.message : '로그인에 실패했어요'),
  });
  const guest = () => { finishOnboarding(); router.replace('/(tabs)/home'); toast('둘러보기로 시작해요'); };
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="로그인" onBack={() => router.back()} rightLabel={onboarded ? '' : '둘러보기'} rightColor={colors.textSub} onRight={guest} />
      <PageTitle title="로그인하면 관심 종목이 저장돼요" sub="알림·커뮤니티 글쓰기·투표도 로그인 뒤에 열려요." />
      <View style={styles.social}>
        <CtaButton label="Apple로 계속하기" tone="dark" onPress={() => social.mutate('apple')} />
        <CtaButton label="Google로 계속하기" onPress={() => social.mutate('google')} />
      </View>
      <View style={styles.or}>
        <View style={styles.orLine} />
        <Text style={styles.orText}>또는</Text>
        <View style={styles.orLine} />
      </View>
      <View style={styles.form}>
        <TextInput value={email} onChangeText={(v) => { setEmail(v); setErr(''); }} placeholder="이메일" placeholderTextColor={colors.textFaint} keyboardType="email-address" autoCapitalize="none" style={[styles.input, !!err && styles.inputErr]} />
        <TextInput value={pw} onChangeText={(v) => { setPw(v); setErr(''); }} placeholder="비밀번호" placeholderTextColor={colors.textFaint} secureTextEntry style={[styles.input, !!err && styles.inputErr]} onSubmitEditing={() => emailLogin.mutate()} />
        {!!err && <Text style={styles.err}>{err}</Text>}
        <CtaButton label="이메일로 로그인" tone="dark" disabled={!email || !pw} onPress={() => emailLogin.mutate()} />
      </View>
      <View style={styles.links}>
        <Pressable onPress={() => router.push('/auth/reset')}><Text style={styles.link}>비밀번호를 잊었어요</Text></Pressable>
        <Text style={styles.linkSep}>·</Text>
        <Pressable onPress={() => router.push('/auth/signup')}><Text style={styles.link}>이메일로 가입하기</Text></Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  social: { gap: 12, marginTop: 26, paddingHorizontal: 24 },
  or: { flexDirection: 'row', alignItems: 'center', gap: 12, marginVertical: 22, paddingHorizontal: 24 },
  orLine: { flex: 1, height: 1, backgroundColor: colors.line },
  orText: { fontFamily: fam.mono, fontSize: 11, color: colors.textFaint },
  form: { gap: 12, paddingHorizontal: 24 },
  input: { height: 54, borderRadius: 14, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 16, color: colors.text },
  inputErr: { borderColor: colors.up },
  err: { fontFamily: fam.regular, fontSize: 13, color: colors.up, marginTop: -4 },
  links: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 10, marginTop: 22 },
  link: { fontFamily: fam.semibold, fontSize: 14, color: colors.textSub },
  linkSep: { color: colors.textDisabled },
});
