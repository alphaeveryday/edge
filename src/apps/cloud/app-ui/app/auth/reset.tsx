import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { AuthField } from '@/features/auth/AuthField';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 메일로 받은 6자리 코드와 새 비밀번호 입력. 성공 시 로그인 화면으로
export default function PasswordReset() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [err, setErr] = useState('');
  const [sent, setSent] = useState(false);
  const fail = (fallback: string) => (e: unknown) => setErr(isApiError(e) ? e.message : fallback);
  const req = useMutation({
    mutationFn: () => api.auth.requestPasswordReset(email.trim()),
    onSuccess: () => setSent(true),
    onError: fail('요청에 실패했어요'),
  });
  const confirm = useMutation({
    mutationFn: () => api.auth.confirmPasswordReset(email.trim(), code, pw),
    onSuccess: () => {
      router.dismissTo({ pathname: '/login', params: { email: email.trim() } });
      toast('비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요');
    },
    onError: fail('변경에 실패했어요'),
  });
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  const problem = (pw && pw.length < 8 ? '비밀번호는 8자 이상이에요' : '') || (pw2 && pw !== pw2 ? '비밀번호가 서로 달라요' : '');
  const ready = /^\d{6}$/.test(code) && pw.length >= 8 && pw === pw2;
  const resend = () => { setErr(''); req.mutate(undefined, { onSuccess: () => Alert.alert('코드를 다시 보냈어요', '메일함을 확인해 주세요. 1분 안에는 다시 보낼 수 없어요.') }); };
  return (
    <ScrollView style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="비밀번호 재설정" backIcon="close" onBack={() => router.back()} />
      {sent ? (
        <>
          <PageTitle title="코드를 입력해 주세요" sub={`${email.trim()} 으로 6자리 코드를 보냈어요. 10분 안에 입력해 주세요. 메일이 없으면 스팸함을 확인하고, 1분 뒤 다시 요청할 수 있어요.`} />
          <View style={styles.form}>
            <AuthField value={code} onChangeText={clear((v) => setCode(v.replace(/\D/g, '').slice(0, 6)))} placeholder="6자리 코드" keyboardType="number-pad" textContentType="oneTimeCode" autoComplete="one-time-code" maxLength={6} />
            <AuthField value={pw} onChangeText={clear(setPw)} placeholder="새 비밀번호 (8자 이상)" secure textContentType="newPassword" autoComplete="new-password" />
            <AuthField value={pw2} onChangeText={clear(setPw2)} placeholder="새 비밀번호 확인" secure invalid={!!pw2 && pw !== pw2} textContentType="newPassword" autoComplete="new-password" />
            {!!(err || problem) && <Text style={styles.err}>{err || problem}</Text>}
            <CtaButton label="비밀번호 바꾸기" tone="dark" disabled={!ready || confirm.isPending} onPress={() => confirm.mutate()} />
            <CtaButton label="코드 다시 보내기" tone="soft" disabled={req.isPending} onPress={resend} />
          </View>
        </>
      ) : (
        <>
          <PageTitle title="비밀번호를 다시 정해요" sub="가입한 이메일로 6자리 코드를 보내드려요." />
          <View style={styles.form}>
            <AuthField value={email} onChangeText={clear(setEmail)} placeholder="이메일" keyboardType="email-address" textContentType="emailAddress" autoComplete="email" />
            {!!err && <Text style={styles.err}>{err}</Text>}
            <CtaButton label="코드 보내기" tone="dark" disabled={!email.trim() || req.isPending} onPress={() => req.mutate()} />
          </View>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  form: { gap: 12, marginTop: 26, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
});
