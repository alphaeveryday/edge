import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { Button } from './design-system/components';
import { trackResult } from './analytics';
import orca from '../../app-ui/assets/icon.png';

// 유형 카드 10가지(시안 v8-SH-types). 어느 유형인지는 서버가 정해 type_card.key 로 보낸다 — 화면은 판정하지 않는다.
type TypeInfo = { no: string; name: string; line: string; weak: string; match: string; enemy: string };
export const typeCards: Record<string, TypeInfo> = {
  start: { no: '01', name: '첫 출발형', line: '아직 0원. 무서운 건 내년에도 0원인 것', weak: '시작을 미루는 버릇', match: '미국 직진형', enemy: '레버리지 질주형' },
  lev: { no: '02', name: '레버리지 질주형', line: '올라갈 땐 천재, 떨어질 땐 -60%', weak: '하락장 한 번이면 끝', match: '채권 방패형', enemy: '연금 장기전형' },
  stock: { no: '03', name: '개별주 사냥꾼형', line: '“내가 고른 종목은 달라”', weak: '물리면 존버', match: '고르게 나눈 형', enemy: '미국 직진형' },
  chip: { no: '04', name: '반도체 덕후형', line: '엔비디아 실적 날 잠 못 자는 사람', weak: '반도체가 쉬는 해', match: '미국 직진형', enemy: '배당 농부형' },
  div: { no: '05', name: '배당 농부형', line: '주가는 안 봐요. 입금 알림만 봐요', weak: '오르는 장에서 소외', match: '연금 장기전형', enemy: '레버리지 질주형' },
  bond: { no: '06', name: '채권 방패형', line: '안 잃는 게 버는 거라고 믿어요', weak: '너무 안전해서 덜 불어남', match: '레버리지 질주형', enemy: '반도체 덕후형' },
  pension: { no: '07', name: '연금 장기전형', line: '55세의 나에게 몰빵한 사람', weak: '지금 쓸 돈이 없음', match: '배당 농부형', enemy: '개별주 사냥꾼형' },
  kr: { no: '08', name: '한국 사랑형', line: '“국장 탈출은 지능순”? 끝까지 남는다', weak: '한국만 쉬는 해', match: '배당 농부형', enemy: '미국 직진형' },
  us: { no: '09', name: '미국 직진형', line: 'S&P500 말고는 다 잡주라고 봐요', weak: '환율이 떨어지는 해', match: '첫 출발형', enemy: '한국 사랑형' },
  bal: { no: '10', name: '고르게 나눈 형', line: '다 조금씩. 크게 잃지도 벌지도 않게', weak: '이도 저도 아닌 해', match: '개별주 사냥꾼형', enemy: '레버리지 질주형' },
};
const shareUrl = 'https://etforca.com/portfolio/';

/** 서버 stats 는 [이름, 값] 쌍·{label, value}·{이름: 값} 어느 꼴이든 받아 앞의 셋만 쓴다. */
export function statRows(stats: unknown): [string, string][] {
  const pair = (label: unknown, value: unknown): [string, string][] => (typeof value === 'string' || typeof value === 'number') && String(label).trim() ? [[String(label), String(value)]] : [];
  const rows = Array.isArray(stats) ? stats.flatMap(s => Array.isArray(s) ? pair(s[0], s[1]) : s && typeof s === 'object' && 'label' in s ? pair(s.label, (s as { value?: unknown }).value) : [])
    : stats && typeof stats === 'object' ? Object.entries(stats).flatMap(([k, v]) => pair(k, v)) : [];
  return rows.slice(0, 3);
}

function Card({ info, keyName, stats }: { info: TypeInfo; keyName: string; stats: [string, string][] }) {
  return <div className="r8-type-card" style={{ '--tc1': `var(--type-${keyName}-1)`, '--tc2': `var(--type-${keyName}-2)` } as CSSProperties}>
    <span className="r8-type-no" aria-hidden="true">{info.no}</span>
    <div className="r8-type-top"><span>나의 투자 유형</span><span>No.{info.no}</span></div>
    <div className="r8-type-art" aria-hidden="true"><span /><img src={orca} width="120" height="120" alt="" /></div>
    <div className="r8-type-name"><b>{info.name}</b><span>{info.line}</span></div>
    <dl className="r8-type-stats">{stats.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}<div data-weak><dt>약점</dt><dd>{info.weak}</dd></div></dl>
    <div className="r8-type-pair"><span><small>찰떡 궁합</small><b>{info.match}</b></span><span><small>상극</small><b>{info.enemy}</b></span></div>
    <span className="r8-type-foot">너는 무슨 유형? etforca.com</span>
  </div>;
}

