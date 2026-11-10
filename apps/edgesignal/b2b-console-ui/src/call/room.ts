// 휴대폰 ↔ 콘솔 방(ALPHA-1490). 휴대폰 전화 화면이 받아 적은 말을 /api/call/room/{방} 에 올리고, 콘솔이 가져간다.

export type RoomEvent = { type: 'start' } | { type: 'stop' } | { type: 'delta'; text: string };
export type StoredRoomEvent = RoomEvent & { seq: number };

/** 추측하기 어려운 방 코드 — 12자(영문 대소문자·숫자). */
export function newRoomId(): string {
  const abc = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, (b) => abc[b % abc.length]).join('');
}

export const phoneUrl = (room: string) => `${location.origin}/#/phone/${room}`;

export async function sendRoomEvents(room: string, events: RoomEvent[]): Promise<void> {
  const res = await fetch(`/api/call/room/${room}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events }),
  });
  if (!res.ok) throw new Error(`콘솔로 보내지 못했습니다(${res.status})`);
}

export async function readRoom(room: string, after: number): Promise<StoredRoomEvent[]> {
  const res = await fetch(`/api/call/room/${room}?after=${after}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`휴대폰 연결을 읽지 못했습니다(${res.status})`);
  return (await res.json()).events ?? [];
}

/**
 * 휴대폰 쪽 보내기 — 전사 조각을 모아 0.3초마다 한 번에 올린다. 앞 요청이 끝나야 다음을 보내 순서가 지켜진다.
 * 실패한 묶음은 다음 차례에 다시 보낸다(시연 중 잠깐 끊겨도 말이 빠지지 않게).
 */
export function roomSender(room: string, onError: (message: string) => void) {
  let queue: RoomEvent[] = [];
  let busy = false;
  const flush = async () => {
    if (busy || !queue.length) return;
    busy = true;
    const batch = queue.slice(0, 50);
    try {
      await sendRoomEvents(room, batch);
      queue = queue.slice(batch.length);
    } catch (err) {
      onError((err as Error).message);
    } finally {
      busy = false;
    }
  };
  const timer = setInterval(flush, 300);
  return {
    push: (e: RoomEvent) => {
      // 조각이 500자를 넘지 않게 나눈다(서버 한도).
      if (e.type === 'delta' && e.text.length > 500) for (let i = 0; i < e.text.length; i += 500) queue.push({ type: 'delta', text: e.text.slice(i, i + 500) });
      else queue.push(e);
    },
    /** 남은 것을 다 보내고 멈춘다 */
    close: async () => {
      clearInterval(timer);
      for (let i = 0; i < 20 && queue.length; i++) {
        await flush();
        if (queue.length) await new Promise((r) => setTimeout(r, 300));
      }
    },
  };
}
