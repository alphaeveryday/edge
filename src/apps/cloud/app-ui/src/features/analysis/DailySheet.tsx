import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { DailyAnalysis } from '@/api';
import { BottomSheet, Chevron, SheetScrollView, LinkRow, RowQuote, SectorIcon, Sticker, type SheetStats } from '@/components/ui';
import { VoteCard } from '@/features/community/VoteCard';
import { DisclaimerNote } from '@/features/disclaimer/DisclaimerNote';
import { useVoteStat } from '@/features/community/queries';
import { useEtf } from '@/features/etf/queries';
import { track } from '@/lib/analytics';
import { useScrollFocus } from '@/lib/useScrollFocus';
import { Loading } from '@/components/state';
import { createStyles, useColors, useSignal } from '@/theme/theme';
import { radius } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { axisHref } from './axisHref';
import { dirSignal } from './dir';
import { FactorRow } from './FactorRow';
import { HintSheet } from './HintSheet';

interface Props {
  code: string;
  daily: DailyAnalysis | undefined;
  open: boolean;
  onClose: () => void;
  entry: 'etf_page' | 'explore_rank' | 'explore_next';
  // 탐색에서 열 때의 투표 카드와 다음 ETF
  withVote?: boolean;
  linkEtf?: boolean;
  next?: { code: string; name: string };
  onNext?: () => void;
}

