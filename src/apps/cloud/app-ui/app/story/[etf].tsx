import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import type { StoryCard } from '@/api';
import { Chevron, IconButton, SectorIcon } from '@/components/ui';
import { useStories } from '@/features/story/queries';
import { pct } from '@/lib/format';
import { colors, signal as SIG } from '@/theme/tokens';
import { fam } from '@/theme/typography';

const DUR = 6000;
const DOT = { help: colors.up, neutral: colors.neutral, burden: colors.down } as const;

function Card({ card, onIssue }: { card: StoryCard; onIssue: (id: string) => void }) {
  if (card.kind === 'ai') {
    return (
      <ScrollView contentContainerStyle={styles.aiScroll} showsVerticalScrollIndicator={false}>
        <Text style={styles.badge}>{card.badge}</Text>
        <Text style={[styles.pct, { color: card.changePct < 0 ? colors.down : colors.up }]}>{pct(card.changePct)}</Text>
        <Text style={styles.hd}>{card.headline}</Text>
        <Text style={styles.noteTitle}>{card.noteTitle}</Text>
        <View style={{ gap: 7, marginTop: 9 }}>
          {card.notes.map((n) => (
            <View key={n} style={styles.note}><View style={styles.noteDot} /><Text style={styles.noteText}>{n}</Text></View>
          ))}
        </View>
        <Text style={styles.noteTitle}>관련 이슈</Text>
        <View style={{ gap: 6, marginTop: 9, marginBottom: 20 }}>
          {card.news.map((n) => (
            <Pressable key={n.t} onPress={() => onIssue(n.issueId)} style={({ pressed }) => [styles.news, pressed && { opacity: 0.6 }]}>
              <View style={[styles.newsDot, { backgroundColor: DOT[n.dir] }]} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={styles.newsT}>{n.t}</Text>
                <Text numberOfLines={1} style={styles.newsKw}>{n.phase} · {n.kw}</Text>
              </View>
              <Chevron size={14} color={colors.textFaint} />
            </Pressable>
          ))}
        </View>
      </ScrollView>
    );
  }
  if (card.kind === 'news') {
    return (
      <View style={{ flex: 1 }}>
        <Text style={styles.newsBig}>{card.t}</Text>
        <Text style={styles.newsKw2}>{card.kw}</Text>
        <Text style={styles.newsBody}>{card.b}</Text>
        <Pressable onPress={() => onIssue(card.issueId)} style={({ pressed }) => [styles.newsCta, pressed && { opacity: 0.6 }]}>
          <Text style={styles.newsCtaText}>뉴스 자세히 보기</Text>
          <Chevron size={16} color={colors.textFaint} />
        </Pressable>
      </View>
    );
  }
  return (
    <View style={{ flex: 1 }}>
      <Text style={styles.hookBig}>{card.big}</Text>
      <Text style={styles.cap}>{card.capPre}<Text style={styles.capB}>{card.capB}</Text>{card.capPost}</Text>
    </View>
  );
}

