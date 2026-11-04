import { useEffect, useRef } from 'react';
import { Button } from './design-system/components';
import icon from '../../app-ui/assets/icon.png';
import shot1 from './assets/app-store/ETF-Orca-01.png';
import shot2 from './assets/app-store/ETF-Orca-02.png';
import shot3 from './assets/app-store/ETF-Orca-03.png';
import shot4 from './assets/app-store/ETF-Orca-04.png';
import shot6 from './assets/app-store/ETF-Orca-06.png';
import shot7 from './assets/app-store/ETF-Orca-07.png';

const screenshots = [shot1, shot2, shot3, shot4, shot6, shot7];
const storeUrl = 'https://apps.apple.com/kr/app/etf-orca/id6816826436';

export function AppDownloadSheet({ open, onClose, onStoreClick }: { open: boolean; onClose: () => void; onStoreClick?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [open]);
  return <dialog ref={dialog} className="app-download-sheet" aria-labelledby="app-download-title" aria-describedby="app-download-description" onCancel={onClose}>
    {open && <>
      <header className="app-download-header">
        <span className="app-download-grabber" aria-hidden="true" />
        <div className="app-download-identity"><img src={icon} width="60" height="60" alt="" /><div><strong>ETF ORCA</strong><span>내 ETF를 이해하는 가장 쉬운 습관</span></div></div>
        <Button className="app-download-close" aria-label="앱 다운로드 안내 닫기" onClick={onClose}>×</Button>
      </header>
      <div className="app-download-content">
        <div className="app-download-copy">
          <p className="app-download-eyebrow">뉴스는 ORCA가 읽을게요</p>
          <h2 id="app-download-title">내 ETF에 무슨 일이?<br />핵심만 매일 확인해요.</h2>
          <p id="app-download-description">쏟아지는 뉴스를 하나씩 찾지 않아도 괜찮아요.<br />보유 ETF의 핵심 이슈와 영향을 매일 정리해 드려요.</p>
          <div className="app-download-tags"><span>핵심 이슈</span><span>가격이 움직인 이유</span><span>AI 분석</span></div>
        </div>
        <div className="app-download-gallery" tabIndex={0} role="region" aria-label="ETF ORCA 앱 소개 이미지 6장, 좌우로 넘겨 보세요">
          {screenshots.map((src, index) => <img key={src} src={src} width="1320" height="2868" loading={index < 2 ? 'eager' : 'lazy'} alt={`ETF ORCA 앱 소개 ${index + 1}`} />)}
        </div>
      </div>
      <footer className="app-download-footer">
        <a className="ds-button app-download-cta" data-variant="primary" href={storeUrl} target="_blank" rel="noopener noreferrer" onClick={() => { onStoreClick?.(); onClose(); }}>App Store에서 무료로 받기 <span aria-hidden="true">↗</span></a>
        <div><span>iPhone용 앱 · App Store로 이동해요</span><Button onClick={onClose}>나중에 볼게요</Button></div>
      </footer>
    </>}
  </dialog>;
}