function load(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = reject; img.src = src; });
}
// 인스타 스토리(1080×1920) 그림. 새 의존성 없이 캔버스에 카드와 같은 내용을 그린다. 금액은 넣지 않는다.
async function storyImage(info: TypeInfo, keyName: string, stats: [string, string][]) {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string) => css.getPropertyValue(name).trim();
  const font = token('--font');
  const canvas = document.createElement('canvas');
  canvas.width = 1080; canvas.height = 1920;
  const g = canvas.getContext('2d')!;
  const gradient = g.createLinearGradient(0, 0, 700, 1920);
  gradient.addColorStop(0, token(`--type-${keyName}-1`)); gradient.addColorStop(1, token(`--type-${keyName}-2`));
  g.fillStyle = gradient; g.fillRect(0, 0, 1080, 1920);
  await document.fonts.ready;
  const white = token('--color-surface');
  const text = (value: string, x: number, y: number, size: number, weight: number, align: CanvasTextAlign = 'center', alpha = 1, color = white) => {
    g.globalAlpha = alpha; g.fillStyle = color; g.textAlign = align; g.font = `${weight} ${size}px ${font}`; g.fillText(value, x, y); g.globalAlpha = 1;
  };
  text(info.no, 1110, 560, 420, 900, 'right', .08);
  text('나의 투자 유형', 110, 220, 42, 600, 'left', .8); text(`No.${info.no}`, 970, 220, 42, 600, 'right', .8);
  g.fillStyle = white; g.beginPath(); g.arc(540, 560, 230, 0, Math.PI * 2); g.fill();
  const image = await load(orca);
  g.save(); g.beginPath(); g.arc(540, 560, 190, 0, Math.PI * 2); g.clip(); g.drawImage(image, 350, 370, 380, 380); g.restore();
  text(info.name, 540, 960, 100, 900);
  text(info.line, 540, 1045, 46, 600);
  const rows = [...stats, ['약점', info.weak] as [string, string]];
  const top = 1120, height = rows.length * 84 + 40;
  g.globalAlpha = .2; g.fillStyle = token('--color-app'); g.beginPath(); g.roundRect(110, top, 860, height, 40); g.fill(); g.globalAlpha = 1;
  rows.forEach(([label, value], i) => {
    const y = top + 72 + i * 84;
    text(label, 150, y, 38, 400, 'left', .7);
    text(value, 930, y, 38, 700, 'right', 1, i === rows.length - 1 ? token('--color-on-dark-alert') : white);
  });
  const pairTop = top + height + 32;
  [['찰떡 궁합', info.match], ['상극', info.enemy]].forEach(([label, value], i) => {
    const x = 110 + i * 440;
    g.globalAlpha = .12; g.fillStyle = white; g.beginPath(); g.roundRect(x, pairTop, 420, 150, 32); g.fill(); g.globalAlpha = 1;
    text(label, x + 36, pairTop + 58, 32, 400, 'left', .65); text(value, x + 36, pairTop + 112, 40, 700, 'left');
  });
  text('너는 무슨 유형? etforca.com', 540, 1800, 40, 600, 'center', .8);
  return new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('IMAGE_FAILED')), 'image/png'));
}

export function TypeCardSheet({ open, card, analysisId, onClose }: { open: boolean; card: { key: string; stats?: unknown }; analysisId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const info = typeCards[card.key];
  const stats = statRows(card.stats);
  const file = typeof File !== 'undefined' ? new File([''], 'orca-type.png', { type: 'image/png' }) : null;
  const canShareFiles = !!file && !!navigator.canShare?.({ files: [file] });
  useEffect(() => {
    const element = dialog.current!;
    if (!open) { if (element.open) element.close(); return; }
    setStatus('');
    if (!element.open) element.showModal();
  }, [open]);
  async function story() {
    setBusy(true); setStatus('');
    try {
      const blob = await storyImage(info, card.key, stats);
      const image = new File([blob], `etforca-${card.key}.png`, { type: 'image/png' });
      if (canShareFiles && navigator.canShare({ files: [image] })) await navigator.share({ files: [image], title: info.name });
      else {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob); link.download = image.name; link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        setStatus('카드 그림을 저장했어요. 인스타 스토리에서 불러와 올려 주세요.');
      }
      trackResult('type_card_shared', { analysis_id: analysisId, method: 'story' });
    } catch (error) {
      if ((error as Error).name !== 'AbortError') setStatus('카드 그림을 만들지 못했어요. 다시 눌러 주세요.');
    } finally { setBusy(false); }
  }
  async function link() {
    setStatus('');
    try {
      if (navigator.share) await navigator.share({ title: 'ETF Orca 투자 유형', text: `나는 ${info.name}. 너는 무슨 유형?`, url: shareUrl });
      else { await navigator.clipboard.writeText(shareUrl); setStatus('링크를 복사했어요. 친구에게 보내 주세요.'); }
      trackResult('type_card_shared', { analysis_id: analysisId, method: 'link' });
    } catch (error) {
      if ((error as Error).name !== 'AbortError') setStatus('링크를 보내지 못했어요. 다시 눌러 주세요.');
    }
  }
  return <dialog ref={dialog} className="r8-type-sheet" aria-labelledby="r8-type-title" onClose={onClose}>
    {open && info && <>
      <header><h2 id="r8-type-title">내 투자 유형</h2><Button className="r8-type-close" aria-label="유형 카드 닫기" onClick={() => dialog.current!.close()}><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></Button></header>
      <Card info={info} keyName={card.key} stats={stats} />
      <div className="r8-type-actions">
        <Button className="r8-type-story" loading={busy} onClick={() => void story()}>{canShareFiles ? '인스타 스토리에 올리기' : '카드 그림 저장하기'}</Button>
        <Button className="r8-type-link" onClick={() => void link()}>친구 유형 맞혀 보기 · 링크 보내기</Button>
        <p role="status">{status || '금액은 카드에 나오지 않아요'}</p>
      </div>
    </>}
  </dialog>;
}
