import { useState } from 'react';
import { TextInput, View } from 'react-native';
import type { WatchGroup } from '@/api';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useCreateGroup } from './queries';

// 관심 탭 시트와 하트 시트 공용의 새 그룹 만들기
export function NewGroupForm({ onCreated }: { onCreated: (g: WatchGroup) => void }) {
  const styles = useStyles();
  const colors = useColors();
  const [name, setName] = useState('');
  const create = useCreateGroup();
  const toast = useToast((s) => s.show);
  const submit = () => {
    if (!name.trim() || create.isPending) return;
    create.mutate(name.trim(), {
      onSuccess: (g) => {
        setName('');
        onCreated(g);
        toast(`${g.label} 그룹을 만들었어요`);
      },
    });
  };
  return (
    <>
      <TextInput value={name} onChangeText={setName} placeholder="예: 연금계좌" placeholderTextColor={colors.textFaint} autoFocus returnKeyType="done" onSubmitEditing={submit} style={styles.input} />
      <View style={{ marginTop: 14 }}>
        <CtaButton label="만들기" tone="dark" disabled={!name.trim()} onPress={submit} />
      </View>
    </>
  );
}

export function NewGroupSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const setGroup = useWatchGroup((s) => s.setGroup);
  return (
    <BottomSheet open={open} onClose={onClose} head={<SheetHead title="새 그룹" />}>
      <NewGroupForm onCreated={(g) => { setGroup(g.key); onClose(); }} />
    </BottomSheet>
  );
}

const useStyles = createStyles((colors) => ({
  input: { marginTop: 4, backgroundColor: colors.surface, borderRadius: radius.field, height: 54, paddingHorizontal: 14, fontFamily: fam.regular, fontSize: 15, color: colors.text },
}));
