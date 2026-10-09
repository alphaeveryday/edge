import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import type { EtfSummary } from '@/api';
import { BottomSheet, CtaButton, SectorIcon, SheetScrollView } from '@/components/ui';
import { useToast } from '@/store/toast';
import { createStyles, useColors } from '@/theme/theme';
import { fam, type } from '@/theme/typography';
import { CheckCircle } from './CheckCircle';
import { NewGroupForm } from './NewGroupSheet';
import { useGroupMembers, useSetMembership, useWatchGroups } from './queries';

interface Props {
  etfs: EtfSummary[];
  title?: string;
  onClose: () => void;
}

// ETF 를 어느 관심 그룹에 담을지 고르는 시트
// 고른 ETF 전부가 담긴 그룹의 초기 체크
export function PickGroupSheet({ etfs, title, onClose }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const open = etfs.length > 0;
  const { data: groups } = useWatchGroups();
  const members = useGroupMembers();
  const save = useSetMembership();
  const toast = useToast((s) => s.show);
  const [initial, setInitial] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  const ready = !!groups && groups.every((g) => members[g.key]);
  const [inited, setInited] = useState(false);
  useEffect(() => {
    if (!open) {
      setInited(false);
      setNewOpen(false);
    } else if (ready && !inited) {
      const all = groups.filter((g) => etfs.every((e) => members[g.key].has(e.code))).map((g) => g.key);
      setInitial(all);
      setPicked(all);
      setInited(true);
    }
  }, [open, ready, inited, groups, etfs, members]);
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  // 체크를 바꾼 그룹만 반영
  const confirm = () => {
    const items = etfs.map((e) => {
      const had = (groups ?? []).filter((g) => members[g.key]?.has(e.code)).map((g) => g.key);
      const kept = had.filter((k) => !initial.includes(k) || picked.includes(k));
      return { code: e.code, groups: [...new Set([...kept, ...picked])] };
    });
    save.mutate(items, {
      onSuccess: () => {
        onClose();
        toast(items.some((v) => v.groups.length) ? '관심에 담았어요' : '관심에서 뺐어요');
      },
    });
  };
  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      tall
      padded={false}
      head={title ? (
        <Text style={styles.title}>{title}</Text>
      ) : (
        <View style={styles.head}>
          {etfs[0] && <SectorIcon theme={etfs[0].theme} bg={etfs[0].logoBg} size={34} />}
          <Text numberOfLines={1} style={styles.name}>{etfs[0]?.name}</Text>
        </View>
      )}
    >
      <SheetScrollView style={styles.list}>
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
              <CheckCircle on={on} />
            </Pressable>
          );
        })}
        <View style={{ height: 12 }} />
      </SheetScrollView>
      <View style={styles.foot}>
        <View style={{ flex: 1 }}><CtaButton label="취소" tone="soft" onPress={onClose} /></View>
        <View style={{ flex: 1.6 }}><CtaButton label="확인" disabled={!inited || save.isPending} onPress={confirm} /></View>
      </View>
    </BottomSheet>
  );
}

const useStyles = createStyles((colors) => ({
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 20, paddingBottom: 6 },
  name: { flex: 1, fontFamily: fam.extrabold, fontSize: 17, color: colors.text, letterSpacing: -0.34 },
  title: { ...type.sheetTitle, color: colors.text, paddingHorizontal: 20, paddingBottom: 6 },
  list: { paddingHorizontal: 20 },
  newRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 15 },
  plus: { width: 26, height: 26, borderRadius: 999, backgroundColor: colors.primarySoft, alignItems: 'center', justifyContent: 'center' },
  newText: { fontFamily: fam.bold, fontSize: 15, color: colors.primary },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 15, borderTopWidth: 1, borderTopColor: colors.surface },
  gname: { flex: 1, fontFamily: fam.bold, fontSize: 16, color: colors.text },
  count: { fontFamily: fam.mono, fontSize: 12, color: colors.textFaint },
  foot: { flexDirection: 'row', gap: 9, paddingTop: 12, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: colors.surface },
}));
