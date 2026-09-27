import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function PasswordReset() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const [email, setEmail] = useState('');
  const [err, setErr] = useState('');
  const req = useMutation({
    mutationFn: () => api.auth.requestPasswordReset(email.trim()),
    onError: (e) => setErr(isApiError(e) ? e.message : '요청에 실패했어요'),
  });
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="비밀번호 재설정" backIcon="close" onBack={() => router.back()} />
      {req.isSuccess ? (
        <>
          <PageTitle title="메일을 보냈어요" sub={`${email.trim()} 으로 재설정 링크를 보냈어요. 10분 안에 열어 주세요.`} />
          <View style={styles.form}>
            <CtaButton label="로그인으로 돌아가기" tone="dark" onPress={() => router.back()} />
            <CtaButton label="메일을 다시 보내기" tone="soft" onPress={() => req.mutate()} />
          </View>
        </>
      ) : (
        <>
          <PageTitle title="비밀번호를 다시 정해요" sub="가입한 이메일로 재설정 링크를 보내드려요." />
          <View style={styles.form}>
            <TextInput value={email} onChangeText={(v) => { setEmail(v); setErr(''); }} placeholder="이메일" placeholderTextColor={colors.textFaint} keyboardType="email-address" autoCapitalize="none" style={styles.input} />
            {!!err && <Text style={styles.err}>{err}</Text>}
            <CtaButton label="링크 보내기" tone="dark" disabled={!email.trim()} onPress={() => req.mutate()} />
          </View>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  form: { gap: 12, marginTop: 26, paddingHorizontal: 24 },
  input: { height: 54, borderRadius: 14, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 16, fontFamily: fam.regular, fontSize: 16, color: colors.text },
  err: { fontFamily: fam.regular, fontSize: 13, color: colors.up, marginTop: -4 },
});