// 분석 상세 시트
export function DailySheet({ code, daily: d, open, onClose, entry, withVote, linkEtf, next, onNext }: Props) {
  const styles = useStyles();
  const colors = useColors();
  const SIG = useSignal();
  const router = useRouter();
  const { data: etf } = useEtf(code);
  const { data: stat } = useVoteStat(code, !!withVote && open);
  const [axisOpen, setAxisOpen] = useState(false);
  // 다른 화면에 다녀오는 동안의 숨김
  // 떠날 때와 복귀 시 애니메이션 없는 전환
  const [away, setAway] = useState(false);
  const awayRef = useRef(false);
  const [instant, setInstant] = useState(false);
  const shown = open && !away;
  const focus = useScrollFocus(axisOpen && shown);
  const [hint, setHint] = useState<string | null>(null);
  useFocusEffect(useCallback(() => {
    if (!awayRef.current) return;
    awayRef.current = false;
    setInstant(true);
    setAway(false);
  }, []));
  const leave = (go?: () => void) => {
    awayRef.current = true;
    setInstant(true);
    setAway(true);
    go?.();
  };
  // 다른 화면 이동이 끝낸 열림의 복귀 뒤 닫힘 미기록
  const ended = useRef(false);
  useEffect(() => {
    if (!open) return;
    ended.current = false;
    track('analysis_detail_opened', { etf: code, entry });
  }, [open, code]);
  const closed = (s: SheetStats) => {
    if (ended.current) return;
    ended.current = s.close_method === 'navigate';
    track('analysis_detail_closed', { etf: s.session ?? code, duration_sec: s.duration_sec, scroll_pct: s.scroll_pct, close_method: s.close_method });
  };
  const goMetric = (axis: string) => leave(() => router.push(axisHref(code, axis)));
  // 복귀 표시 직후 즉시 전환 해제
  useEffect(() => {
    if (shown && instant) setInstant(false);
  }, [shown, instant]);
  const head = (
    <View style={styles.headRow}>
      {/* ETF 이름 탭 시 상세의 오늘 움직임으로 이동 */}
      <Pressable disabled={!linkEtf} onPress={() => leave(() => router.push(`/etf/${code}/summary`))} style={styles.headLink}>
        {etf && <SectorIcon theme={etf.theme} bg={etf.logoBg} size={36} />}
        <View style={styles.headMid}>
          <Text numberOfLines={1} style={styles.headName}>{etf?.name}</Text>
          {etf && <RowQuote price={etf.price} changePct={etf.changePct} />}
        </View>
      </Pressable>
      {etf && <Sticker signal={etf.signal} />}
    </View>
  );
  return (
    <BottomSheet open={shown} onClose={onClose} tall padded={false} instant={instant} head={head} session={code} onClosed={closed} closeMethod={away ? 'navigate' : 'button'}>
      <View style={styles.rule0} />
      {!d && <Loading rows={3} />}
      {d && (
        <SheetScrollView ref={focus.scroll} contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 20, paddingBottom: 10 }}>
          {withVote && stat && <View style={{ marginBottom: 20 }}><VoteCard stat={stat} entry="analysis_detail" onGate={leave} /></View>}
          <Text style={styles.title}>{d.title}</Text>
          {d.today.length > 0 && (
            <View style={styles.today}>
              <View style={styles.todayHead}>
                <View style={styles.todayDot} />
                <Text style={styles.todayCap}>오늘 추가된 것 · {d.todayDate}</Text>
              </View>
              <View style={{ gap: 9, marginTop: 11 }}>
                {d.today.map((t) => <Text key={t} style={styles.todayLine}><Text style={styles.hl}>{t}</Text></Text>)}
              </View>
            </View>
          )}
          {d.args.map((a) => (
            <View key={a.no} style={styles.arg}>
              <View style={styles.argHead}>
                <Text style={styles.argNo}>{a.no}</Text>
                <Text style={styles.argClaim}>{a.claim}</Text>
              </View>
              <View style={styles.argBody}>
                {a.body.map((b) => (
                  <View key={b} style={styles.bullet}>
                    <View style={styles.bulletDot} />
                    <Text style={styles.bulletText}>{b}</Text>
                  </View>
                ))}
              </View>
            </View>
          ))}
          <View style={styles.rule} />
          <View style={styles.closing}>
            <Text style={styles.closingTitle}>그래서 {d.closingTitle}</Text>
            <View style={{ gap: 12, marginTop: 18 }}>
              <View style={{ gap: 8 }}>
                <Text style={[styles.chipCap, { color: colors.downDeep }]}>부담</Text>
                <View style={styles.chips}>{d.neg.map((t) => <Text key={t} style={[styles.chip, styles.chipNeg]}>{t}</Text>)}</View>
              </View>
              <View style={{ gap: 8, marginTop: 4 }}>
                <Text style={[styles.chipCap, { color: colors.upDeep }]}>개선</Text>
                <View style={styles.chips}>{d.pos.map((t) => <Text key={t} style={[styles.chip, styles.chipPos]}>{t}</Text>)}</View>
              </View>
            </View>
            <Text style={styles.close}>{d.close}</Text>
            {d.prev && d.prev !== d.now && (
              <View style={styles.changed}>
                <Text style={styles.prevLabel}>{SIG[d.prev].label}</Text>
                <Text style={styles.arrow}>→</Text>
                <Text style={[styles.nowLabel, { color: SIG[d.now].color }]}>{SIG[d.now].label}</Text>
              </View>
            )}
          </View>
          <Pressable ref={focus.anchor} onPress={() => { if (!axisOpen) track('analysis_criteria_expanded', { etf: code }); setAxisOpen(!axisOpen); }} style={styles.toggle}>
            <Text style={styles.toggleText}>5가지 기준 모두 보기</Text>
            <Chevron size={14} color={colors.textFaint} dir={axisOpen ? 'up' : 'down'} />
          </Pressable>
          {axisOpen && (
            <View style={{ gap: 10, marginTop: 16 }}>
              {d.axes.map((a) => (
                <FactorRow key={a.axis} axis={a.axis} dir={a.dir} summary={a.summary} hasPage={a.hasPage} onSelect={() => a.hasPage && goMetric(a.axis)} onHint={() => setHint(a.axis)} />
              ))}
            </View>
          )}
          <Pressable onPress={() => setHint('edge')} style={styles.source}>
            <Text style={styles.sourceText}>분석 기준과 출처</Text>
            <Chevron size={14} color={colors.textDisabled} />
          </Pressable>
          <DisclaimerNote style={styles.note} />
        </SheetScrollView>
      )}
      {next && onNext && (
        <View style={styles.foot}>
          <LinkRow variant="accent" label={`다음 · ${next.name}`} onPress={onNext} />
        </View>
      )}
      <HintSheet hintKey={hint} onClose={() => setHint(null)} />
    </BottomSheet>
  );
}

