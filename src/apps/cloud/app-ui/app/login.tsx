import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar } from '@/components/ui';
import { AuthField } from '@/features/auth/AuthField';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 이메일 로그인. 성공 시 원래 화면으로
export default function Login() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const ready = email.trim().length > 0 && pw.length > 0;
  const close = () => (router.canGoBack() ? router.back() : router.replace('/(tabs)/home'));
  const submit = useMutation({
    mutationFn: () => api.auth.login(email.trim(), pw),
    onSuccess: () => { login(); close(); toast('로그인했어요'); },
    onError: (e) => setErr(isApiError(e) ? e.message : '로그인에 실패했어요'),
  });
  const send = () => ready && !submit.isPending && submit.mutate();
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="" onBack={null} rightIcon="close" onRight={close} />
      <Text style={styles.brand}>ETF Orca</Text>
      <View style={styles.form}>
        <AuthField value={email} onChangeText={clear(setEmail)} placeholder="이메일" keyboardType="email-address" textContentType="emailAddress" autoComplete="email" returnKeyType="next" />
        <AuthField value={pw} onChangeText={clear(setPw)} placeholder="비밀번호" secure invalid={!!err} textContentType="password" autoComplete="password" returnKeyType="go" onSubmitEditing={send} />
        {!!err && <Text style={styles.err}>{err}</Text>}
        <View style={{ marginTop: 12 }}>
          <CtaButton label="로그인" tone="dark" disabled={!ready || submit.isPending} onPress={send} />
        </View>
      </View>
      <View style={styles.links}>
        <Pressable onPress={() => router.push('/auth/signup')} hitSlop={8}><Text style={styles.link}>회원가입</Text></Pressable>
        <View style={styles.sep} />
        <Pressable onPress={() => router.push('/auth/reset')} hitSlop={8}><Text style={styles.link}>비밀번호 찾기</Text></Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  brand: { alignSelf: 'center', marginTop: 48, fontFamily: fam.extrabold, fontSize: 32, letterSpacing: -1, color: colors.primary },
  form: { gap: 12, marginTop: 40, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
  links: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 20 },
  link: { fontFamily: fam.semibold, fontSize: 14, color: colors.textSub },
  sep: { width: 1, height: 12, backgroundColor: '#D1D6DB' },
});
