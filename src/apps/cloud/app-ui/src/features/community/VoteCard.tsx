import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { VoteStat, VoteChoice } from '@/api';
import { colors } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useVote } from './queries';
import { useRequireLogin } from '@/store/session';

const META: { k: VoteChoice; label: string; c: string; bg: string }[] = [
  { k: 'buy', label: '산다', c: colors.up, bg: '#FFF0F1' },
  { k: 'wait', label: '기다린다', c: colors.textSub, bg: colors.surface },
  { k: 'sell', label: '판다', c: colors.down, bg: '#EAF2FF' },
];

export function VoteCard({ stat, onGate }: { stat: VoteStat; onGate?: () => void }) {
  const vote = useVote(stat.code);
  const requireLogin = useRequireLogin();
  const top = META.reduce((a, b) => (stat.pct[b.k] > stat.pct[a.k] ? b : a));
  const voted = !!stat.mine;
  return (
    <View style={styles.card}>
      {voted ? (
        <>
          <Text style={styles.title}><Text style={{ color: top.c }}>{top.label}</Text> 선택이 더 많아요</Text>
          <Text style={styles.sub}>{stat.count.toLocaleString('ko-KR')}명 참여 · 나도 포함</Text>
          <View style={styles.bar}>
            {META.map((m) => <View key={m.k} style={{ flex: Math.max(stat.pct[m.k], 4), borderRadius: 5, backgroundColor: m.c }} />)}
          </View>
          <View style={styles.pcts}>
            {META.map((m) => <Text key={m.k} numberOfLines={1} style={[styles.pct, { flex: Math.max(stat.pct[m.k], 4), color: m.c }]}>{stat.pct[m.k]}%</Text>)}
          </View>
        </>
      ) : (
        <>
          <Text style={styles.title}>이 전망, 당신이라면?</Text>
          <Text style={styles.sub}>{stat.count.toLocaleString('ko-KR')}명이 골랐어요</Text>
        </>
      )}
      <View style={styles.btns}>
        {META.map((m) => {
          const on = stat.mine === m.k;
          return (
            <Pressable
              key={m.k}
              onPress={() => requireLogin('투표', () => vote.mutate(m.k), onGate)}
              style={({ pressed }) => [styles.btn, voted ? { backgroundColor: on ? m.c : colors.white, borderWidth: 1.5, borderColor: on ? m.c : colors.line } : { backgroundColor: m.bg }, pressed && { transform: [{ scale: 0.97 }] }]}
            >
              <Text style={[styles.btnText, { color: voted && on ? colors.white : m.c }]}>{m.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 18, backgroundColor: colors.card, padding: 16 },
  title: { fontFamily: fam.extrabold, fontSize: 17, lineHeight: 23, letterSpacing: -0.5, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 12.5, color: colors.textMuted, marginTop: 4 },
  bar: { flexDirection: 'row', gap: 3, marginTop: 12, height: 12 },
  pcts: { flexDirection: 'row', gap: 3, marginTop: 6 },
  pct: { fontFamily: fam.monoExtraBold, fontSize: 13 },
  btns: { flexDirection: 'row', gap: 8, marginTop: 12 },
  btn: { flex: 1, height: 40, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  btnText: { fontFamily: fam.extrabold, fontSize: 14 },
});
