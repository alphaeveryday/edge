import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { Avatar, BottomSheet, Chevron, CtaButton, ListRow, NavBar, SheetHead, ToggleRow } from '@/components/ui';
import { useMe } from '@/features/community/queries';
import { useOnboarding } from '@/store/onboarding';
import { useSession } from '@/store/session';
import { openPrivacy, openTerms } from '@/lib/links';
import { useToast } from '@/store/toast';
import { colors, radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';

export default function Profile() {
  const router = useRouter();
  const { top } = useSafeAreaInsets();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const logout = useSession((s) => s.logout);
  const resetOnboarding = useOnboarding((s) => s.reset);
  const toast = useToast((s) => s.show);
  // 로그아웃과 탈퇴 공통의 온보딩 복귀
  const leave = (msg: string) => { qc.clear(); logout(); resetOnboarding(); if (router.canDismiss()) router.dismissAll();
    router.replace('/onboarding/how'); toast(msg); };
  const [notif, setNotif] = useState(true);
  const [delOpen, setDelOpen] = useState(false);
  const del = useMutation({
    mutationFn: () => api.member.deleteAccount(),
    onSuccess: () => { setDelOpen(false); leave('계정을 지웠어요'); },
  });
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <NavBar title="계정" onBack={() => router.back()} />
      <ScrollView showsVerticalScrollIndicator={false}>
        <Pressable onPress={() => router.push('/profile/community')} style={({ pressed }) => [styles.me, pressed && { opacity: 0.6 }]}>
          {me && <Avatar label={me.nick} bg={me.avatarBg} size={52} />}
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{me?.nick}</Text>
            <Text style={styles.email}>{me?.email}</Text>
          </View>
          <Chevron size={16} color={colors.textDisabled} />
        </Pressable>
        <Text style={styles.cap}>알림</Text>
        <View style={styles.card}>
          <ToggleRow label="관심 ETF 온도 변화 알림" sub="온도가 바뀌는 순간에만 알려드려요" on={notif} onToggle={() => setNotif((v) => !v)} />
        </View>
        <Text style={styles.cap}>계정</Text>
        <View style={[styles.card, { paddingHorizontal: 8 }]}>
          <ListRow label="이용약관" divider onPress={openTerms} />
          <ListRow label="개인정보 처리방침" divider onPress={openPrivacy} />
          <ListRow label="회원 탈퇴" labelColor={colors.textMuted} onPress={() => setDelOpen(true)} />
        </View>
        <Pressable onPress={() => { api.auth.logout(); leave('로그아웃했어요'); }} style={styles.logout}>
          <Text style={styles.logoutText}>로그아웃</Text>
        </Pressable>
        <Text style={styles.version}>ETF Orca v0.1.0</Text>
      </ScrollView>
      <BottomSheet open={delOpen} onClose={() => setDelOpen(false)}>
        <SheetHead title="정말 탈퇴할까요?" sub="관심 종목과 투표 기록은 지워지고, 쓴 글과 답글은 '탈퇴한 사용자'로 남아요." />
        <View style={{ flexDirection: 'row', gap: 8, marginTop: 20 }}>
          <View style={{ flex: 1 }}><CtaButton label="취소" tone="soft" onPress={() => setDelOpen(false)} /></View>
          <View style={{ flex: 1.6 }}><CtaButton label="탈퇴하기" tone="danger" onPress={() => del.mutate()} /></View>
        </View>
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.card },
  me: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 20 },
  name: { fontFamily: fam.extrabold, fontSize: 19, color: colors.text },
  email: { fontFamily: fam.regular, fontSize: 14, color: colors.textMuted },
  cap: { fontFamily: fam.bold, fontSize: 13, color: colors.textMuted, paddingTop: 24, paddingHorizontal: 24, paddingBottom: 8 },
  card: { backgroundColor: colors.white, borderRadius: radius.card, marginHorizontal: 20, paddingHorizontal: 16 },
  logout: { alignItems: 'center', paddingTop: 26, paddingBottom: 6 },
  logoutText: { fontFamily: fam.semibold, fontSize: 15, color: colors.up },
  version: { textAlign: 'center', fontFamily: fam.regular, fontSize: 12, color: colors.textDisabled, paddingBottom: 28 },
});
