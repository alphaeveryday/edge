import { useState } from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { BottomSheet, CtaButton, SheetHead } from '@/components/ui';
import { useToast } from '@/store/toast';
import { useWatchGroup } from '@/store/watch';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useCreateGroup } from './queries';

export function NewGroupSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const create = useCreateGroup();
  const setGroup = useWatchGroup((s) => s.setGroup);
  const toast = useToast((s) => s.show);
  const submit = () =>
    create.mutate(name.trim(), {
      onSuccess: (g) => {
        setGroup(g.key);
        setName('');
        onClose();
        toast(`${g.label} 그룹을 만들었어요`);
      },
    });
  return (
    <BottomSheet open={open} onClose={onClose}>
      <SheetHead title="새 그룹" />
      <TextInput value={name} onChangeText={setName} placeholder="예: 연금계좌" placeholderTextColor={colors.textFaint} autoFocus style={styles.input} />
      <View style={{ marginTop: 14 }}>
        <CtaButton label="만들기" tone="dark" disabled={!name.trim()} onPress={submit} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  input: { marginTop: 16, backgroundColor: colors.surface, borderRadius: 12, padding: 14, fontFamily: fam.regular, fontSize: 15, color: colors.text },
});
