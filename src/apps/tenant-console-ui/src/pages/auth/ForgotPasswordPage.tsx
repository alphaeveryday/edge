/* 비밀번호 찾기 페이지 — /forgot-password */
import { Link } from 'react-router-dom';
import { Icon, toast } from '../../components';

export function ForgotPasswordPage() {
  return (
    <div className="col gap22">
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 720, color: 'var(--n-900)' }}>비밀번호 재설정</h1>
        <p style={{ fontSize: 13.5, color: 'var(--n-500)', marginTop: 6 }}>
          가입한 이메일로 재설정 링크를 보내드립니다.
        </p>
      </div>
      <div className="field">
        <label>이메일</label>
        <input className="input mono" type="email" placeholder="name@company.com" />
      </div>
      <button
        className="btn btn-pri btn-lg btn-block"
        onClick={() => toast('재설정 링크를 전송했습니다')}
      >
        <Icon n="mail" s={16} />
        링크 보내기
      </button>
      <Link to="/login" className="hint" style={{ textAlign: 'center' }}>
        로그인으로 돌아가기
      </Link>
    </div>
  );
}
