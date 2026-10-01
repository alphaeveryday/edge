import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { BottomSheet, CtaButton, SectorIcon, SheetHead } from '@/components/ui';
import { useEtfList } from '@/features/etf/queries';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useSetMembers, useWatchList } from './queries';

interface Props {
  open: boolean;
  group: string;
  groupLabel: string;
  onClose: () => void;
}

// 그룹에 넣을 종목 체크 목록
export function GroupEditSheet({ open, group, groupLabel, onClose }: Props) {
  const { data: all } = useEtfList();
  const { data: current } = useWatchList(group);
  const save = useSetMembers();
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => {
    if (open) setPicked((current ?? []).map((e) => e.code));
  }, [open, current]);
  const toggle = (c: string) => setPicked((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
  const done = () => save.mutate({ group, codes: picked }, { onSuccess: onClose });
  return (
    <BottomSheet open={open} onClose={onClose}>
      <SheetHead title={`${groupLabel}에 담을 종목`} />
      <ScrollView style={{ marginTop: 10 }} showsVerticalScrollIndicator={false}>
        {all?.map((e) => {
          const on = picked.includes(e.code);
          return (
            <Pressable key={e.code} onPress={() => toggle(e.code)} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
              <View style={[styles.box, on && styles.boxOn]}>
                {on && (
                  <Svg width={12} height={12} viewBox="0 0 12 12">
                    <Path d="M2.5 6.3l2.2 2.2 4.8-5" stroke={colors.white} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" />
                  </Svg>
                )}
              </View>
              <SectorIcon theme={e.theme} bg={e.logoBg} size={30} />
              <Text numberOfLines={1} style={styles.name}>{e.name}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <View style={{ marginTop: 18 }}>
        <CtaButton label="완료" tone="dark" onPress={done} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: colors.surface },
  box: { width: 22, height: 22, borderRadius: 7, borderWidth: 2, borderColor: colors.line, alignItems: 'center', justifyContent: 'center' },
  boxOn: { borderColor: colors.primary, backgroundColor: colors.primary },
  name: { flex: 1, fontFamily: fam.bold, fontSize: 15, color: colors.text },
});
