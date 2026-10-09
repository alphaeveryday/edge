import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import { Avatar, BottomBar, BottomSheet, CtaButton, IconButton, SectorIcon, SheetHead, SheetScrollView } from '@/components/ui';
import { useCreatePost, useMe } from '@/features/community/queries';
import { useEtfList } from '@/features/etf/queries';
import { useWatchedCodes } from '@/features/watch/queries';
import { isApiError } from '@/api';
import { useToast } from '@/store/toast';
import { createStyles, useColors } from '@/theme/theme';
import { fam } from '@/theme/typography';

const MAX = 280;
const TAG_MAX = 3;

export default function CommunityWrite() {
  const styles = useStyles();
  const colors = useColors();
  const router = useRouter();
  const { code } = useLocalSearchParams<{ code?: string }>();
  const { top } = useSafeAreaInsets();
  const { data: me } = useMe();
  const { data: etfs } = useEtfList();
  const watched = useWatchedCodes();
  const create = useCreatePost();
  const toast = useToast((s) => s.show);
  const [draft, setDraft] = useState('');
  // ETF 화면에서 진입 시 그 ETF 의 사전 태그
  const [tags, setTags] = useState<string[]>(code ? [code] : []);
  const [picking, setPicking] = useState(false);
  const etfOf = (c: string) => etfs?.find((e) => e.code === c);
  const unwatched = watched.ready ? tags.filter((c) => !watched.codes.has(c)) : [];
  const candidates = (etfs ?? []).filter((e) => watched.codes.has(e.code));
  const toggle = (c: string) => setTags((t) => (t.includes(c) ? t.filter((x) => x !== c) : t.length < TAG_MAX ? [...t, c] : t));
  const ready = !!draft.trim() && draft.length <= MAX && tags.length > 0 && unwatched.length === 0;
  const submit = () =>
    create.mutate({ body: draft.trim(), tags }, {
      onSuccess: () => { router.back(); toast('글을 올렸어요'); },
      onError: (e) => toast(isApiError(e) ? e.message : '글을 올리지 못했어요', 'error'),
    });
  const hint = unwatched.length
    ? '관심에 담은 ETF만 태그할 수 있어요'
    : tags.length ? `종목 ${tags.length}개 태그 (최대 ${TAG_MAX}개)` : '종목을 1개 이상 태그해 주세요';
  return (
    <KeyboardAvoidingView behavior="padding" style={[styles.root, { paddingTop: top + 8 }]}>
      <View style={styles.nav}>
        <IconButton icon="close" onPress={() => router.back()} />
        <Text style={styles.navTitle}>글쓰기</Text>
        <View style={{ width: 84 }}>
          <CtaButton label="게시" tone="dark" size="sm" disabled={!ready || create.isPending} onPress={submit} />
        </View>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={styles.tags} keyboardShouldPersistTaps="handled">
        {tags.map((c) => {
          const e = etfOf(c);
          const bad = unwatched.includes(c);
          return (
            <Pressable key={c} accessibilityLabel={`${e?.name ?? c} 태그 빼기`} onPress={() => toggle(c)} style={[styles.tag, bad && styles.tagBad]}>
              {e && <SectorIcon theme={e.theme} bg={e.logoBg} size={18} />}
              <Text numberOfLines={1} style={[styles.tagText, bad && { color: colors.up }]}>{e?.name ?? c}</Text>
              <Svg width={10} height={10} viewBox="0 0 10 10"><Path d="M2 2l6 6M8 2l-6 6" stroke={colors.textFaint} strokeWidth={1.6} strokeLinecap="round" /></Svg>
            </Pressable>
          );
        })}
        {tags.length < TAG_MAX && (
          <Pressable accessibilityLabel="종목 태그 추가" onPress={() => setPicking(true)} style={({ pressed }) => [styles.add, pressed && { opacity: 0.6 }]}>
            <Svg width={11} height={11} viewBox="0 0 14 14"><Path d="M7 2v10M2 7h10" stroke={colors.primary} strokeWidth={2} strokeLinecap="round" /></Svg>
            <Text style={styles.addText}>종목</Text>
          </Pressable>
        )}
      </ScrollView>
      <View style={styles.editor}>
        {me && <Avatar label={me.nick} bg={me.avatarBg} size={38} />}
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder="어떻게 보세요?"
          placeholderTextColor={colors.textFaint}
          multiline
          autoFocus
          style={styles.input}
        />
      </View>
      <View style={{ flex: 1 }} />
      <BottomBar style={styles.foot}>
        <Text style={[styles.hint, unwatched.length > 0 && { color: colors.up }]}>{hint}</Text>
        <View style={{ flex: 1 }} />
        <Text style={[styles.count, draft.length > MAX && { color: colors.up }]}>{draft.length}/{MAX}</Text>
      </BottomBar>
      <BottomSheet open={picking} onClose={() => setPicking(false)} tall head={<SheetHead title="종목 태그" sub={`관심에 담은 ETF 중 최대 ${TAG_MAX}개`} />}>
        <SheetScrollView style={styles.pickList}>
          {candidates.map((e) => {
            const on = tags.includes(e.code);
            return (
              <Pressable key={e.code} onPress={() => toggle(e.code)} style={({ pressed }) => [styles.pickRow, pressed && { opacity: 0.6 }]}>
                <SectorIcon theme={e.theme} bg={e.logoBg} size={30} />
                <Text numberOfLines={1} style={styles.pickName}>{e.name}</Text>
                <View style={[styles.ck, on && styles.ckOn]}>
                  <Svg width={12} height={12} viewBox="0 0 12 12"><Path d="M2.5 6.3l2.2 2.2 4.8-5" stroke={on ? colors.onPrimary : colors.line} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
                </View>
              </Pressable>
            );
          })}
          {watched.ready && candidates.length === 0 && <Text style={styles.empty}>관심에 담은 ETF가 없어요</Text>}
        </SheetScrollView>
        <View style={{ marginTop: 12 }}><CtaButton label="완료" onPress={() => setPicking(false)} /></View>
      </BottomSheet>
    </KeyboardAvoidingView>
  );
}