const useStyles = createStyles((colors) => ({
  rule0: { height: 1, backgroundColor: colors.surface },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 20, paddingBottom: 4 },
  headLink: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 },
  headMid: { flex: 1, gap: 5, marginLeft: 4 },
  headName: { fontFamily: fam.bold, fontSize: 15, color: colors.text, letterSpacing: -0.3, lineHeight: 20 },
  title: { fontFamily: fam.extrabold, fontSize: 21, lineHeight: 29, letterSpacing: -0.6, color: colors.text },
  today: { marginTop: 16, borderRadius: radius.card, borderWidth: 1, borderColor: colors.primaryTint, backgroundColor: colors.primarySoft, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 15 },
  todayHead: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  todayDot: { width: 7, height: 7, borderRadius: 999, backgroundColor: colors.down },
  todayCap: { fontFamily: fam.extrabold, fontSize: 13, color: colors.downDeep },
  todayLine: { fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.text },
  hl: { backgroundColor: colors.highlight },
  arg: { marginTop: 18, borderRadius: radius.card, padding: 12, marginHorizontal: -12 },
  argHead: { flexDirection: 'row', gap: 10, alignItems: 'baseline' },
  argNo: { fontFamily: fam.monoBold, fontSize: 12, color: colors.textDisabled },
  argClaim: { flex: 1, fontFamily: fam.extrabold, fontSize: 17, lineHeight: 25, letterSpacing: -0.5, color: colors.text },
  argBody: { gap: 7, marginTop: 12, paddingLeft: 26 },
  bullet: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  bulletDot: { width: 4, height: 4, borderRadius: 999, backgroundColor: colors.textFaint, marginTop: 10 },
  bulletText: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 25, color: colors.textSub },
  rule: { marginTop: 34, height: 1, backgroundColor: colors.line },
  closing: { marginTop: 26, borderRadius: radius.card, backgroundColor: colors.card, paddingTop: 22, paddingHorizontal: 20, paddingBottom: 24 },
  closingTitle: { fontFamily: fam.extrabold, fontSize: 18, letterSpacing: -0.5, color: colors.text },
  chipCap: { fontFamily: fam.extrabold, fontSize: 12, letterSpacing: 0.5 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { fontFamily: fam.bold, fontSize: 13, borderRadius: radius.tag, paddingVertical: 6, paddingHorizontal: 10, overflow: 'hidden' },
  chipNeg: { color: colors.downDeep, backgroundColor: colors.downSoft },
  chipPos: { color: colors.upDeep, backgroundColor: colors.upSoft },
  close: { fontFamily: fam.bold, fontSize: 16, lineHeight: 27, color: colors.text, marginTop: 18, paddingTop: 16, borderTopWidth: 1, borderTopColor: colors.line },
  changed: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14 },
  prevLabel: { fontFamily: fam.bold, fontSize: 14, color: colors.textMuted },
  arrow: { fontFamily: fam.regular, fontSize: 13, color: colors.textDisabled },
  nowLabel: { fontFamily: fam.extrabold, fontSize: 14 },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 34, paddingTop: 24, borderTopWidth: 1, borderTopColor: colors.surface },
  toggleText: { fontFamily: fam.extrabold, fontSize: 14, color: colors.text, letterSpacing: -0.28 },
  source: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 18, paddingTop: 18, borderTopWidth: 1, borderTopColor: colors.surface },
  note: { marginTop: 16 },
  sourceText: { fontFamily: fam.bold, fontSize: 14, color: colors.textSub },
  foot: { paddingTop: 8, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: colors.surface, backgroundColor: colors.bg },
}));
