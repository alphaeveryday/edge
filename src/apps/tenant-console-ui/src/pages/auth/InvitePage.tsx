/* 초대 수락 페이지 — /invite/:token */
import { useParams, useNavigate } from 'react-router-dom';
import { Icon } from '../../components';

export function InvitePage() {
  const { token } = useParams();
  const navigate = useNavigate();
  return (
    <div className="col gap22">
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 720, color: 'var(--n-900)' }}>초대 수락</h1>
        <p style={{ fontSize: 13.5, color: 'var(--n-500)', marginTop: 6 }}>
          한빛투자증권 EDGE Console 에 초대되었습니다.
        </p>
      </div>
      <div className="field">
        <label>초대 토큰</label>
        <input className="input mono" value={token ?? ''} readOnly />
      </div>
      <div className="field">
        <label>비밀번호 설정</label>
        <input className="input" type="password" placeholder="••••••••" />
      </div>
      <button className="btn btn-pri btn-lg btn-block" onClick={() => navigate('/onboarding')}>
        <Icon n="check" s={16} />
        가입 완료
      </button>
    </div>
  );
}
