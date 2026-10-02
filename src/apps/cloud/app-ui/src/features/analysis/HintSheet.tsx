import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { IconButton, useBottomGap } from '@/components/ui';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useHint } from './queries';

// 화면 하단에 뜨는 용어와 기준 설명 카드
export function HintSheet({ hintKey, onClose }: { hintKey: string | null; onClose: () => void }) {
  const { data: h } = useHint(hintKey);
  const gap = useBottomGap();
  return (
    <Modal visible={!!hintKey} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      {h && (
        <View style={[styles.card, { bottom: gap }]}>
          <View style={styles.head}>
            <Text style={styles.title}>{h.title}</Text>
            {!!h.orig && <Text style={styles.orig}>{h.orig}</Text>}
            <View style={{ flex: 1 }} />
            <IconButton icon="close" size={32} color={colors.textFaint} onPress={onClose} />
          </View>
          <Text style={styles.body}>{h.body}</Text>
          {h.list && (
            <View style={styles.list}>
              {h.list.map((l) => (
                <View key={l.k} style={styles.item}>
                  <Text style={styles.k}>{l.k}</Text>
                  <Text style={styles.d}>{l.d}</Text>
                </View>
              ))}
            </View>
          )}
          {!!h.why && <Text style={styles.why}>{h.why}</Text>}
        </View>
      )}
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim },
  card: { position: 'absolute', left: 20, right: 20, backgroundColor: colors.white, borderRadius: radius.card, paddingTop: 18, paddingHorizontal: 20, paddingBottom: 20, shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 18, shadowOffset: { width: 0, height: 12 } },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontFamily: fam.extrabold, fontSize: 16, color: colors.text },
  orig: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  body: { fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub, marginTop: 10 },
  list: { gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.line },
  item: { flexDirection: 'row', gap: 10 },
  k: { minWidth: 40, fontFamily: fam.extrabold, fontSize: 14, lineHeight: 22, color: colors.text },
  d: { flex: 1, fontFamily: fam.regular, fontSize: 14, lineHeight: 22, color: colors.textSub },
  why: { fontFamily: fam.regular, fontSize: 13, lineHeight: 21, color: colors.textMuted, marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: colors.line },
});
