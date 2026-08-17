# 블로그 게시용 차트 — 본문 어휘(인프로세스만/공유 캐시만/2계층) 라벨판
import json, statistics, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SP = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(SP, 'chartdata.json')))
OUT = os.path.expanduser('~/Developer/choyoungseo20.github.io/assets/img/posts')

SURFACE = '#fcfcfb'
INK = '#0b0b0b'; INK2 = '#52514e'; MUTED = '#898781'
GRID = '#e1e0d9'; BASELINE = '#c3c2b7'
C_NONE = '#898781'; C_CAF = '#2a78d6'; C_RED = '#eb6834'; C_TWO = '#1baf7a'
MODES = [('none', '캐시 없음', C_NONE), ('caffeine', '인프로세스만\n(Caffeine)', C_CAF),
         ('redis', '공유 캐시만\n(Redis)', C_RED), ('two-level', '2계층\n(L1+L2)', C_TWO)]

plt.rcParams.update({
    'font.family': 'Apple SD Gothic Neo',
    'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
    'text.color': INK, 'axes.edgecolor': BASELINE, 'axes.labelcolor': INK2,
    'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.linewidth': 1.0,
    'font.size': 11,
})

def style_ax(ax):
    for s in ('top', 'right', 'left'):
        ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color(BASELINE)
    ax.grid(axis='y', color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)

def med(vals): return statistics.median(vals)

# ============ 글2-01: 모드별 p95/p99 ============
fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), dpi=200)
for ax, key, title in [(axes[0], 'p95', 'p95'), (axes[1], 'p99', 'p99')]:
    for i, (mode, label, color) in enumerate(MODES):
        reps = [r[key] for r in D['phaseB'][mode]]
        m = med(reps)
        ax.bar(i, m, width=0.5, color=color, zorder=2)
        jit = [i - 0.12, i, i + 0.12][:len(reps)]
        ax.scatter(jit, reps, s=26, color=INK, zorder=4,
                   edgecolors=SURFACE, linewidths=1.4)
        ax.annotate(f'{m:.2f}', (i, max(reps)), xytext=(0, 10), textcoords='offset points',
                    ha='center', color=INK, fontsize=11.5, fontweight='bold')
    ax.set_xticks(range(4))
    ax.set_xticklabels([l for _, l, _ in MODES], color=INK2, fontsize=10)
    ax.set_ylim(0, 2.15)
    ax.set_title(title, color=INK, fontsize=13, fontweight='bold', loc='left', pad=10)
    style_ax(ax)
axes[0].set_ylabel('응답 지연 (ms)', fontsize=10)
fig.suptitle('고정 부하에서 캐시 전략별 응답 지연 — 전 모드 1~2ms, 지연 축 차이 없음',
             x=0.065, ha='left', fontsize=14.5, fontweight='bold', color=INK)
fig.text(0.065, 0.895, 'hot-key 3키 · 1,600 rps · API 4대 · 3분 × 3회  (막대=중앙값, 점=반복 run)',
         fontsize=10, color=INK2)
fig.tight_layout(rect=[0, 0, 1, 0.86])
fig.savefig(os.path.join(OUT, '2026-08-17-the-redis-experiment-that-removed-redis-01.png'),
            bbox_inches='tight')
plt.close(fig)

# ============ 글2-02: DB 오프로딩 ============
fig, ax = plt.subplots(figsize=(10, 4.4), dpi=200)
none_med = med([r['loaders_total'] for r in D['phaseB']['none']])
hitlab = {
    'none': '캐시 hit 0% — 전 요청 DB 직행',
    'caffeine': 'L1 hit 99.63%',
    'redis': 'L2 hit 99.93%',
    'two-level': 'L1 hit 99.63% · 잔여의 L2 hit 86%',
}
rows = list(reversed(MODES))
for i, (mode, lab, color) in enumerate(rows):
    reps = [r['loaders_total'] for r in D['phaseB'][mode]]
    m = med(reps)
    ax.plot([1, m], [i, i], color=GRID, linewidth=1, zorder=1)
    ax.scatter(reps, [i - 0.22]*len(reps), s=22, color=color, alpha=0.45, zorder=3)
    ax.scatter([m], [i], s=110, color=color, zorder=4, edgecolors=SURFACE, linewidths=1.6)
    mult = '' if mode == 'none' else f'   (캐시 없음 대비 ÷{none_med/m:,.0f})'
    ax.annotate(f'{m:,.0f}회{mult}', (m, i), xytext=(12, 0), textcoords='offset points',
                va='center', color=INK, fontsize=11.5, fontweight='bold')
ax.set_yticks(range(4))
ax.set_yticklabels([f'{lab.splitlines()[0]}\n{hitlab[mode]}' for mode, lab, _ in rows],
                   fontsize=10, color=INK2)
