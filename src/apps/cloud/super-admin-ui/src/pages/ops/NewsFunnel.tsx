/* 뉴스 처리 퍼널 — 당일 단계별 처리량과 감소 (ALPHA-738).
 *
 * **추이가 아니라 하루의 단면**이라 산출 추이 화면에서 데이터 화면(뉴스 데이터셋)으로 옮겼다.
 *
 * 이름을 "계보"라 하지 않는다 — 레코드 사이의 실제 lineage 가 아니라 **단계별 집계**다.
 * 레코드 관계(분석 결과 ↔ assertion ↔ source event ↔ 원문)는 근거·계보 화면 소관이고,
 * 이 표를 그쪽에 복제하지 않는다.
 *
 * 배지·설명은 **구조화된 필드에서 결정적으로** 만든다. 완성된 문장을 셀에 박아 두면 표가
 * 문단이 되고, 값이 바뀌어도 문장은 그대로 남는다(상한 도달 런이 0인데 "상한 절단값"이 남는 일).
 */
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { Absent, F, fmt } from './shared';
import { InfoPopover } from '../_shared/InfoPopover';
import '../../styles/ops.css';

type Step = (typeof F.news_funnel)[number];

interface StepNote {
  /** 짧은 상태 — 판단에 필요한 것만 */
  badge: string;
  /** 조치 필요성에 따라 톤을 가른다 — 전부 경고색으로 칠하지 않는다 */
  tone: BadgeTone;
  /** 정적 정의·계산식은 팝오버로 */
  detail: string[];
}

/** 구조화 필드 → 배지·설명. 여기가 유일한 판정 지점이다 */
export function stepNotes(s: Step): StepNote[] {
  const out: StepNote[] = [];

  if (s.maxPages != null && s.pageSize != null) {
    const cap = s.maxPages * s.pageSize;
    const atLimit = s.runsAtLimit ?? 0;
    const detail = [
      `런당 최대 수집량은 MAX_PAGES(${s.maxPages}) × ${s.pageSize} = ${fmt(cap)}건입니다.`,
      `${fmt(cap)}건은 기대 수집량이 아니라 수집 상한입니다.`,
      ...(s.totalRuns != null ? [`오늘은 ${s.totalRuns}개 런 중 ${atLimit}개가 상한에 도달했습니다.`] : []),
      ...(s.includesWindowOverlap ? ['수집량에는 창 겹침이 포함됩니다.'] : []),
      ...(s.includesDuplicates ? ['수집량에는 중복이 포함됩니다.'] : []),
    ];
    /* 상한에 실제로 닿았을 때만 상태 배지다 — 안 닿았으면 상한은 정적 설명일 뿐이다 */
    if (atLimit > 0) out.push({ badge: '수집 상한 도달', tone: 'warn', detail });
    else out.push({ badge: '수집 상한 있음', tone: 'neutral', detail });
  }

  if (s.includesDuplicates && s.maxPages == null) {
    out.push({
      badge: '중복 포함',
      tone: 'neutral',
      detail: ['이 단계 값에는 중복이 포함됩니다 — 다음 단계의 감소는 유실이 아닙니다.'],
    });
  }

  if (s.dedupKey) {
    out.push({
      badge: '중복 제거',
      tone: 'neutral',
      detail: [`${s.dedupKey} 로 중복을 제거한 뒤의 값입니다.`, '앞 단계와의 차이는 유실이 아닙니다.'],
    });
  }

  if (s.timestampAxis) {
    out.push({
      badge: '축 차이',
      tone: 'neutral',
      detail: [
        `앞 단계와 시각 축이 다릅니다 — ${s.timestampAxis}.`,
        '축이 다르면 값이 어긋나는 것이 정상이고, 그 차이를 유실로 세지 않습니다.',
      ],
    });
  }

  if (s.universeFilter) {
    out.push({
      badge: '유니버스 필터',
      tone: 'warn',
      detail: [
        '유니버스에 없는 기사는 여기서 빠집니다(assemble 단계의 필터).',
        '앞 단계의 감소는 중복 제거·축 차이지만, 이 단계의 감소는 실제 탈락입니다.',
      ],
    });
  }

  if (s.missingReasonInstrumentation === false) {
    out.push({
      badge: '원인 미계측',
      tone: 'neutral',
      detail: [
        '없음의 사유가 한 통입니다 — NO_EVENT · 추출 실패 · 미실행을 가르는 계측이 없습니다.',
        '그래서 이 단계의 감소를 실패로 단정하지 않습니다.',
      ],
    });
  }

  return out;
}

export function NewsFunnel() {
  const funnel = F.news_funnel;
  return (
    <div className="card" id="news-funnel">
      <div className="card-head">
        <span className="t-label">뉴스 처리 퍼널</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          거래일 {F.meta.today} · 단계별 처리량과 감소
          <InfoPopover
            label="뉴스 처리 퍼널"
            title="뉴스 처리 퍼널"
            text={
              '레코드 사이의 lineage 가 아니라 **단계별 집계**입니다 — 그래서 "계보"라 부르지 않습니다.\n' +
              '레코드 관계(분석 결과 ↔ assertion ↔ source event ↔ 원문)는 근거·계보 화면이 답합니다.\n\n' +
              '단계마다 단위가 다릅니다(기사 · 문서 · event) — 빼기 전에 각 행의 ⓘ 를 보세요.\n' +
              '중복 제거·축 차이로 인한 감소는 유실이 아닙니다.'
            }
          />
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr>
              <th>단계</th>
              <th className="num">값</th>
              <th>단위</th>
              <th className="num">직전 대비</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {funnel.map((x, i) => {
              const prev = i > 0 ? funnel[i - 1].value : null;
              const drop = prev != null && x.value < prev ? prev - x.value : 0;
              const notes = stepNotes(x);
              return (
                <tr key={x.stage} id={'funnel-' + i}>
                  <td>{x.stage}</td>
                  <td className="num">{fmt(x.value)}</td>
                  <td className="col-muted">{x.unit}</td>
                  <td className="num" style={drop ? { color: 'var(--warn)' } : undefined}>
                    {prev == null ? <Absent kind="none" /> : drop ? `−${fmt(drop)}` : '·'}
                  </td>
                  {/* 긴 문장을 셀에 넣지 않는다 — 짧은 배지 + ⓘ */}
                  <td>
                    <span className="ops-badges">
                      {notes.map((n) => (
                        <span key={n.badge} className="ops-badge-with-info">
                          <StatusBadge tone={n.tone}>{n.badge}</StatusBadge>
                          <InfoPopover label={n.badge} title={n.badge} text={n.detail.join('\n')} />
                        </span>
                      ))}
                      {notes.length === 0 && x.note && (
                        <InfoPopover label={x.stage} title={x.stage} text={x.note} />
                      )}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
