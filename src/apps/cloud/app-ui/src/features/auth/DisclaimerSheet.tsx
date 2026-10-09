import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { api } from '@/api';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useMe } from '@/features/community/queries';
import { useSession } from '@/store/session';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const LINES = [
  'ETF Orca의 분석은 공개된 뉴스·공시·리포트를 AI가 정리한 참고 자료예요.',
  '매수·매도 권유가 아니며, 투자 판단과 책임은 이용자 본인에게 있어요.',
  '전망은 매일 장 시작 전에, 가격 변동 설명은 가격이 움직일 때 갱신돼요.',
];

// AI 분석 첫 진입 시 한 번 받는 면책 동의
// 회원의 서버 기록과 비회원의 기기 기록
// 동의 없이 닫으면 다음 진입에 다시 표시
export function DisclaimerSheet() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const accept = useMutation({ mutationFn: () => api.member.acceptDisclaimer(), onSuccess: (m) => qc.setQueryData(['member', 'me'], m) });
  const { loggedIn, restored, guestDisclaimed, acceptGuestDisclaimer } = useSession();
  const [dismissed, setDismissed] = useState(false);
  const open = !dismissed && (loggedIn ? !!me && !me.disclaimerAcceptedAt : restored && !guestDisclaimed);
  return (
    <BottomSheet open={open} onClose={() => setDismissed(true)} head={<SheetHead title="투자 유의 사항" sub="AI 분석을 보기 전에 한 번만 확인해 주세요." />}>
      <View style={styles.list}>
        {LINES.map((t) => (
          <View key={t} style={styles.row}>
            <View style={styles.dot} />
            <Text style={styles.text}>{t}</Text>
          </View>
        ))}
      </View>
      <View style={{ marginTop: 20 }}>
        <CtaButton label="확인했어요" tone="dark" onPress={() => (loggedIn ? accept.mutate() : acceptGuestDisclaimer())} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  list: { gap: 10, marginTop: 4 },
  row: { flexDirection: 'row', gap: 10 },
  dot: { width: 5, height: 5, borderRadius: 999, backgroundColor: colors.text, marginTop: 10 },
  text: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.textSub },
});