ax.set_xscale('log')
ax.set_xlim(80, 4e6)
ax.set_xticks([100, 1000, 10_000, 100_000, 1_000_000])
ax.set_xticklabels(['100', '1천', '1만', '10만', '100만'], fontsize=9.5)
ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
for s in ('top', 'right', 'left'):
    ax.spines[s].set_visible(False)
ax.spines['bottom'].set_color(BASELINE)
ax.grid(axis='x', color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
ax.tick_params(length=0)
ax.set_xlabel('3분간 DB loader 호출 수 (로그 눈금)', fontsize=10)
ax.set_title('캐시의 DB 오프로딩 — 지연이 아니라 DB 부하 축에서 갈린다',
             loc='left', fontsize=14.5, fontweight='bold', color=INK, pad=26)
ax.text(0, 1.06, 'hot-key 3키 · 1,600 rps · API 4대 · 3분 × 3회  (큰 점=중앙값, 반투명 점=반복 run · 공유 캐시 r1은 재기동 콜드 오염)',
        transform=ax.transAxes, fontsize=10, color=INK2)
fig.tight_layout()
fig.savefig(os.path.join(OUT, '2026-08-17-the-redis-experiment-that-removed-redis-02.png'),
            bbox_inches='tight')
plt.close(fig)

# ============ 글4-01: 워킹셋 스윕 ============
Ns = [100, 300, 800, 1088, 3000, 5000]
fig, ax = plt.subplots(figsize=(10, 5.2), dpi=200)
for mode, color, name in [('caffeine', C_CAF, '인프로세스만'), ('two-level', C_TWO, '2계층')]:
    meds, xs = [], []
    for n in Ns:
        reps = [r['p99'] for r in D['W'][mode][str(n)]]
        xs.append(n); meds.append(med(reps))
        if len(reps) > 1:
            ax.scatter([n]*len(reps), reps, s=18, color=color, alpha=0.45, zorder=3)
    ax.plot(xs, meds, color=color, linewidth=2, zorder=4,
            marker='o', markersize=6.5, markeredgecolor=SURFACE, markeredgewidth=1.4)
    ax.annotate(name, (xs[-1], meds[-1]), xytext=(10, 0), textcoords='offset points',
                va='center', color=color, fontsize=11.5, fontweight='bold')
    lbl = '12.0' if meds[-1] > 11.5 else f'{meds[-1]:.1f}'  # 글4 표(12.0ms 반올림)와 표기 통일
    ax.annotate(lbl, (xs[-1], meds[-1]), xytext=(0, 11), textcoords='offset points',
                ha='center', color=INK, fontsize=10.5, fontweight='bold')
ax.axvline(800, color=BASELINE, linewidth=1, linestyle=(0, (4, 4)), zorder=1)
ax.annotate('L1 임계점 N≈800\n(적중률 50%, 예측과 일치)', (800, 12.6), ha='center',
            color=INK2, fontsize=9.5)
ax.annotate('실제 유니버스\n1,088종 (hit 42%)', (1088, 0.35), ha='center',
            color=INK2, fontsize=9.5)
ax.annotate('miss마다 L2 왕복이 추가되는\n대워킹셋 역효과 (12.0 vs 2.4ms)', (3350, 8.6),
            ha='right', color=INK2, fontsize=9.5)
l1hits = {100: 88.8, 300: 72.6, 800: 50.0, 1088: 42.4, 3000: 21.0, 5000: 13.7}
ax.set_xscale('log')
ax.set_xticks(Ns)
ax.set_xticklabels([f'{n:,}\nL1 {l1hits[n]:.0f}%' for n in Ns], fontsize=9.5)
ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
ax.set_ylim(0, 13.5)
ax.set_xlim(88, 7200)
ax.set_ylabel('p99 지연 (ms)', fontsize=10)
ax.set_xlabel('워킹셋 키 수 N (로그 눈금)', fontsize=10)
style_ax(ax)
ax.set_title('워킹셋 크기 스윕 — L1 임계점을 지나면 2계층의 L2 왕복 비용이 꼬리를 키운다',
             loc='left', fontsize=14.5, fontweight='bold', color=INK, pad=26)
ax.text(0, 1.035, '균등 접근 · 1,600 rps · API 4대 · L1 2s + L2 3s  (N=800·1,088은 3분×3회 중앙값·반투명 점=반복, 나머지는 단일 2분 run)',
        transform=ax.transAxes, fontsize=10, color=INK2)
fig.tight_layout()
fig.savefig(os.path.join(OUT, '2026-08-17-measuring-the-cache-boundary-01.png'),
            bbox_inches='tight')
plt.close(fig)
print('done')
