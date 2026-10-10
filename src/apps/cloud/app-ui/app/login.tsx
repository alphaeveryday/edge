import { useMutation } from '@tanstack/react-query';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, NavBar, PageScroll } from '@/components/ui';
import { AuthField } from '@/features/auth/AuthField';
import { track } from '@/lib/analytics';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';

// 로그인이 필요한 동작에서 들어왔을 때의 안내
const REASON: Record<string, string> = {
  투표: '투표하려면 로그인이 필요해요',
  글쓰기: '글을 쓰려면 로그인이 필요해요',
  답글: '답글을 달려면 로그인이 필요해요',
  좋아요: '좋아요를 누르려면 로그인이 필요해요',
  신고: '신고하려면 로그인이 필요해요',
  만료: '로그인이 풀렸어요. 다시 로그인해 주세요',
};
const REASON_KEY: Record<string, string> = { 투표: 'vote', 글쓰기: 'write', 답글: 'reply', 좋아요: 'like', 신고: 'report', 만료: 'expired', 온보딩: 'onboarding' };

// 이메일 로그인
// 성공 시 원래 화면 복귀
export default function Login() {
  const styles = useStyles();
  const router = useRouter();
  const { reason, email: resetEmail } = useLocalSearchParams<{ reason?: string; email?: string }>();
  const hint = reason ? REASON[reason] : undefined;
  useEffect(() => { track('login_prompt_shown', { reason: (reason && REASON_KEY[reason]) ?? 'direct' }); }, [reason]);
  const { top } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const onboarded = useSession((s) => s.onboarded);
  const finishOnboarding = useSession((s) => s.finishOnboarding);
  const toast = useToast((s) => s.show);
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  // 비밀번호 재설정 후 돌아올 때 이메일 채움
  useEffect(() => {
    if (resetEmail) { setEmail(resetEmail); setPw(''); }
  }, [resetEmail]);
  const ready = email.trim().length > 0 && pw.length > 0;
  const close = () => (router.canGoBack() ? router.back() : router.replace('/(tabs)/home'));
  // 온보딩 중 로그인은 고르기 생략의 홈 이동
  const done = () => {
    if (onboarded) return close();
    finishOnboarding();
    if (router.canDismiss()) router.dismissAll();
    router.replace('/(tabs)/home');
  };
  const submit = useMutation({
    mutationFn: () => api.auth.login(email.trim(), pw),
    onSuccess: () => { track('login_completed'); login(); done(); toast('로그인했어요'); },
    onError: (e) => setErr(isApiError(e) ? e.message : '로그인에 실패했어요'),
  });
  const send = () => ready && !submit.isPending && submit.mutate();
  const clear = (fn: (v: string) => void) => (v: string) => { fn(v); setErr(''); };
  return (
    <PageScroll style={styles.root} contentContainerStyle={{ paddingTop: top + 8, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <NavBar title="" onBack={null} rightIcon="close" onRight={close} />
      <Text style={styles.brand}>ETF Orca</Text>
      {!!hint && <Text style={styles.hint}>{hint}</Text>}
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
    </PageScroll>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  brand: { alignSelf: 'center', marginTop: 48, fontFamily: fam.extrabold, fontSize: 32, letterSpacing: -1, color: colors.primary },
  hint: { alignSelf: 'center', marginTop: 10, fontFamily: fam.medium, fontSize: 14, color: colors.textMuted },
  form: { gap: 12, marginTop: 40, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
  links: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 20 },
  link: { fontFamily: fam.semibold, fontSize: 14, color: colors.textSub },
  sep: { width: 1, height: 12, backgroundColor: colors.lineStrong },
}));
