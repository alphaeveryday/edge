import { useMutation } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api } from '@/api';
import { CtaButton, NavBar, PageTitle } from '@/components/ui';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

// 소셜 둘과 이메일 진입 버튼의 로그인 첫 화면
export default function Login() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const login = useSession((s) => s.login);
  const toast = useToast((s) => s.show);
  const done = () => {
    login();
    if (router.canGoBack()) router.back();
    else router.replace('/(tabs)/home');
    toast('로그인했어요');
  };
  const social = useMutation({ mutationFn: (p: 'apple' | 'google') => api.auth.social(p), onSuccess: done, onError: () => toast('로그인에 실패했어요') });
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="로그인" onBack={() => router.back()} />
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
      <View style={styles.social}>
        <CtaButton label="이메일로 계속하기" tone="soft" onPress={() => router.push('/auth/signup')} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  social: { gap: 12, marginTop: 26, paddingHorizontal: 24 },
  or: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 22, paddingHorizontal: 24 },
  orLine: { flex: 1, height: 1, backgroundColor: colors.line },
  orText: { fontFamily: fam.mono, fontSize: 11, color: colors.textFaint },
});
