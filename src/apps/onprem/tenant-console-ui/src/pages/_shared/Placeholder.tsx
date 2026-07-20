/* 아직 도메인이 붙지 않은 화면용 공용 플레이스홀더.
 * 3단계에서 members 와 동일한 데이터 레이어 패턴으로 교체된다.
 */
import { Icon, PageHeader } from '../../components';

export function Placeholder({
  title,
  desc,
  icon = 'list',
}: {
  title: string;
  desc?: string;
  icon?: string;
}) {
  return (
    <div>
      <PageHeader title={title} desc={desc} />
      <div className="card">
        <div className="placeholder">
          <div className="empty-ico">
            <Icon n={icon} s={26} />
          </div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 650, color: 'var(--n-700)' }}>준비 중</div>
            <p style={{ fontSize: 13, marginTop: 6, maxWidth: 420 }}>
              이 화면은 다음 단계에서 <b>members 와 동일한 도메인 패턴</b>
              (types · repository · mock · real · hook)으로 구현됩니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
