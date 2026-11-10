import qrcode from 'qrcode-generator';
import { useMemo } from 'react';
import { phoneUrl } from './room.ts';

/** [휴대폰 연결] — 휴대폰 카메라로 찍을 QR 과 주소. 연결은 유지된 채 창만 닫을 수 있다. */
export function PairDialog(p: { room: string; onClose: () => void; onUnlink: () => void }) {
  const url = phoneUrl(p.room);
  const svg = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    return qr.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
  }, [url]);
  return (
    <div className="pair-back" onClick={p.onClose}>
      <div className="pair" role="dialog" aria-label="휴대폰 연결" onClick={(e) => e.stopPropagation()}>
        <div className="pair-title">휴대폰으로 QR 을 찍어 주세요</div>
        <div className="pair-sub">휴대폰에 전화 화면이 열립니다. 거기서 [전화]를 누르면 통화를 듣고, 종목 탭은 이 콘솔에 뜹니다.</div>
        <div className="pair-qr" dangerouslySetInnerHTML={{ __html: svg }} />
        <div className="pair-url">{url}</div>
        <div className="pair-actions">
          <button type="button" className="btn" onClick={p.onUnlink}>
            연결 끊기
          </button>
          <button type="button" className="btn btn-primary" onClick={p.onClose}>
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