export default function Story() {
  const { etf } = useLocalSearchParams<{ etf: string }>();
  const router = useRouter();
  const { top, bottom } = useSafeAreaInsets();
  const { data: stories } = useStories();
  const [si, setSi] = useState(() => Math.max(0, (stories ?? []).findIndex((s) => s.etf.code === etf)));
  const [ci, setCi] = useState(0);
  const [playing, setPlaying] = useState(true);
  const progress = useRef(new Animated.Value(0)).current;
  const anim = useRef<Animated.CompositeAnimation | null>(null);
  const story = stories?.[si];
  const cards = useMemo(() => story?.cards ?? [], [story]);
  const card = cards[ci];

  const next = () => {
    if (!stories) return;
    if (ci + 1 < cards.length) setCi(ci + 1);
    else if (si + 1 < stories.length) { setSi(si + 1); setCi(0); }
    else router.back();
  };
  const prev = () => {
    if (ci > 0) setCi(ci - 1);
    else if (si > 0) { setSi(si - 1); setCi(0); }
  };

  useEffect(() => {
    if (!card) return;
    progress.setValue(0);
    anim.current?.stop();
    anim.current = Animated.timing(progress, { toValue: 1, duration: DUR, useNativeDriver: false });
    if (playing) anim.current.start(({ finished }) => finished && next());
    return () => anim.current?.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [si, ci, card]);
  useEffect(() => {
    if (!card) return;
    if (playing) anim.current?.start(({ finished }) => finished && next());
    else anim.current?.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  if (!story || !card) return <View style={styles.root} />;
  const now = SIG[story.etf.signal];
  const prevSig = story.prev ? SIG[story.prev] : null;
  const width = progress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });
  const goIssue = (id: string) => { router.back(); setTimeout(() => router.push(`/issues/${id}`), 0); };
  return (
    <View style={[styles.root, { paddingTop: top + 8 }]}>
      <View style={styles.bars}>
        {cards.map((_, i) => (
          <View key={i} style={styles.bar}>
            {i < ci && <View style={[styles.barFill, { width: '100%' }]} />}
            {i === ci && <Animated.View style={[styles.barFill, { width }]} />}
          </View>
        ))}
      </View>
      <View style={styles.head}>
        <SectorIcon theme={story.etf.theme} bg={story.etf.logoBg} size={32} />
        <View style={{ flex: 1 }}>
          <Text numberOfLines={1} style={styles.name}>{story.etf.name}</Text>
          <Text style={styles.sub}>{story.sub} · {ci + 1}/{cards.length}</Text>
        </View>
        {prevSig && prevSig !== now && (
          <View style={styles.changed}>
            <Text style={styles.prevLabel}><Text style={{ color: prevSig.color }}>{prevSig.mark}</Text> {prevSig.label}</Text>
            <Text style={styles.arrow}>→</Text>
            <Text style={styles.nowLabel}><Text style={{ color: now.color }}>{now.mark}</Text> {now.label}</Text>
          </View>
        )}
        <IconButton icon="close" onPress={() => router.back()} />
      </View>
      <View style={styles.stage}>
        <View style={styles.cardWrap}>
          {!!card.sec && <Text style={styles.sec}>{card.sec}</Text>}
          <Card card={card} onIssue={goIssue} />
        </View>
        <Pressable onPress={prev} style={styles.zonePrev} />
        <Pressable onPress={next} style={styles.zoneNext} />
      </View>
      <View style={[styles.foot, { paddingBottom: Math.max(bottom, 20) + 16 }]}>
        <Pressable onPress={() => setPlaying((v) => !v)} style={styles.playBtn}>
          <Svg width={14} height={14} viewBox="0 0 14 14">
            {playing ? <Path d="M3 2h3v10H3zM8 2h3v10H8z" fill={colors.text} /> : <Path d="M4 2l8 5-8 5z" fill={colors.text} />}
          </Svg>
        </Pressable>
        <Pressable onPress={() => { router.back(); setTimeout(() => router.push(`/etf/${story.etf.code}/brief`), 0); }} style={({ pressed }) => [styles.cta, pressed && { opacity: 0.8 }]}>
          <Text style={styles.ctaText}>ETF 상세 보기</Text>
          <Svg width={12} height={12} viewBox="0 0 12 12"><Path d="M4 2.5l4 3.5-4 3.5" stroke={colors.white} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" /></Svg>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.white },
  bars: { flexDirection: 'row', gap: 4, paddingHorizontal: 12 },
  bar: { flex: 1, height: 2.5, borderRadius: 2, backgroundColor: colors.line, overflow: 'hidden' },
  barFill: { height: '100%', backgroundColor: colors.text },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 12, paddingHorizontal: 14 },
  name: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
  sub: { fontFamily: fam.regular, fontSize: 12, color: colors.textFaint, marginTop: 1 },
  changed: { flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.line, borderRadius: 999, paddingVertical: 5, paddingHorizontal: 11 },
  prevLabel: { fontFamily: fam.regular, fontSize: 11, color: colors.textFaint },
  arrow: { fontFamily: fam.extrabold, fontSize: 12, color: colors.textDisabled },
  nowLabel: { fontFamily: fam.bold, fontSize: 12, color: colors.text },
  stage: { flex: 1 },
  cardWrap: { flex: 1, paddingTop: 18, paddingHorizontal: 20, paddingBottom: 8 },
  sec: { fontFamily: fam.regular, fontSize: 13, color: colors.textFaint, marginBottom: 12 },
  zonePrev: { position: 'absolute', left: 0, top: 0, bottom: 0, width: '32%' },
  zoneNext: { position: 'absolute', right: 0, top: 0, bottom: 0, width: '68%' },
  aiScroll: { flexGrow: 1, justifyContent: 'center' },
  badge: { alignSelf: 'flex-start', fontFamily: fam.extrabold, fontSize: 12, color: colors.textSub, backgroundColor: colors.surface, borderRadius: 8, paddingVertical: 5, paddingHorizontal: 10, overflow: 'hidden' },
  pct: { fontFamily: fam.monoExtraBold, fontSize: 46, letterSpacing: -1.4, lineHeight: 50, marginTop: 12 },
  hd: { fontFamily: fam.extrabold, fontSize: 23, lineHeight: 32, letterSpacing: -0.7, color: colors.text, marginTop: 18 },
  noteTitle: { fontFamily: fam.bold, fontSize: 12, color: colors.textFaint, marginTop: 20, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.surface },
  note: { flexDirection: 'row', gap: 8 },
  noteDot: { width: 4, height: 4, borderRadius: 999, backgroundColor: colors.textDisabled, marginTop: 9 },
  noteText: { flex: 1, fontFamily: fam.regular, fontSize: 15, lineHeight: 23, color: colors.textSub },
  news: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: colors.card, borderRadius: 12, paddingVertical: 9, paddingHorizontal: 12 },
  newsDot: { width: 8, height: 8, borderRadius: 999 },
  newsT: { fontFamily: fam.bold, fontSize: 14, lineHeight: 19, color: colors.text },
  newsKw: { fontFamily: fam.regular, fontSize: 12, color: colors.textMuted },
  newsBig: { fontFamily: fam.extrabold, fontSize: 26, lineHeight: 34, letterSpacing: -0.5, color: colors.text },
  newsKw2: { fontFamily: fam.semibold, fontSize: 14, color: colors.textMuted, marginTop: 12 },
  newsBody: { fontFamily: fam.regular, fontSize: 15, lineHeight: 26, color: '#333D4B', marginTop: 16 },
  newsCta: { marginTop: 'auto', marginBottom: 64, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: colors.card, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  newsCtaText: { fontFamily: fam.bold, fontSize: 14, color: colors.text },
  hookBig: { fontFamily: fam.extrabold, fontSize: 30, lineHeight: 39, letterSpacing: -0.6, color: colors.text },
  cap: { fontFamily: fam.regular, fontSize: 15, lineHeight: 23, color: colors.textSub, marginTop: 18 },
  capB: { fontFamily: fam.bold, color: colors.text },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 12, paddingHorizontal: 20 },
  playBtn: { width: 48, height: 48, borderRadius: 999, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  cta: { flex: 1, height: 48, borderRadius: 999, backgroundColor: colors.text, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  ctaText: { fontFamily: fam.bold, fontSize: 15, color: colors.white },
});
