import { ApiError } from '../../api/client';

/** 쿼리 실패를 빈 화면으로 위장하지 않는다 (Rule 12).
 * 403 은 일시 장애가 아니라 권한/접속 위치 문제라 문구를 가른다 — 로그인 화면의
 * 차단 배너(WAF/망 제한)와 같은 축. 그 외에는 재시도 안내가 맞다. */
export function LoadError({ error }: { error?: unknown }) {
  const forbidden = error instanceof ApiError && error.status === 403;
  return (
    <div className="card card-pad" style={{ fontSize: 12, color: 'var(--down)' }}>
      {forbidden
        ? '접근이 거부되었습니다(403). 운영자 권한 또는 접속 위치(망 차단) 문제일 수 있습니다 — 관리자에게 문의해 주세요.'
        : '데이터를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.'}
    </div>
  );
}
