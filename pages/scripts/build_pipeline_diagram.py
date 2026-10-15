#!/usr/bin/env python3
"""drawio → 인터랙티브 다이어그램 HTML (SVG 내장형).

1~4번 다이어그램은 drawio 셀을 HTML 상자로 재구성했지만, 파이프라인 그림처럼
AWS 아이콘·서식 라벨이 많은 원본은 그 방식으로는 깨진다. 여기서는 draw.io CLI 가
export 한 SVG 를 그대로 품고(셀 id 가 `data-cell-id` 로 남는다), drawio XML 의
간선(source→target)만 읽어 클릭·호버 강조를 얹는다.

    python pages/scripts/build_pipeline_diagram.py <원본.drawio> <출력.html> --title "…"

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


def export_svg(drawio_path: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "d.svg")
        subprocess.run(
            [DRAWIO_BIN, "-x", "-f", "svg", "--theme", "light", "--embed-svg-images", "-o", out, drawio_path],
            check=True, capture_output=True,
        )
        with open(out, encoding="utf-8") as f:
            return f.read()


def read_graph(drawio_path: str) -> dict:
    root = ET.parse(drawio_path).getroot()
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
        if c.get("parent") not in layers:
            continue
        if c.get("edge") == "1" and c.get("source") and c.get("target"):
            edges.append([c.get("id"), c.get("source"), c.get("target")])
        elif c.get("vertex") == "1":
            g = c.find("mxGeometry")
            boxes[c.get("id")] = tuple(float(g.get(k, 0)) for k in ("x", "y", "width", "height"))
    conn = {a for _, a, _ in edges} | {b for _, _, b in edges}
    for cid, (x, y, w, h) in boxes.items():
        if cid in conn:
            continue
        best = None
        for oid in conn:
            if oid not in boxes:
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
#viewport{width:100%;height:100%;overflow:auto;cursor:grab}
#canvas>svg{display:block}
.cell{transition:opacity .25s,filter .25s}
.node{cursor:pointer}
.node:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.edge{pointer-events:none}
.edge path{transition:stroke .25s,stroke-width .25s}
body.focus .cell{opacity:.14}
body.focus .cell.on,body.focus .cell.on-c{opacity:1}
body.focus .cell.on:not(.edge){filter:drop-shadow(0 0 1.5px var(--brand))}
body.focus .cell.sel{filter:drop-shadow(0 0 2px var(--brand)) drop-shadow(0 2px 8px rgba(0,48,159,.4))}
body.focus .edge.on path{stroke:var(--brand)!important}
body.focus .edge.on path[fill="none"]{stroke-width:3.2}
body.focus .edge.on path:not([fill="none"]){fill:var(--brand)!important}
body.peek .cell{opacity:.45}
body.peek .cell.pk-on,body.peek .cell.pk-c{opacity:1}
body.peek .cell.pk-on:not(.edge){filter:drop-shadow(0 0 1.5px var(--brand))}
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
GRAPH.cells.forEach(id=>{
  const g=byId(id); if(!g) return;
  g.classList.add('cell'); CELLS.push(g);
  if(CONN.has(id)){
    g.classList.add('node'); g.setAttribute('role','button'); g.setAttribute('tabindex','0');
    g.setAttribute('aria-pressed','false');
    g.setAttribute('aria-label',(g.textContent||'').replace(/\\s+/g,' ').trim()||'다이어그램 노드');
    NODES.push(g);
  }
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
  CELLS.forEach(c=>{
    const cid=c.dataset.cellId;
    if(!c.classList.contains('edge')) c.classList.toggle(cls,!!on[cid]);
    c.classList.toggle(childCls,!!(id&&!on[cid]&&OWNER[cid]&&on[OWNER[cid]]));
    c.classList.toggle(selCls,cid===id);
    if(c.classList.contains('node')&&selCls==='sel') c.setAttribute('aria-pressed',String(cid===id));
  });
}
function setFocus(id){SEL=id; setPeek(null); document.body.classList.toggle('focus',!!id); mark(id,'on','sel');}
function setPeek(id){if(id&&SEL) return; document.body.classList.toggle('peek',!!id); mark(id,'pk-on','pk-sel');}
NODES.forEach(n=>{
  const id=n.dataset.cellId;
  n.addEventListener('mouseenter',()=>setPeek(id));
  n.addEventListener('mouseleave',()=>setPeek(null));
  n.addEventListener('click',ev=>{ev.stopPropagation(); setFocus(SEL===id?null:id);});
  n.addEventListener('keydown',ev=>{
    if(ev.key==='Enter'||ev.key===' '){ev.preventDefault(); setFocus(SEL===id?null:id);}
    else if(ev.key==='Escape') setFocus(null);});
});
document.body.addEventListener('click',()=>setFocus(null));
// 줌은 CSS transform 대신 SVG 크기로 — foreignObject(HTML 라벨)가 변환 아래서 어긋나는 브라우저가 있다.
let scale=1;
function apply(){svg.setAttribute('width',W*scale); svg.setAttribute('height',H*scale);}
function fit(){scale=Math.min((vp.clientWidth-8)/W,(vp.clientHeight-8)/H,1); apply();}
document.getElementById('zin').onclick=e=>{e.stopPropagation(); scale=Math.min(scale*1.25,3); apply();};
document.getElementById('zout').onclick=e=>{e.stopPropagation(); scale=Math.max(scale/1.25,.05); apply();};
document.getElementById('zfit').onclick=e=>{e.stopPropagation(); fit();};
vp.addEventListener('wheel',ev=>{
  if(!(ev.ctrlKey||ev.metaKey)) return;
  ev.preventDefault();
  const rect=vp.getBoundingClientRect(), px=ev.clientX-rect.left, py=ev.clientY-rect.top;
  const wx=(vp.scrollLeft+px)/scale, wy=(vp.scrollTop+py)/scale;
  const unit=ev.deltaMode===1?16:ev.deltaMode===2?vp.clientHeight:1;
  scale=Math.min(3,Math.max(.05,scale*Math.exp(-ev.deltaY*unit*.0015)));
  apply(); vp.scrollLeft=wx*scale-px; vp.scrollTop=wy*scale-py;
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
    a = ap.parse_args()
    graph = read_graph(a.drawio)
    svg, w, h = slim_svg(export_svg(a.drawio))
    graph.update(w=w, h=h)
    html = (TEMPLATE.replace("__TITLE__", a.title)
            .replace("__GRAPH__", json.dumps(graph, ensure_ascii=False, separators=(",", ":")))
            .replace("__SVG__", svg))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{a.out}: {len(html)//1024}KB, nodes {len(graph['conn'])}, edges {len(graph['edges'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
