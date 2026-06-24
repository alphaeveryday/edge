/* 로그인 페이지. 인증 도메인은 아직 없으므로 제출 시 대시보드로 이동만 한다.
 * 추후 auth/session 도메인이 생기면 동일한 repository/hook 패턴으로 교체한다.
 */
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Icon } from '../../components';

export function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('dohyun@hanbit.co.kr');
  const [pw, setPw] = useState('');

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate('/dashboard');
  };

  return (
    <form className="col gap22" onSubmit={submit}>
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 720, letterSpacing: '-.02em', color: 'var(--n-900)' }}>
          콘솔 로그인
        </h1>
        <p style={{ fontSize: 13.5, color: 'var(--n-500)', marginTop: 6 }}>
          조직 계정으로 EDGE Console 에 접속하세요.
        </p>
      </div>

      <div className="field">
        <label>이메일</label>
        <input
          className="input mono"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com"
        />
      </div>

      <div className="field">
        <label>비밀번호</label>
        <input
          className="input"
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="••••••••"
        />
        <Link to="/forgot-password" className="hint" style={{ alignSelf: 'flex-end' }}>
          비밀번호를 잊으셨나요?
        </Link>
      </div>

      <button className="btn btn-pri btn-lg btn-block" type="submit">
        <Icon n="logout" s={16} />
        로그인
      </button>
    </form>
  );
}
