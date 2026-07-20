/* 웹훅 탭 — applications 도메인 hook(useWebhooks)만 의존한다 (mock 직접 import 없음). */
import { useState } from 'react';
import { Badge, Icon, Modal, Panel, Segmented, toast } from '../../components';
import { useWebhooks } from '../../domains/applications';

const LOG_ROWS: [string, string, string, 'ok' | 'bad'][] = [
  ['14:32:08', '200', 'analysis.created', 'ok'],
  ['14:21:44', '200', 'analysis.created', 'ok'],
  ['13:58:10', '200', 'analysis.updated', 'ok'],
  ['13:30:02', '503', 'analysis.created', 'bad'],
  ['13:29:51', '200', 'analysis.created', 'ok'],
];

export function WebhooksTab() {
  const { webhooks, addWebhook } = useWebhooks();

  const [addOpen, setAddOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [evt, setEvt] = useState('analysis.created');
  const [logUrl, setLogUrl] = useState<string | null>(null);

  const submitAdd = async () => {
    if (!url.trim()) return;
    await addWebhook({ url: url.trim(), events: evt });
    setAddOpen(false);
    setUrl('');
    toast('엔드포인트가 추가되었습니다');
  };

  return (
    <div>
      <Panel
        title="웹훅"
        pad={false}
        actions={
          <button className="btn btn-pri btn-sm" onClick={() => setAddOpen(true)}>
            <Icon n="plus" s={15} />
            엔드포인트 추가
          </button>
        }
      >
        <div className="tbl-scroll">
          <table className="tbl hover">
            <thead>
              <tr>
                <th>엔드포인트</th>
                <th>구독 이벤트</th>
                <th>상태</th>
                <th>성공률</th>
                <th className="r">로그</th>
              </tr>
            </thead>
            <tbody>
              {webhooks.map((w) => (
                <tr key={w.url}>
                  <td>
                    <div
                      className="mono"
                      title={w.url}
                      style={{
                        fontSize: 12,
                        maxWidth: 178,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {w.url}
                    </div>
                  </td>
                  <td className="mono" style={{ fontSize: 12, color: 'var(--n-500)' }}>
                    {w.events}
                  </td>
                  <td>
                    <Badge tone={w.status === '활성' ? 'ok' : 'warn'} dot>
                      {w.status}
                    </Badge>
                  </td>
                  <td
                    className="num"
                    style={{ color: parseFloat(w.rate) < 95 ? 'var(--warn)' : undefined }}
                  >
                    {w.rate}
                  </td>
                  <td className="r">
                    <button
                      className="btn btn-quiet btn-sm"
                      onClick={() => setLogUrl(w.url)}
                      title="전송 로그"
                    >
                      <Icon n="list" s={14} />
                      전송 로그
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="재시도 정책"
        style={{ marginTop: 20 }}
        actions={
          <button
            className="btn btn-pri btn-sm"
            onClick={() => toast('재시도 정책이 저장되었습니다')}
          >
            <Icon n="check" s={15} />
            저장
          </button>
        }
      >
        <div className="three-col">
          <div className="field">
            <label>최대 재시도</label>
            <select className="input">
              <option>5회</option>
              <option>3회</option>
              <option>8회</option>
            </select>
          </div>
          <div className="field">
            <label>백오프</label>
            <select className="input">
              <option>지수 (2ⁿ)</option>
              <option>선형</option>
              <option>고정</option>
            </select>
          </div>
          <div className="field">
            <label>타임아웃</label>
            <select className="input">
              <option>10초</option>
              <option>5초</option>
              <option>30초</option>
            </select>
          </div>
        </div>
      </Panel>

      {addOpen && (
        <Modal
          title="엔드포인트 추가"
          onClose={() => setAddOpen(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setAddOpen(false)}>
                취소
              </button>
              <button className="btn btn-pri" disabled={!url.trim()} onClick={submitAdd}>
                <Icon n="plus" s={15} />
                추가
              </button>
            </>
          }
        >
          <div className="col gap18">
            <div className="field">
              <label>엔드포인트 URL</label>
              <input
                className="input mono"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://api.example.com/hooks"
                autoFocus
              />
            </div>
            <div className="field">
              <label>구독 이벤트</label>
              <Segmented
                opts={['analysis.created', 'analysis.updated', 'error.spike']}
                value={evt}
                onChange={setEvt}
                block
              />
            </div>
          </div>
        </Modal>
      )}

      {logUrl && (
        <Modal title="전송 로그" sub={logUrl} w={520} onClose={() => setLogUrl(null)}>
          <div className="col gap8">
            {LOG_ROWS.map((r, i) => (
              <div key={i} className="lrow">
                <span className="mono" style={{ fontSize: 12, color: 'var(--n-500)', width: 70 }}>
                  {r[0]}
                </span>
                <span className="mono" style={{ flex: 1, fontSize: 12.5 }}>
                  {r[2]}
                </span>
                <Badge tone={r[3] === 'ok' ? 'ok' : 'bad'}>{r[1]}</Badge>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
