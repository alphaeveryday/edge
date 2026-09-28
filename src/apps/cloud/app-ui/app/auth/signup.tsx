import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// 이메일 폼. 가입과 로그인을 한 화면에서 전환
export default function EmailAuth() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const toast = useToast((s) => s.show);
  const [mode, setMode] = useState<'signup' | 'login'>('signup');
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [nick, setNick] = useState('');
  const [err, setErr] = useState('');
  const signup = mode === 'signup';
  const problems = [
    email && !EMAIL.test(email) ? '이메일 형식을 확인해 주세요' : '',
    signup && pw && pw.length < 8 ? '비밀번호는 8자 이상이에요' : '',
    signup && pw2 && pw !== pw2 ? '비밀번호가 서로 달라요' : '',
  ].filter(Boolean);
  const ready = EMAIL.test(email) && (signup ? pw.length >= 8 && pw === pw2 && nick.trim().length >= 2 : pw.length > 0);
  const done = () => {
    login();
    // 폼과 로그인 진입 화면을 함께 닫고 원래 화면으로
    if (router.canDismiss()) router.dismiss(2);
    else router.replace('/(tabs)/home');
    toast(signup ? '가입을 마쳤어요' : '로그인했어요');
  };
  const submit = useMutation({
    mutationFn: () => (signup ? api.auth.signup({ email: email.trim(), password: pw, nick: nick.trim() }) : api.auth.login(email.trim(), pw)),
    onSuccess: done,
    onError: (e) => setErr(isApiError(e) ? e.message : signup ? '가입에 실패했어요' : '로그인에 실패했어요'),
  });
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title={signup ? '가입' : '로그인'} backIcon="close" onBack={() => router.back()} />
      <PageTitle title={signup ? '이메일로 가입해요' : '이메일로 로그인해요'} sub={signup ? '비밀번호는 8자 이상, 닉네임은 2자 이상이에요.' : '가입한 이메일과 비밀번호를 넣어 주세요.'} />
      <View style={styles.form}>
        <TextInput value={email} onChangeText={clear(setEmail)} placeholder="이메일" placeholderTextColor={colors.textFaint} keyboardType="email-address" autoCapitalize="none" style={styles.input} />
        <TextInput value={pw} onChangeText={clear(setPw)} placeholder="비밀번호" placeholderTextColor={colors.textFaint} secureTextEntry style={styles.input} onSubmitEditing={() => !signup && ready && submit.mutate()} />
        {signup && <TextInput value={pw2} onChangeText={clear(setPw2)} placeholder="비밀번호 확인" placeholderTextColor={colors.textFaint} secureTextEntry style={styles.input} />}
        {signup && <TextInput value={nick} onChangeText={clear(setNick)} placeholder="닉네임" placeholderTextColor={colors.textFaint} style={styles.input} />}
        {(problems[0] || err) ? <Text style={styles.err}>{err || problems[0]}</Text> : null}
        {signup && <Text style={styles.terms}>가입하면 이용약관과 개인정보 처리방침에 동의한 것으로 봐요.</Text>}
        <CtaButton label={signup ? '가입하기' : '로그인'} tone="dark" disabled={!ready || submit.isPending} onPress={() => submit.mutate()} />
      </View>
      <View style={styles.links}>
        <Pressable onPress={() => { setMode(signup ? 'login' : 'signup'); setErr(''); }}><Text style={styles.link}>{signup ? '이미 계정이 있어요' : '이메일로 가입하기'}</Text></Pressable>
        {!signup && (
          <>
            <Text style={styles.linkSep}>·</Text>
            <Pressable onPress={() => router.push('/auth/reset')}><Text style={styles.link}>비밀번호를 잊었어요</Text></Pressable>
          </>
        )}
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
  links: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 10, marginTop: 22 },
  link: { fontFamily: fam.semibold, fontSize: 14, color: colors.textSub },
  linkSep: { color: colors.textDisabled },
});
