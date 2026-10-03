import { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { ReportReason } from '@/api';
import { BottomSheet, CtaButton, ListRow, SheetHead } from '@/components/ui';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { useBlock, useReport } from './queries';

const REASONS: { key: ReportReason; label: string }[] = [
  { key: 'spam', label: '스팸·홍보' },
  { key: 'abuse', label: '욕설·비하' },
  { key: 'sexual', label: '음란물' },
  { key: 'scam', label: '사기·투자 유도' },
  { key: 'etc', label: '기타' },
];

export interface ReportTarget {
  type: 'post' | 'reply';
  id: string;
  handle: string;
  name: string;
}

// 신고 사유 5개와 작성자 차단
// 확인 단계를 거치는 차단
export function ReportSheet({ target, onClose, onBlocked }: { target: ReportTarget | null; onClose: () => void; onBlocked?: () => void }) {
  const toast = useToast((s) => s.show);
  const report = useReport();
  const block = useBlock();
  const [confirm, setConfirm] = useState(false);
  const close = () => { setConfirm(false); onClose(); };
  const send = (reason: ReportReason) => {
    if (!target) return;
    report.mutate({ target, reason }, {
      onSuccess: () => { close(); toast('신고를 접수했어요. 24시간 안에 확인할게요'); },
      onError: () => toast('신고에 실패했어요', 'error'),
    });
  };
  const doBlock = () => {
    if (!target) return;
    block.mutate(target.handle, {
      onSuccess: () => { close(); onBlocked?.(); toast('차단했어요'); },
      onError: () => toast('차단에 실패했어요', 'error'),
    });
  };
  return (
    <BottomSheet open={!!target} onClose={close}>
      {confirm ? (
        <>
          <SheetHead title={`${target?.name} 님을 차단할까요?`} sub="이 사용자의 글과 답글이 목록에서 보이지 않고, 이 사용자의 답글 알림도 오지 않아요." />
          <View style={styles.btns}>
            <View style={{ flex: 1 }}><CtaButton label="취소" tone="soft" onPress={() => setConfirm(false)} /></View>
            <View style={{ flex: 1.6 }}><CtaButton label="차단하기" tone="danger" disabled={block.isPending} onPress={doBlock} /></View>
          </View>
        </>
      ) : (
        <>
          <SheetHead title="신고 사유를 골라 주세요" onClose={close} />
          <View style={styles.list}>
            {REASONS.map((r) => <ListRow key={r.key} label={r.label} chevron={false} divider onPress={() => !report.isPending && send(r.key)} />)}
            <ListRow label="이 사용자 차단" labelColor={colors.upDeep} chevron={false} onPress={() => setConfirm(true)} />
          </View>
        </>
      )}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  list: { marginTop: 8 },
  btns: { flexDirection: 'row', gap: 8, marginTop: 18 },
});
