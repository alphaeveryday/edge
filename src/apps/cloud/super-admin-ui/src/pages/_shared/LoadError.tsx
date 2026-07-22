/** 쿼리 실패를 빈 화면으로 위장하지 않는다 (Rule 12) */
export function LoadError() {
  return (
    <div className="card card-pad" style={{ fontSize: 12, color: 'var(--down)' }}>
      데이터를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.
    </div>
  );
}
