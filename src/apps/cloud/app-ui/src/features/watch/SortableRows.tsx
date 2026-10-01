import { useEffect, useRef, useState } from 'react';
import { Animated, PanResponder, Pressable, StyleSheet, Text, View } from 'react-native';
import type { EtfSummary } from '@/api';
import { SectorIcon } from '@/components/ui';
import { colors, shadow } from '@/theme/tokens';
import { fam } from '@/theme/typography';
import { CheckCircle } from './CheckCircle';

const HOLD_MS = 250;

interface Drag {
  start: (i: number) => void;
  move: (dy: number) => void;
  end: () => void;
}

interface Props {
  etfs: EtfSummary[];
  selected: string[];
  onToggle: (code: string) => void;
  onReorder: (codes: string[]) => void;
  onDragging: (on: boolean) => void;
}

// 체크 선택과 손잡이 길게 눌러 끌기의 관심 종목 목록
export function SortableRows({ etfs, selected, onToggle, onReorder, onDragging }: Props) {
  const [order, setOrder] = useState(etfs);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const dy = useRef(new Animated.Value(0)).current;
  const rowH = useRef(0);
  const cur = useRef<{ from: number; to: number } | null>(null);
  useEffect(() => {
    if (!cur.current) setOrder(etfs);
  }, [etfs]);

  const handlers = useRef<Drag>({ start: () => {}, move: () => {}, end: () => {} });
  handlers.current = {
    start: (i) => {
      cur.current = { from: i, to: i };
      dy.setValue(0);
      setDrag(cur.current);
      onDragging(true);
    },
    move: (d) => {
      const c = cur.current;
      if (!c || !rowH.current) return;
      dy.setValue(d);
      const to = Math.max(0, Math.min(order.length - 1, c.from + Math.round(d / rowH.current)));
      if (to !== c.to) {
        cur.current = { from: c.from, to };
        setDrag(cur.current);
      }
    },
    end: () => {
      const c = cur.current;
      cur.current = null;
      setDrag(null);
      onDragging(false);
      if (!c || c.from === c.to) return;
      const next = [...order];
      const [m] = next.splice(c.from, 1);
      next.splice(c.to, 0, m);
      setOrder(next);
      onReorder(next.map((e) => e.code));
    },
  };

  // 끄는 동안 사이 행의 한 칸 밀림
  const shift = (k: number) => {
    if (!drag || k === drag.from) return 0;
    if (drag.from < drag.to && k > drag.from && k <= drag.to) return -rowH.current;
    if (drag.to < drag.from && k >= drag.to && k < drag.from) return rowH.current;
    return 0;
  };

  return (
    <View>
      {order.map((e, i) => {
        const dragging = drag?.from === i;
        return (
          <Animated.View
            key={e.code}
            onLayout={i === 0 ? (ev) => { rowH.current = ev.nativeEvent.layout.height; } : undefined}
            style={[styles.wrap, dragging ? [styles.lifted, { transform: [{ translateY: dy }] }] : { transform: [{ translateY: shift(i) }] }]}
          >
            <Pressable onPress={() => onToggle(e.code)} style={({ pressed }) => [styles.row, pressed && !drag && { opacity: 0.6 }]}>
              <CheckCircle on={selected.includes(e.code)} />
              <SectorIcon theme={e.theme} bg={e.logoBg} size={30} />
              <Text numberOfLines={1} style={styles.name}>{e.name}</Text>
              <Grip index={i} handlers={handlers} />
            </Pressable>
          </Animated.View>
        );
      })}
    </View>
  );
}

function Grip({ index, handlers }: { index: number; handlers: React.MutableRefObject<Drag> }) {
  const idx = useRef(index);
  idx.current = index;
  const pr = useRef(
    (() => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let active = false;
      const stop = () => {
        clearTimeout(timer);
        if (active) handlers.current.end();
        active = false;
      };
      return PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          timer = setTimeout(() => {
            active = true;
            handlers.current.start(idx.current);
          }, HOLD_MS);
        },
        onPanResponderMove: (_, g) => {
          if (active) handlers.current.move(g.dy);
          else if (Math.abs(g.dy) > 6) clearTimeout(timer);
        },
        // 길게 누르기 전에는 스크롤에 양보
        onPanResponderTerminationRequest: () => !active,
        onPanResponderRelease: stop,
        onPanResponderTerminate: stop,
      });
    })(),
  ).current;
  return (
    <View {...pr.panHandlers} accessibilityLabel="순서 바꾸기" style={styles.grip}>
      {[0, 1, 2].map((i) => (
        <View key={i} style={styles.gripRow}><View style={styles.dot} /><View style={styles.dot} /></View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { backgroundColor: colors.white },
  lifted: { zIndex: 1, ...shadow.floating },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: colors.surface },
  name: { flex: 1, fontFamily: fam.bold, fontSize: 15, color: colors.text },
  grip: { gap: 3, paddingVertical: 6, paddingHorizontal: 8, marginRight: -6 },
  gripRow: { flexDirection: 'row', gap: 3 },
  dot: { width: 3, height: 3, borderRadius: 999, backgroundColor: colors.lineStrong },
});
