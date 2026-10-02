import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { DailyAnalysis } from '@/api';
import { BottomBar, Chevron, IconButton, LinkRow, RowQuote, SectorIcon, Sticker } from '@/components/ui';
import { VoteCard } from '@/features/community/VoteCard';
import { useVoteStat } from '@/features/community/queries';
import { useEtf } from '@/features/etf/queries';
import { Loading } from '@/components/state';
import { colors, signal as SIG, radius, shadow } from '@/theme/tokens';
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
  // 탐색에서 열 때의 투표 카드와 다음 ETF
  withVote?: boolean;
  linkEtf?: boolean;
  next?: { code: string; name: string };
  onNext?: () => void;
}

// 화면을 거의 다 덮는 분석 상세 시트
export function DailySheet({ code, daily: d, open, onClose, withVote, linkEtf, next, onNext }: Props) {
  const router = useRouter();
  const { top, bottom } = useSafeAreaInsets();
  const { data: etf } = useEtf(code);
  const { data: stat } = useVoteStat(code, !!withVote && open);
  const [axisOpen, setAxisOpen] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const goMetric = (axis: string) => {
    onClose();
    router.push(axisHref(code, axis));
  };
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={[styles.sheet, { marginTop: top + 46 }]}>
          <View style={styles.head}>
            <View style={styles.handle} />
            <View style={styles.headRow}>
              {/* ETF 이름을 누르면 상세의 오늘 움직임으로 */}
              <Pressable disabled={!linkEtf} onPress={() => { onClose(); router.push(`/etf/${code}/summary`); }} style={styles.headLink}>
                {etf && <SectorIcon theme={etf.theme} bg={etf.logoBg} size={36} />}
                <View style={styles.headMid}>
                  <Text numberOfLines={1} style={styles.headName}>{etf?.name}</Text>
                  {etf && <RowQuote price={etf.price} changePct={etf.changePct} />}
                </View>
              </Pressable>
              {etf && <Sticker signal={etf.signal} />}
              <IconButton icon="close" size={32} color={colors.textFaint} onPress={onClose} />
            </View>
          </View>
          {!d && <Loading rows={3} />}
          {d && (
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 20, paddingBottom: next && onNext ? 10 : bottom + 10 }} showsVerticalScrollIndicator={false}>
              {withVote && stat && <View style={{ marginBottom: 20 }}><VoteCard stat={stat} onGate={onClose} /></View>}
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
              <Pressable onPress={() => setAxisOpen((v) => !v)} style={styles.toggle}>
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
            </ScrollView>
          )}
          {next && onNext && (
            <BottomBar style={styles.foot}>
              <LinkRow variant="accent" label={`다음 · ${next.name}`} onPress={onNext} />
            </BottomBar>
          )}
          <HintSheet hintKey={hint} onClose={() => setHint(null)} />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  backdrop: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim },
  sheet: { flex: 1, backgroundColor: colors.white, borderTopLeftRadius: radius.sheet, borderTopRightRadius: radius.sheet, ...shadow.sheet },
  head: { paddingTop: 10, paddingHorizontal: 20, paddingBottom: 16, borderBottomWidth: 1, borderBottomColor: colors.surface },
  handle: { width: 38, height: 4, borderRadius: 999, backgroundColor: colors.line, alignSelf: 'center', marginBottom: 18 },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
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
  sourceText: { fontFamily: fam.bold, fontSize: 14, color: colors.textSub },
  foot: { paddingTop: 8, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: colors.surface, backgroundColor: colors.white },
});
