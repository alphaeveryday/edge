/* 분석 설정 탭 — universe 는 도메인 hook 으로 가져오고, 편집은 컴포넌트 로컬 상태로 둔다
 * (이 화면의 유니버스 편집은 repository 쓰기 없음).
 */
import { useEffect, useState } from 'react';
import { Icon, Modal, Panel, Segmented, toast } from '../../components';
import { useUniverse } from '../../domains/applications';

const CAND = [
  'TIGER 미국나스닥100',
  'KODEX 코스닥150',
  'TIGER 차이나전기차',
  'KODEX 골드선물',
  'KBSTAR 국고채',
  'ARIRANG 고배당주',
];

export function AnalysisTab() {
  const { universe } = useUniverse();

  const [cats, setCats] = useState<Record<string, boolean>>({
    실적: true,
    공시: true,
    정책: true,
    거시: true,
    지수: false,
  });
  const [thr, setThr] = useState(40);
  const [conf, setConf] = useState('중간');
  const [tone, setTone] = useState('간결 요약');
  const [uni, setUni] = useState<string[]>([]);
  const [modal, setModal] = useState(false);
  const [pick, setPick] = useState('');

  // hook 으로 받은 초기 유니버스를 로컬 상태에 한 번 반영한다.
  useEffect(() => {
    setUni(universe);
  }, [universe]);

  const candidates = CAND.filter((c) => !uni.includes(c));

  const addEtf = () => {
    if (!pick) return;
    setUni([...uni, pick]);
    toast(`${pick} 추가됨`);
    setModal(false);
    setPick('');
  };

  return (
    <div>
      <Panel
        title="분석 엔진 설정"
        sub="이벤트 카테고리·임계치·대상 유니버스"
        actions={
          <button
            className="btn btn-pri btn-sm"
            onClick={() => toast('분석 설정이 저장되었습니다')}
          >
            <Icon n="check" s={15} />
            설정 저장
          </button>
        }
      >
        <div className="col gap28">
          <div className="field">
            <label>이벤트 카테고리</label>
            <div className="row gap8" style={{ flexWrap: 'wrap' }}>
              {Object.keys(cats).map((c) => {
                const on = cats[c];
                return (
                  <button
                    key={c}
                    className={'pill-tog' + (on ? ' on' : '')}
                    onClick={() => setCats((p) => ({ ...p, [c]: !p[c] }))}
                  >
                    {on && <Icon n="check" s={14} />}
                    {c}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="two-eq" style={{ gap: 28 }}>
            <div className="field">
              <label>
                최소 영향도{' '}
                <span className="num" style={{ color: 'var(--p-700)', fontWeight: 700 }}>
                  {thr}
                </span>
              </label>
              <input
                className="rng"
                type="range"
                min={0}
                max={100}
                value={thr}
                onChange={(e) => setThr(Number(e.target.value))}
              />
              <div className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>
                이 값 이상일 때만 위젯에 노출 · 0 ─ 100
              </div>
            </div>
            <div className="field">
              <label>최소 신뢰도</label>
              <Segmented opts={['낮음', '중간', '높음']} value={conf} onChange={setConf} />
            </div>
          </div>

          <div className="field">
            <label>콘텐츠 톤</label>
            <Segmented
              opts={['중립·사실 전달', '간결 요약', '상세 해설']}
              value={tone}
              onChange={setTone}
            />
          </div>

          <div className="field">
            <div className="row spread" style={{ alignItems: 'center' }}>
              <label style={{ margin: 0 }}>대상 ETF 유니버스</label>
              <button className="btn btn-ghost btn-sm" onClick={() => setModal(true)}>
                <Icon n="plus" s={15} />
                ETF 추가
              </button>
            </div>
            <div className="row gap8" style={{ flexWrap: 'wrap', marginTop: 10 }}>
              {uni.map((c) => (
                <span key={c} className="tag-chip">
                  {c}
                  <button
                    className="chip-x"
                    onClick={() => setUni(uni.filter((x) => x !== c))}
                    aria-label={`${c} 제거`}
                  >
                    <Icon n="x" s={12} />
                  </button>
                </span>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
              전체 유니버스(312종) 또는 워치리스트로 제한할 수 있습니다.
            </div>
          </div>
        </div>
      </Panel>

      {modal && (
        <Modal
          title="ETF 추가"
          onClose={() => setModal(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setModal(false)}>
                취소
              </button>
              <button className="btn btn-pri" disabled={!pick} onClick={addEtf}>
                추가
              </button>
            </>
          }
        >
          <div className="col gap8">
            {candidates.map((c) => (
              <button
                key={c}
                className={'select-row' + (pick === c ? ' on' : '')}
                onClick={() => setPick(c)}
              >
                <span className={'radio' + (pick === c ? ' on' : '')} />
                {c}
              </button>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