const useStyles = createStyles((colors) => ({
  root: { flex: 1, backgroundColor: colors.bg },
  nav: { height: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingLeft: 8, paddingRight: 16 },
  navTitle: { position: 'absolute', left: 0, right: 0, pointerEvents: 'none', textAlign: 'center', fontFamily: fam.bold, fontSize: 17, color: colors.text, letterSpacing: -0.34 },
  tags: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 12, paddingHorizontal: 16 },
  tag: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 32, paddingLeft: 8, paddingRight: 10, borderRadius: 999, backgroundColor: colors.card },
  tagBad: { borderWidth: 1, borderColor: colors.up },
  tagText: { fontFamily: fam.bold, fontSize: 13, color: colors.text },
  add: { flexDirection: 'row', alignItems: 'center', gap: 5, height: 32, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: colors.lineStrong },
  addText: { fontFamily: fam.bold, fontSize: 13, color: colors.primary },
  editor: { flexDirection: 'row', gap: 11, paddingTop: 12, paddingHorizontal: 16, paddingBottom: 8 },
  input: { flex: 1, minHeight: 150, fontFamily: fam.regular, fontSize: 16, lineHeight: 26, color: colors.text, paddingTop: 7, textAlignVertical: 'top' },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 12, paddingHorizontal: 18, borderTopWidth: 1, borderTopColor: colors.surface },
  hint: { fontFamily: fam.regular, fontSize: 13, color: colors.textMuted },
  count: { fontFamily: fam.mono, fontSize: 13, color: colors.textFaint },
  pickList: { marginTop: 4 },
  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 13, borderTopWidth: 1, borderTopColor: colors.surface },
  pickName: { flex: 1, fontFamily: fam.bold, fontSize: 15, color: colors.text },
  ck: { width: 24, height: 24, borderRadius: 999, borderWidth: 1.6, borderColor: colors.lineStrong, alignItems: 'center', justifyContent: 'center' },
  ckOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  empty: { paddingVertical: 24, textAlign: 'center', fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
}));
