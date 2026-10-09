import { useRef } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, radius } from '@/theme/tokens';
import { type } from '@/theme/typography';
import { CtaButton } from './CtaButton';

interface Props {
  open: boolean;
  title: string;
  sub?: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

// 되돌릴 수 없는 결정 하나를 받는 가운데 팝업
export function Dialog({ open, title, sub, confirmLabel, danger, busy, onConfirm, onClose }: Props) {
  // 사라지는 동안의 마지막 문구 유지
  const last = useRef({ title, sub });
  if (open) last.current = { title, sub };
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.root}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.card}>
          <Text style={styles.title}>{last.current.title}</Text>
          {!!last.current.sub && <Text style={styles.sub}>{last.current.sub}</Text>}
          <View style={styles.btns}>
            <CtaButton label="취소" tone="soft" size="sm" grow onPress={onClose} />
            <CtaButton label={confirmLabel} tone={danger ? 'danger' : 'primary'} size="sm" grow disabled={busy} onPress={onConfirm} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim },
  card: { width: '100%', maxWidth: 340, backgroundColor: colors.white, borderRadius: radius.sheet, paddingTop: 24, paddingHorizontal: 20, paddingBottom: 16 },
  title: { ...type.sheetTitle, color: colors.text },
  sub: { ...type.body, color: colors.textMuted, marginTop: 8 },
  btns: { flexDirection: 'row', gap: 8, marginTop: 22 },
});
