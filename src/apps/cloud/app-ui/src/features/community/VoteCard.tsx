import { Pressable, Text, View } from 'react-native';
import type { VoteStat, VoteChoice } from '@/api';
import { createStyles, useColors } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { useVote } from './queries';
import { useRequireLogin } from '@/store/session';
import { track } from '@/lib/analytics';

export type VoteEntry = 'community' | 'etf_community' | 'analysis_detail';

export function VoteCard({ stat, entry, onGate }: { stat: VoteStat; entry: VoteEntry; onGate?: () => void }) {
  const styles = useStyles();
  const colors = useColors();
  const META: { k: VoteChoice; label: string; c: string; bg: string }[] = [
    { k: 'buy', label: '산다', c: colors.up, bg: colors.voteUpBg },
    { k: 'wait', label: '기다린다', c: colors.textSub, bg: colors.surface },
    { k: 'sell', label: '판다', c: colors.down, bg: colors.voteDownBg },
  ];
  const vote = useVote(stat.code);
  const requireLogin = useRequireLogin();
  const top = META.reduce((a, b) => (stat.pct[b.k] > stat.pct[a.k] ? b : a));
  const voted = !!stat.mine;
  return (
    <View style={styles.card}>
      {stat.count > 0 ? (
        <>
          <Text style={styles.title}><Text style={{ color: top.c }}>{top.label}</Text> 선택이 더 많아요</Text>
          <Text style={styles.sub}>{stat.count.toLocaleString('ko-KR')}명 참여{voted && ' · 나도 포함'}</Text>
          <View style={styles.bar}>
            {META.map((m) => <View key={m.k} style={{ flex: Math.max(stat.pct[m.k], 4), borderRadius: 5, backgroundColor: m.c }} />)}
          </View>
        </>
      ) : (
        <>
          <Text style={styles.title}>이 전망, 당신이라면?</Text>
          <Text style={styles.sub}>아직 참여한 사람이 없어요</Text>
        </>
      )}
      <View style={styles.btns}>
        {META.map((m) => {
          const on = stat.mine === m.k;
          return (
            <Pressable
              key={m.k}
              onPress={() => requireLogin('투표', () => vote.mutate(on ? null : m.k, {
                onSuccess: () => track(on ? 'vote_canceled' : 'vote_submitted', on ? { etf: stat.code, entry } : { etf: stat.code, choice: m.k, entry }),
              }), onGate)}
              style={({ pressed }) => [styles.btn, voted ? { backgroundColor: on ? m.c : colors.bg, borderWidth: 1, borderColor: on ? m.c : colors.lineStrong } : { backgroundColor: m.bg }, pressed && { transform: [{ scale: 0.97 }] }]}
            >
              <Text numberOfLines={1} adjustsFontSizeToFit style={[styles.btnText, { color: voted && on ? colors.onPrimary : m.c }]}>
                {m.label}{voted && <Text style={styles.btnPct}> {stat.pct[m.k]}%</Text>}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const useStyles = createStyles((colors) => ({
  card: { borderRadius: radius.card, backgroundColor: colors.card, padding: 16 },
  title: { fontFamily: fam.extrabold, fontSize: 17, lineHeight: 23, letterSpacing: -0.5, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 12.5, color: colors.textMuted, marginTop: 4 },
  bar: { flexDirection: 'row', gap: 3, marginTop: 12, height: 12 },
  btns: { flexDirection: 'row', gap: 8, marginTop: 12 },
  btn: { flex: 1, height: 40, borderRadius: radius.control, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6 },
  btnText: { fontFamily: fam.extrabold, fontSize: 14 },
  btnPct: { fontFamily: fam.monoExtraBold, fontSize: 13 },
}));
