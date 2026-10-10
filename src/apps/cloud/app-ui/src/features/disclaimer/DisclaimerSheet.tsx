import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useFocusEffect, useIsFocused } from 'expo-router';
import { useCallback, useState } from 'react';
import { Text, View } from 'react-native';
import { api } from '@/api';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useMe } from '@/features/community/queries';
import { useSession } from '@/store/session';
import { createStyles } from '@/theme/theme';
import { fam } from '@/theme/typography';
import { NOTICE_LINES, NOTICE_SUB } from './copy';

// AI 분석·오늘 움직임·탐색 첫 진입 시 한 번 받는 면책 동의
// 회원의 서버 기록과 비회원의 기기 기록, 한 곳의 동의로 전부 해제
// 동의 없이 닫으면 다음 진입에 다시 표시
export function DisclaimerSheet() {
  const qc = useQueryClient();
  const { data: me } = useMe();
  const accept = useMutation({ mutationFn: () => api.member.acceptDisclaimer(), onSuccess: (m) => qc.setQueryData(['member', 'me'], m) });
  const { loggedIn, restored, guestDisclaimed, acceptGuestDisclaimer } = useSession();
  const [dismissed, setDismissed] = useState(false);
  // 탭처럼 띄워 둔 화면의 재진입 시 다시 표시
  useFocusEffect(useCallback(() => setDismissed(false), []));
  // 미리 띄워 둔 탭·아래 깔린 화면의 시트 겹침 방지
  const focused = useIsFocused();
  const open = focused && !dismissed && (loggedIn ? !!me && !me.disclaimerAcceptedAt : restored && !guestDisclaimed);
  return (
    <Notice open={open} onClose={() => setDismissed(true)} onConfirm={() => (loggedIn ? accept.mutate() : acceptGuestDisclaimer())} />
  );
}

// 다시 보기용, 동의 기록 없음
export function DisclaimerInfoSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Notice open={open} onClose={onClose} onConfirm={onClose} />;
}

function Notice({ open, onClose, onConfirm }: { open: boolean; onClose: () => void; onConfirm: () => void }) {
  const styles = useStyles();
  return (
    <BottomSheet open={open} onClose={onClose} head={<SheetHead title="투자 유의 사항" sub={NOTICE_SUB} />}>
      <View style={styles.list}>
        {NOTICE_LINES.map((t) => (
          <View key={t} style={styles.row}>
            <View style={styles.dot} />
            <Text style={styles.text}>{t}</Text>
          </View>
        ))}
      </View>
      <View style={{ marginTop: 20 }}>
        <CtaButton label="확인했어요" tone="dark" onPress={onConfirm} />
      </View>
    </BottomSheet>
  );
}

const useStyles = createStyles((colors) => ({
  list: { gap: 10, marginTop: 4 },
  row: { flexDirection: 'row', gap: 10 },
  dot: { width: 5, height: 5, borderRadius: 999, backgroundColor: colors.text, marginTop: 10 },
  text: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 24, color: colors.textSub },
}));
