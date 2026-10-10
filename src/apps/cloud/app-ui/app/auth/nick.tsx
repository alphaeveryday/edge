import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { BackHandler, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { api, isApiError } from '@/api';
import { CtaButton, PageScroll } from '@/components/ui';
import { AuthField } from '@/features/auth/AuthField';
import { useSession } from '@/store/session';
import { useToast } from '@/store/toast';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';

// 소셜 첫 가입의 닉네임 설정, 건너뛰기 없음
// 온보딩 중 가입은 ETF 고르기로, 그 외는 원래 화면 복귀
export default function Nick() {
  const styles = useStyles();
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const onboarded = useSession((s) => s.onboarded);
  const qc = useQueryClient();
  const toast = useToast((s) => s.show);
  const [nick, setNick] = useState('');
  const [err, setErr] = useState('');
  useFocusEffect(useCallback(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []));
  const ready = nick.trim().length >= 2;
  const save = useMutation({
    mutationFn: () => api.member.update({ nick: nick.trim() }),
    onSuccess: (me) => {
      qc.setQueryData(['member', 'me'], me);
      toast('가입을 마쳤어요');
      if (onboarded) {
        if (router.canDismiss()) router.dismiss(2);
        else router.replace('/(tabs)/home');
        return;
      }
      if (router.canDismiss()) router.dismissAll();
      router.push('/onboarding/etf');
    },
    onError: (e) => setErr(isApiError(e) ? e.message : '닉네임을 저장하지 못했어요'),
  });
  const send = () => ready && !save.isPending && save.mutate();
  return (
    <PageScroll style={styles.root} contentContainerStyle={{ paddingTop: top + 56, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>닉네임을 정해 주세요</Text>
      <Text style={styles.sub}>커뮤니티 글과 답글에 보이는 이름이에요.</Text>
      <View style={styles.form}>
        <AuthField value={nick} onChangeText={(v) => { setNick(v); setErr(''); }} placeholder="닉네임 (2자 이상)" textContentType="nickname" autoComplete="nickname" returnKeyType="done" onSubmitEditing={send} />
        {!!err && <Text style={styles.err}>{err}</Text>}
        <View style={{ marginTop: 12 }}>
          <CtaButton label="시작하기" tone="dark" disabled={!ready || save.isPending} onPress={send} />
        </View>
      </View>
    </PageScroll>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  title: { paddingHorizontal: 24, fontFamily: fam.extrabold, fontSize: 24, letterSpacing: -0.6, color: colors.text },
  sub: { paddingHorizontal: 24, marginTop: 8, fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  form: { gap: 12, marginTop: 32, paddingHorizontal: 24 },
  err: { fontFamily: fam.regular, fontSize: 13, lineHeight: 18, color: colors.upDeep },
}));
