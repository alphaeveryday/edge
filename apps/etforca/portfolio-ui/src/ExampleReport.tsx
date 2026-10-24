import { useRef } from 'react';
import { Button } from './design-system/components';
import { trackAction } from './analytics';
import { Glyph } from './flow';
import example from './example-report.jpg';

// 입력을 시작하기 전에 받게 될 결과를 보여준다(이탈 분석: 가치를 확인하기 전에 떠남). 합성 예시 화면을 담은 이미지라 고객 API·탐색용 바가 없다.
export function ExampleReport() {
  const dialog = useRef<HTMLDialogElement>(null);
  return <>
    <Button className="fl-textlink" onClick={() => { dialog.current?.showModal(); trackAction('example_report_opened'); }}>예시 보기<Glyph name="right" size={16} /></Button>
    <dialog ref={dialog} className="followup-dialog example-dialog" aria-labelledby="example-title">
      <Button className="dialog-close" aria-label="예시 결과 닫기" onClick={() => dialog.current?.close()}>×</Button>
      <h2 id="example-title">이런 결과를 받아요</h2>
      <p>예시 포트폴리오로 만든 화면이에요. 실제 결과는 입력한 보유 종목으로 만들어요.</p>
      <img src={example} width={780} height={3910} loading="lazy" alt="예시 결과: 추천 포트폴리오 구성과 별점, 바꿀 종목과 비중, 계좌별 최종 투자 제안 금액, 추천 구성과 현재 구성의 과거 성과 비교" />
    </dialog>
  </>;
}
