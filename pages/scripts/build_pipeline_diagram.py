#!/usr/bin/env python3
"""drawio → 인터랙티브 다이어그램 HTML (SVG 내장형).

1~4번 다이어그램은 drawio 셀을 HTML 상자로 재구성했지만, 파이프라인 그림처럼
AWS 아이콘·서식 라벨이 많은 원본은 그 방식으로는 깨진다. 여기서는 draw.io CLI 가
export 한 SVG 를 그대로 품고(셀 id 가 `data-cell-id` 로 남는다), drawio XML 의
간선(source→target)만 읽어 클릭·호버 강조를 얹는다.

    python pages/scripts/build_pipeline_diagram.py <원본.drawio> <출력.html> --title "…" [--hide 셀id,셀id]

`--hide` 는 도면 안의 제목 같은 장식 셀을 뷰어에서만 뺀다 — 원본은 손대지 않고 임시 사본에서 지운 뒤 export 한다.
대상 없이 점으로 끝나는 화살표(예: DeepSeek 아이콘을 가리키는 것)는 끝점이 놓인 상자에 연결한 것으로 본다.
draw.io 데스크톱 CLI 가 필요하다 (DRAWIO_BIN 으로 경로 지정, 기본은 macOS 앱 경로).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

DRAWIO_BIN = os.environ.get("DRAWIO_BIN", "/Applications/draw.io.app/Contents/MacOS/draw.io")


def load(drawio_path: str, hide: set[str]) -> ET.ElementTree:
    tree = ET.parse(drawio_path)
    cells = tree.getroot().findall(".//mxCell")
    if not cells:
        raise SystemExit(f"{drawio_path}: mxCell 이 없다 — 압축 저장된 drawio 는 지원하지 않는다(draw.io 에서 '압축 해제'로 저장)")
    found = {c.get("id") for c in cells}
    if hide - found:
        raise SystemExit(f"--hide 셀이 도면에 없다: {sorted(hide - found)}")
    for parent in tree.getroot().iter():
        for c in [c for c in parent if c.tag == "mxCell" and c.get("id") in hide]:
            parent.remove(c)
    return tree


def export_svg(tree: ET.ElementTree) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "d.drawio"), os.path.join(tmp, "d.svg")
        tree.write(src, encoding="utf-8", xml_declaration=True)  # 사본 — 원본 drawio 는 그대로
        subprocess.run(
            [DRAWIO_BIN, "-x", "-f", "svg", "--theme", "light", "--embed-svg-images", "-o", out, src],
            check=True, capture_output=True,
        )
        with open(out, encoding="utf-8") as f:
            return f.read()


def read_graph(tree: ET.ElementTree) -> dict:
    root = tree.getroot()
    cells = root.findall(".//mxCell")
    layers = {c.get("id") for c in cells if c.get("parent") == "0"}
    boxes, edges = {}, []
    by_id = {c.get("id"): c for c in cells}
    own = {}  # 장식 셀 → 소유자(담고 있는 인터랙티브 셀, 또는 라벨이 달린 간선). 소유자가 강조되면 함께 살아남는다.
    labels = []
    for c in cells:
        parent = by_id.get(c.get("parent"))
        if c.get("vertex") == "1" and parent is not None and parent.get("edge") == "1":
            labels.append(c.get("id")); own[c.get("id")] = parent.get("id")  # 간선 라벨 셀
            continue
        if c.get("id") == "0" or c.get("id") in layers:
            continue  # 루트·레이어 셀
        if c.get("parent") not in layers:
            raise SystemExit(f"셀 {c.get('id')} 의 부모 {c.get('parent')} 가 레이어가 아니다 — 그룹 안 셀은 지원하지 않는다(그룹 해제 후 저장)")
        if c.get("edge") == "1":
            edges.append(c)
        elif c.get("vertex") == "1":
            g = c.find("mxGeometry")
            boxes[c.get("id")] = tuple(float(g.get(k, 0)) for k in ("x", "y", "width", "height"))

    def at_point(pt):  # 점을 담는 가장 작은 상자 — 대상 없이 점으로 끝난 화살표용. 담는 상자가 없을 때만 30px 근방을 본다
        for pad in (0, 30):
            best = None
            for bid, (x, y, w, h) in boxes.items():
                if x - pad <= pt[0] <= x + w + pad and y - pad <= pt[1] <= y + h + pad:
                    if best is None or w * h < boxes[best][2] * boxes[best][3]:
                        best = bid
            if best:
                return best
        return None

    resolved = []
    for e in edges:
        ends = []
        for attr, as_ in (("source", "sourcePoint"), ("target", "targetPoint")):
            end = e.get(attr)
            if not end:
                pt = e.find(f'mxGeometry/mxPoint[@as="{as_}"]')
                end = at_point((float(pt.get("x", 0)), float(pt.get("y", 0)))) if pt is not None else None
            ends.append(end)
        if ends[0] and ends[1] and ends[0] != ends[1]:
            resolved.append([e.get("id"), ends[0], ends[1]])
    edges = resolved
    conn = {a for _, a, _ in edges} | {b for _, _, b in edges}
    # 모든 셀의 소유자 = 자기를 담는 가장 작은(더 큰) 인터랙티브 셀. 소유자가 강조되면 안쪽이 통째로 따라간다.
    for cid, (x, y, w, h) in boxes.items():
        best = None
        for oid in conn:
            if oid == cid or oid not in boxes:
                continue
            ox, oy, ow, oh = boxes[oid]
            if ox <= x and oy <= y and x + w <= ox + ow and y + h <= oy + oh and ow * oh > w * h:
                if best is None or ow * oh < boxes[best][2] * boxes[best][3]:
                    best = oid
        if best:
            own[cid] = best
    return {"cells": list(boxes) + labels, "edges": edges, "conn": sorted(conn), "own": own}


def slim_svg(svg: str) -> tuple[str, float, float]:
    """prolog 제거, HTML 라벨의 래스터 폴백(<switch> 안 foreignObject 뒤) 삭제, 안 쓰는 <symbol> 삭제."""
    svg = svg[svg.index("<svg"):]
    svg = re.sub(r"</foreignObject>.*?</switch>", "</foreignObject></switch>", svg, flags=re.S)
    for sym in re.findall(r'<symbol id="([^"]+)"', svg):
        if f'href="#{sym}"' not in svg:
            svg = re.sub(rf'<symbol id="{re.escape(sym)}".*?</symbol>', "", svg, flags=re.S)
    # drawio 가 끝에 붙이는 "Text is not SVG" 안내 switch — 브라우저에선 안 보이지만 치운다
    svg = re.sub(r"<switch><g requiredFeatures=[^>]*/>.*?</switch>", "", svg, flags=re.S)
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    return svg, float(m.group(1)), float(m.group(2))


TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
/* 화이트 고정 — 포털 다이어그램 공통. 노드 색은 drawio 원본 유지 */
:root{--bg:#ffffff;--brand:#00309f;--fg:#1c2333}
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{background:var(--bg);overflow:hidden}
#viewport{width:100%;height:100%;overflow:auto;cursor:grab;display:flex}
#canvas{margin:auto;padding:16px}   /* flex + margin:auto — 작으면 가운데, 크면 스크롤(왼쪽 잘림 없음) */
#canvas>svg{display:block}
.cell{transition:opacity .25s,filter .25s}
.node,.node-part{cursor:pointer}
.node:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.edge{pointer-events:none}
.edge path{transition:stroke .25s,stroke-width .25s}
body.focus .cell{opacity:.14}
body.focus .cell.on,body.focus .cell.on-c{opacity:1}
body.focus .cell.on:not(.edge),body.focus .cell.on-c:not(.edge){filter:drop-shadow(0 0 1.5px var(--brand))}
body.focus .cell.sel{filter:drop-shadow(0 0 2px var(--brand)) drop-shadow(0 2px 8px rgba(0,48,159,.4))}
body.focus .edge.on path{stroke:var(--brand)!important}
body.focus .edge.on path[fill="none"]{stroke-width:3.2}
body.focus .edge.on path:not([fill="none"]){fill:var(--brand)!important}
body.peek .cell{opacity:.45}
body.peek .cell.pk-on,body.peek .cell.pk-c{opacity:1}
body.peek .cell.pk-on:not(.edge),body.peek .cell.pk-c:not(.edge){filter:drop-shadow(0 0 1.5px var(--brand))}
body.peek .edge.pk-on path{stroke:var(--brand)!important}
body.peek .edge.pk-on path[fill="none"]{stroke-width:2.8}
body.peek .edge.pk-on path:not([fill="none"]){fill:var(--brand)!important}
#hud{position:fixed;right:14px;bottom:14px;display:flex;gap:6px;z-index:10}
#hud button{width:34px;height:34px;border:1px solid #c3cbdd;border-radius:8px;background:#fff;
  color:var(--fg);font-size:16px;cursor:pointer}
@media (prefers-reduced-motion: reduce){.cell,.edge path{transition:none}}
</style></head><body>
<div id="viewport"><div id="canvas">__SVG__</div></div>
<div id="hud"><button id="zout" aria-label="축소">−</button><button id="zfit" aria-label="화면 맞춤">⊡</button><button id="zin" aria-label="확대">+</button></div>
<script>
const GRAPH=__GRAPH__;
const vp=document.getElementById('viewport'), svg=document.querySelector('#canvas>svg');
const W=GRAPH.w, H=GRAPH.h;
const byId=id=>svg.querySelector('g[data-cell-id="'+CSS.escape(id)+'"]');
const CELLS=[], EDGES=[], NODES=[], OWNER=GRAPH.own, CONN=new Set(GRAPH.conn);
const text=g=>(g.textContent||'').replace(/\\s+/g,' ').trim();
GRAPH.cells.forEach(id=>{
  const g=byId(id); if(!g) return;
  g.classList.add('cell'); CELLS.push(g);
  if(CONN.has(id)){
    g.classList.add('node'); g.setAttribute('role','button'); g.setAttribute('tabindex','0');
    g.setAttribute('aria-pressed','false'); NODES.push(g);
  }
});
// 접근성 이름 — 자기 텍스트가 없는 컨테이너는 안에 담긴 장식 셀의 텍스트로
NODES.forEach(n=>{
  const id=n.dataset.cellId;
  const owned=GRAPH.cells.filter(c=>OWNER[c]===id).map(c=>byId(c)).filter(Boolean).map(text).filter(Boolean);
  n.setAttribute('aria-label',text(n)||owned.join(' · ')||'다이어그램 노드');
});
GRAPH.edges.forEach(([id,a,b])=>{
  const g=byId(id); if(!g) return;
  g.classList.add('cell','edge'); g.dataset.a=a; g.dataset.b=b; CELLS.push(g); EDGES.push(g);
});
let SEL=null;
function mark(id, cls, selCls){
  const on={};
  if(id){
    on[id]=true;
    EDGES.forEach(e=>{const hit=e.dataset.a===id||e.dataset.b===id; e.classList.toggle(cls,hit);
      if(hit){on[e.dataset.a]=true; on[e.dataset.b]=true; on[e.dataset.cellId]=true; e.parentNode.appendChild(e);}});
  } else EDGES.forEach(e=>e.classList.remove(cls));
  const childCls=cls==='on'?'on-c':'pk-c';
  const kept=cid=>{let o=OWNER[cid],hop=0; while(o&&hop++<20){if(on[o]) return true; o=OWNER[o];} return false;};
  const vis=cid=>!!on[cid]||kept(cid);
  // 양끝이 모두 살아 있는 간선은 원래 색 그대로 남긴다 — 그 간선에 달린 라벨 셀도 따라가도록 먼저 정한다
  EDGES.forEach(e=>{if(id&&!on[e.dataset.cellId]&&vis(e.dataset.a)&&vis(e.dataset.b)) on[e.dataset.cellId]='kept';});
  CELLS.forEach(c=>{
    const cid=c.dataset.cellId;
    const isEdge=c.classList.contains('edge');
    if(!isEdge) c.classList.toggle(cls,on[cid]===true);
    c.classList.toggle(childCls,!!(id&&on[cid]!==true&&(on[cid]==='kept'||kept(cid))));
    c.classList.toggle(selCls,cid===id);
    if(c.classList.contains('node')&&selCls==='sel') c.setAttribute('aria-pressed',String(cid===id));
  });
}
function setFocus(id){SEL=id; setPeek(null); document.body.classList.toggle('focus',!!id); mark(id,'on','sel');}
function setPeek(id){if(id&&SEL) return; document.body.classList.toggle('peek',!!id); mark(id,'pk-on','pk-sel');}
function bind(g,id){
  g.addEventListener('mouseenter',()=>setPeek(id));
  g.addEventListener('mouseleave',()=>setPeek(null));
  g.addEventListener('click',ev=>{ev.stopPropagation(); setFocus(SEL===id?null:id);});
  g.addEventListener('keydown',ev=>{
    if(ev.key==='Enter'||ev.key===' '){ev.preventDefault(); setFocus(SEL===id?null:id);}
    else if(ev.key==='Escape') setFocus(null);});
}
NODES.forEach(n=>bind(n,n.dataset.cellId));
// 컨테이너 안의 장식 셀(DOM 상 형제)은 클릭·호버를 소유자에게 넘긴다 — 안 그러면 빈 곳 클릭으로 취급돼 선택이 풀린다
CELLS.forEach(c=>{
  const own=OWNER[c.dataset.cellId];
  if(own&&CONN.has(own)&&!c.classList.contains('node')){c.classList.add('node-part'); bind(c,own);}
});
document.body.addEventListener('click',()=>setFocus(null));
// 줌은 CSS transform 대신 SVG 크기로 — foreignObject(HTML 라벨)가 변환 아래서 어긋나는 브라우저가 있다.
let scale=1;
function apply(){svg.setAttribute('width',W*scale); svg.setAttribute('height',H*scale);}
function fit(){scale=Math.min((vp.clientWidth-32)/W,(vp.clientHeight-32)/H,1); apply();}
document.getElementById('zin').onclick=e=>{e.stopPropagation(); scale=Math.min(scale*1.25,3); apply();};
document.getElementById('zout').onclick=e=>{e.stopPropagation(); scale=Math.max(scale/1.25,.05); apply();};
document.getElementById('zfit').onclick=e=>{e.stopPropagation(); fit();};
vp.addEventListener('wheel',ev=>{
  if(!(ev.ctrlKey||ev.metaKey)) return;
  ev.preventDefault();
  // SVG 는 가운데 정렬·여백 때문에 뷰포트 원점에서 시작하지 않는다 — SVG 자체 좌표로 재어 포인터 아래 지점을 고정한다
  const r=svg.getBoundingClientRect(), wx=(ev.clientX-r.left)/scale, wy=(ev.clientY-r.top)/scale;
  const unit=ev.deltaMode===1?16:ev.deltaMode===2?vp.clientHeight:1;
  scale=Math.min(3,Math.max(.05,scale*Math.exp(-ev.deltaY*unit*.0015)));
  apply();
  const r2=svg.getBoundingClientRect();
  vp.scrollLeft+=r2.left+wx*scale-ev.clientX; vp.scrollTop+=r2.top+wy*scale-ev.clientY;
},{passive:false});
// 드래그 팬 — 끌면 팬, 안 끌면 클릭. 드래그 직후 click 1회 무시.
(function(){
  let panning=false, moved=false, sx=0, sy=0, sl=0, st=0;
  vp.addEventListener('mousedown',ev=>{
    if(ev.button!==0||(ev.target.closest&&ev.target.closest('#hud'))) return;
    panning=true; moved=false; sx=ev.clientX; sy=ev.clientY; sl=vp.scrollLeft; st=vp.scrollTop;
    vp.style.cursor='grabbing'; ev.preventDefault();
  });
  window.addEventListener('mousemove',ev=>{
    if(!panning) return;
    const dx=ev.clientX-sx, dy=ev.clientY-sy;
    if(Math.abs(dx)+Math.abs(dy)>4) moved=true;
    vp.scrollLeft=sl-dx; vp.scrollTop=st-dy;
  });
  window.addEventListener('mouseup',()=>{if(panning){panning=false; vp.style.cursor='grab';}});
  document.body.addEventListener('click',ev=>{if(moved){moved=false; ev.stopImmediatePropagation();}},{capture:true});
})();
window.addEventListener('resize',fit); fit();
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("drawio")
    ap.add_argument("out")
    ap.add_argument("--title", required=True)
    ap.add_argument("--hide", default="", help="뷰어에서 뺄 셀 id (쉼표 구분) — 원본은 그대로")
    a = ap.parse_args()
    tree = load(a.drawio, {h for h in a.hide.split(",") if h})
    graph = read_graph(tree)
    svg, w, h = slim_svg(export_svg(tree))
    missing = [i for i in graph["cells"] + [e[0] for e in graph["edges"]] if f'data-cell-id="{i}"' not in svg]
    if missing:
        raise SystemExit(f"SVG 에 없는 셀 id {len(missing)}개 — 뷰어가 그 노드를 잃는다: {missing[:10]}")
    graph.update(w=w, h=h)
    html = (TEMPLATE.replace("__TITLE__", a.title)
            .replace("__GRAPH__", json.dumps(graph, ensure_ascii=False, separators=(",", ":")))
            .replace("__SVG__", svg))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{a.out}: {len(html)//1024}KB, nodes {len(graph['conn'])}, edges {len(graph['edges'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
