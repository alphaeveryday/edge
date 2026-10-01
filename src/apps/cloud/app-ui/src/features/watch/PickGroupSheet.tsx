import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import type { EtfSummary } from '@/api';
import { BottomSheet, CtaButton, SectorIcon } from '@/components/ui';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { NewGroupForm } from './NewGroupSheet';
import { useMembership, useSetMembership, useWatchGroups } from './queries';

interface Props {
  etf: EtfSummary | null;
  onClose: () => void;
}

// ETF 하나를 어느 관심 그룹에 담을지 고르는 시트
export function PickGroupSheet({ etf, onClose }: Props) {
  const { data: groups } = useWatchGroups();
  const { data: mine } = useMembership(etf?.code ?? '');
  const save = useSetMembership();
  const toast = useToast((s) => s.show);
  const [picked, setPicked] = useState<string[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  useEffect(() => {
    if (etf) setPicked(mine ?? []);
  }, [etf, mine]);
  useEffect(() => {
    if (!etf) setNewOpen(false);
  }, [etf]);
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  const confirm = () =>
    etf &&
    save.mutate({ code: etf.code, groups: picked }, {
      onSuccess: () => {
        onClose();
        toast(picked.length ? '관심에 담았어요' : '관심에서 뺐어요');
      },
    });
  return (
    <BottomSheet open={!!etf} onClose={onClose} padded={false}>
      <View style={styles.head}>
        {etf && <SectorIcon theme={etf.theme} bg={etf.logoBg} size={34} />}
        <Text numberOfLines={1} style={styles.name}>{etf?.name}</Text>
      </View>
      <ScrollView style={styles.list} showsVerticalScrollIndicator={false}>
        {newOpen ? (
          <View style={{ paddingBottom: 15 }}>
            <NewGroupForm onCreated={(g) => { setPicked((p) => [...p, g.key]); setNewOpen(false); }} />
          </View>
        ) : (
          <Pressable onPress={() => setNewOpen(true)} style={({ pressed }) => [styles.newRow, pressed && { opacity: 0.6 }]}>
            <View style={styles.plus}>
              <Svg width={13} height={13} viewBox="0 0 14 14"><Path d="M7 2v10M2 7h10" stroke={colors.primary} strokeWidth={2} strokeLinecap="round" /></Svg>
            </View>
            <Text style={styles.newText}>새 그룹 추가</Text>
          </Pressable>
        )}
        {groups?.map((g) => {
          const on = picked.includes(g.key);
          return (
            <Pressable key={g.key} onPress={() => toggle(g.key)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
              <Svg width={18} height={18} viewBox="0 0 18 18"><Path d="M2 4.5h4.6l1.3 1.6H16v7.4H2z" fill="none" stroke={colors.textFaint} strokeWidth={1.6} strokeLinejoin="round" /></Svg>
              <Text numberOfLines={1} style={styles.gname}>{g.label}</Text>
              <Text style={styles.count}>{g.count}</Text>
              <View style={[styles.ck, on && styles.ckOn]}>
                <Svg width={12} height={12} viewBox="0 0 12 12"><Path d="M2.5 6.3l2.2 2.2 4.8-5" stroke={on ? colors.white : colors.line} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
              </View>
            </Pressable>
          );
        })}
        <View style={{ height: 12 }} />
      </ScrollView>
      <View style={styles.foot}>
        <View style={{ flex: 1 }}><CtaButton label="취소" tone="soft" onPress={onClose} /></View>
        <View style={{ flex: 1.6 }}><CtaButton label="확인" onPress={confirm} /></View>
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingBottom: 6 },
  name: { flex: 1, fontFamily: fam.extrabold, fontSize: 17, color: colors.text, letterSpacing: -0.34 },
  list: { paddingHorizontal: 20 },
  newRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 15 },
  plus: { width: 26, height: 26, borderRadius: 999, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  newText: { fontFamily: fam.bold, fontSize: 15, color: colors.primary },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 15, borderTopWidth: 1, borderTopColor: colors.surface },
  gname: { flex: 1, fontFamily: fam.bold, fontSize: 16, color: colors.text },
  count: { fontFamily: fam.mono, fontSize: 12, color: colors.textFaint },
  ck: { width: 24, height: 24, borderRadius: 999, borderWidth: 1.6, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  ckOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  foot: { flexDirection: 'row', gap: 9, paddingTop: 12, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: colors.surface },
});
