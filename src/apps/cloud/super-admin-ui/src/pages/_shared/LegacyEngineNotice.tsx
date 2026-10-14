/* 옛 설명 엔진의 기록이라는 안내(ALPHA-1328).
 *
 * 가격 변동 분석 목록·종목·상세 화면은 옛 설명 엔진의 표(explanation_*)를 읽는다. 그 엔진은 10-01 이 마지막
 * 실행이라 그 뒤는 비어 있다. 새 분석 엔진 기준으로 다시 만들지 않고 사이드 메뉴에서 뺐다 — 주소로
 * 직접 들어온 사람에게 무엇을 보고 있는지와 새 분석 엔진의 상태가 어디 있는지를 밝힌다.
 */
import { Link } from 'react-router-dom';

export function LegacyEngineNotice() {
  return (
    <p className="card card-pad t-sm m-0" style={{ color: 'var(--fg-2)' }}>
      10-01까지의 옛 설명 엔진 기록입니다. 새 분석 엔진의 ETF별 상태는{' '}
      <Link to="/ops/chain?focus=etf-ledger">흐름 화면의 ETF 표</Link>에 있습니다.
    </p>
  );
}
