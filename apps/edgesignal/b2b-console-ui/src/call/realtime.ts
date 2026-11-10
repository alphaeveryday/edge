// OpenAI 실시간 전사 연결(WebRTC). 키는 서버(/api/call/token)가 60초짜리 임시 토큰으로만 내준다.
// 마이크 음성을 보내고, 데이터 채널(oai-events)로 전사 조각(delta)·확정본(completed)을 받는다.

export interface TranscriptEvents {
  /** 말하는 도중 오는 전사 조각 — item 별로 이어 붙인다 */
  onDelta: (itemId: string, delta: string) => void;
  /** 한 발화의 확정 전사 */
  onCompleted: (itemId: string, transcript: string) => void;
  onError: (message: string) => void;
}

export interface TranscriptSession {
  stop: () => void;
}

export async function startTranscription(keywords: string[], ev: TranscriptEvents): Promise<TranscriptSession> {
  // 마이크 권한을 먼저 받는다 — 거절되면 토큰을 만들 이유가 없다.
  const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const pc = new RTCPeerConnection();
  const stop = () => {
    mic.getTracks().forEach((t) => t.stop());
    pc.close();
  };
  try {
    const res = await fetch('/api/call/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keywords }),
    });
    const token = await res.json().catch(() => ({}));
    if (!res.ok || !token.value) throw new Error(token.error ?? `토큰 발급 실패(${res.status})`);

    mic.getTracks().forEach((t) => pc.addTrack(t, mic));
    const dc = pc.createDataChannel('oai-events');
    dc.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === 'conversation.item.input_audio_transcription.delta') ev.onDelta(e.item_id, e.delta ?? '');
      else if (e.type === 'conversation.item.input_audio_transcription.completed') ev.onCompleted(e.item_id, e.transcript ?? '');
      else if (e.type === 'error') ev.onError(e.error?.message ?? '전사 오류');
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed') ev.onError('전사 연결이 끊겼습니다');
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdp = await fetch('https://api.openai.com/v1/realtime/calls', {
      method: 'POST',
      body: offer.sdp,
      headers: { Authorization: `Bearer ${token.value}`, 'Content-Type': 'application/sdp' },
    });
    if (!sdp.ok) throw new Error(`전사 연결 실패(${sdp.status})`);
    await pc.setRemoteDescription({ type: 'answer', sdp: await sdp.text() });
    return { stop };
  } catch (err) {
    stop();
    throw err;
  }
}
