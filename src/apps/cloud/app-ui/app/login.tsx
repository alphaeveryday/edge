import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Login() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { themes, etfs } = useOnboarding();
  const { login, finishOnboarding } = useSession();
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const done = useMutation({
    mutationFn: () => api.onboarding.complete({ themes, etfs }),
    onSuccess: () => {
      login();
      finishOnboarding();
      router.replace('/(tabs)/home');
    },
  });
  const go = () => done.mutate();
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="로그인" onBack={() => router.back()} />
      <PageTitle title="로그인하면 관심 종목이 저장돼요" sub="알림·커뮤니티 글쓰기·투표도 로그인 뒤에 열려요." />
      <View style={styles.social}>
        <CtaButton label="Apple로 계속하기" tone="dark" onPress={go} />
        <CtaButton label="Google로 계속하기" onPress={go} />
      </View>
      <View style={styles.or}>
        <View style={styles.orLine} />
        <Text style={styles.orText}>또는</Text>
        <View style={styles.orLine} />
      </View>
      <View style={styles.form}>
        <TextInput value={email} onChangeText={setEmail} placeholder="이메일" placeholderTextColor={colors.textFaint} keyboardType="email-address" autoCapitalize="none" style={styles.input} />
        <TextInput value={pw} onChangeText={setPw} placeholder="비밀번호" placeholderTextColor={colors.textFaint} secureTextEntry style={styles.input} />
        <CtaButton label="이메일로 로그인" tone="dark" disabled={!email || !pw} onPress={go} />
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
});
