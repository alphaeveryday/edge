import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Avatar, CtaButton, IconButton, SectorIcon } from '@/components/ui';
import { useCreatePost, useMe } from '@/features/community/queries';
import { useEtfList } from '@/features/etf/queries';
import { isApiError } from '@/api';
import { useToast } from '@/store/toast';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const short = (n: string) => n.replace(/^(TIGER|KODEX|PLUS|HANARO|SOL)\s*/, '');
const MAX = 280;

export default function CommunityWrite() {
  const router = useRouter();
  const { top, bottom } = useSafeAreaInsets();
  const { data: me } = useMe();
  const { data: etfs } = useEtfList();
  const create = useCreatePost();
  const toast = useToast((s) => s.show);
  const [draft, setDraft] = useState('');
  // '#' 뒤 단어로 종목 태그 제안
  const hashTail = useMemo(() => { const m = draft.match(/#([^\s#]*)$/); return m ? m[1] : null; }, [draft]);
  const suggestions = useMemo(() => (hashTail === null ? [] : (etfs ?? []).filter((e) => short(e.name).includes(hashTail)).slice(0, 3)), [hashTail, etfs]);
  const tags = useMemo(() => (etfs ?? []).filter((e) => draft.includes(`#${short(e.name)}`)).map((e) => e.code), [draft, etfs]);
  const pick = (name: string) => setDraft((d) => d.replace(/#([^\s#]*)$/, `#${short(name)} `));
  const submit = () =>
    create.mutate({ body: draft.trim(), tags }, { onSuccess: () => { router.back(); toast('글을 올렸어요'); }, onError: (e) => toast(isApiError(e) ? e.message : '글을 올리지 못했어요') });
  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={[styles.root, { paddingTop: top + 8 }]}>
      <View style={styles.nav}>
        <IconButton icon="close" onPress={() => router.back()} />
        <Text style={styles.navTitle}>글쓰기</Text>
        <View style={{ width: 84 }}>
          <CtaButton label="게시" tone="dark" size="sm" disabled={!draft.trim() || draft.length > MAX} onPress={submit} />
        </View>
      </View>
      <View style={styles.editor}>
        {me && <Avatar label={me.nick} bg={me.avatarBg} size={38} />}
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder="어떻게 보세요? #을 붙여 종목을 태그할 수 있어요"
          placeholderTextColor={colors.textFaint}
          multiline
          autoFocus
          style={styles.input}
        />
      </View>
      {suggestions.length > 0 && (
        <View style={styles.sug}>
          {suggestions.map((e) => (
            <Pressable key={e.code} onPress={() => pick(e.name)} style={({ pressed }) => [styles.sugRow, pressed && { backgroundColor: colors.card }]}>
              <SectorIcon theme={e.theme} bg={e.logoBg} size={26} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text numberOfLines={1} style={styles.sugName}>#{short(e.name)}</Text>
                <Text numberOfLines={1} style={styles.sugSub}>{e.sub}</Text>
              </View>
            </Pressable>
          ))}
        </View>
      )}
      <View style={{ flex: 1 }} />
      <View style={[styles.foot, { paddingBottom: Math.max(bottom, 16) + 14 }]}>
        <Text style={styles.hint}>{tags.length ? `${tags.length}개 종목 태그` : '#을 붙이면 종목을 태그할 수 있어요 (최대 3개)'}</Text>
        <View style={{ flex: 1 }} />
        <Text style={[styles.count, draft.length > MAX && { color: colors.up }]}>{draft.length}/{MAX}</Text>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  nav: { height: 44, flexDirection: 'row', alignItems: 'center', paddingLeft: 8, paddingRight: 16 },
  navTitle: { flex: 1, textAlign: 'center', fontFamily: fam.bold, fontSize: 17, color: colors.text, letterSpacing: -0.34 },
  editor: { flexDirection: 'row', gap: 11, paddingTop: 12, paddingHorizontal: 16, paddingBottom: 8 },
  input: { flex: 1, minHeight: 150, fontFamily: fam.regular, fontSize: 16, lineHeight: 26, color: colors.text, paddingTop: 7, textAlignVertical: 'top' },
  sug: { marginLeft: 65, marginRight: 16, borderRadius: 14, backgroundColor: colors.white, borderWidth: 1, borderColor: colors.line, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 14, shadowOffset: { width: 0, height: 10 } },
  sugRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, paddingHorizontal: 14, borderBottomWidth: 1, borderBottomColor: colors.surface },
  sugName: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
  sugSub: { fontFamily: fam.regular, fontSize: 12, color: colors.textFaint },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 12, paddingHorizontal: 18, borderTopWidth: 1, borderTopColor: colors.surface },
  hint: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  count: { fontFamily: fam.mono, fontSize: 13, color: colors.textMuted },
});
