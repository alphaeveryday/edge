/* Edit the same YAML consumed by the model; no model calls from this workspace. */
(()=>{
const el=id=>document.getElementById(id), make=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
const drafts=new Map();let kind='outlook', current=null, timer, revision=0, comparison=0;
async function api(path,body){const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':(config||await(await request('/api/execution')).json()).csrf_token},body:JSON.stringify(body)};return (await request(path,options)).json()}
function message(text,error=false){el('prompt-message').textContent=text;el('prompt-message').className=error?'error':'muted'}
function yamlColor(text,target){target.replaceChildren();for(const line of text.split('\n')){const key=line.match(/^([\w_]+)(:\s*[|>+-]*)/);if(key){const a=make('span',key[1]);a.className='key';const b=make('span',key[2]);b.className='number';target.append(a,b,document.createTextNode(line.slice(key[0].length)))}else{const n=make('span',line);n.className=line.startsWith('#')?'muted':'string';target.append(n)}target.append(document.createTextNode('\n'))}}
function documentView(text){const box=el('prompt-document');box.replaceChildren();for(const line of text.split('\n')){if(!line.trim()){box.append(make('br',''));continue}const trimmed=line.trim();box.append(make(/^#{1,6} /.test(trimmed)||trimmed.endsWith(':')?'h3':'p',trimmed.replace(/^#{1,6} /,'')))}}
async function preview(){const mine=++revision,text=el('prompt-yaml').value;yamlColor(text,el('prompt-highlight'));message('YAML 확인 중…');try{const value=await api('/api/prompts/preview',{yaml:text});if(mine!==revision)return;documentView(value.system_prompt);el('prompt-save').disabled=false;message(text===current?.yaml?'저장된 활성 버전입니다.':'저장하지 않은 변경 · 저장하면 이후 실행에 적용됩니다.')}catch(error){if(mine!==revision)return;el('prompt-document').replaceChildren(make('p','YAML 오류로 미리보기를 만들 수 없습니다.'));el('prompt-save').disabled=true;message(error.message,true)}}
async function compare(){
 const record=current?.history[Number(el('prompt-history').value)],mine=++comparison;
 el('prompt-compare-old').replaceChildren();el('prompt-compare-current').replaceChildren();
 if(!record)return;el('prompt-diff-status').textContent='저장된 버전의 차이를 확인하고 있습니다.';
 try{const result=await api('/api/prompts/'+kind+'/compare/'+record.version);if(mine!==comparison)return;
 el('prompt-diff-status').textContent=result.added||result.removed?`추가 ${result.added}줄 · 삭제 ${result.removed}줄 · 현재 적용 버전 ${result.current_version.slice(0,12)}`:'두 버전의 내용이 같습니다.';
 for(const row of result.rows)for(const [side,target] of [['old','prompt-compare-old'],['new','prompt-compare-current']]){const value=row[side],changed=row.kind!=='equal'&&value;const prefix=changed?(side==='old'?'−':'+'):' ';const line=make('span',value?prefix+' '+String(value.number).padStart(4)+'  '+value.text.replace(/\r?\n$/,''):' ');line.className='prompt-diff-line'+(changed?(side==='old'?' diff-removed':' diff-added'):'');el(target).append(line)}
 }catch(error){if(mine===comparison)el('prompt-diff-status').textContent=error.message}
}
function populate(){el('prompt-version').textContent=`활성 버전 ${current.version.slice(0,12)} · ${current.source}`;el('prompt-history').replaceChildren();current.history.forEach((r,i)=>{const option=make('option',`${new Date(r.saved_at).toLocaleString('ko-KR')} · ${r.version.slice(0,12)} · ${r.note}`);option.value=String(i);el('prompt-history').append(option)});el('prompt-history').value=current.history.length>1?'1':'0';compare()}
async function load(){const selected=kind;message('프롬프트 불러오는 중…');try{const value=await api('/api/prompts/'+selected);if(selected!==kind)return;const draft=drafts.get(kind);current=draft?.current||value;populate();el('prompt-yaml').value=draft?.yaml??current.yaml;el('prompt-note').value=draft?.note||'';
 if(config?.prompts_read_only){
  el('prompt-yaml').readOnly=true;el('prompt-save').parentElement.hidden=true;
  el('prompt-workspace').querySelector('p.muted').textContent='연결된 시스템 프롬프트를 조회하고 과거 버전과 비교합니다. 기존 분석 결과는 변경되지 않습니다.';
  el('prompt-yaml').closest('section').querySelector('h3').textContent='YAML 보기';
  yamlColor(current.yaml,el('prompt-highlight'));documentView(current.system_prompt);
  message('읽기 전용 · 배포된 프롬프트와 과거 버전을 비교합니다.');
 }else await preview()
 }catch(error){message(error.message,true)}}
function preserve(){if(current)drafts.set(kind,{current,yaml:el('prompt-yaml').value,note:el('prompt-note').value})}
el('workspace-prompts').onclick=()=>{el('analysis-workspace').hidden=true;el('prompt-workspace').hidden=false;el('workspace-prompts').classList.add('active');el('workspace-analysis').classList.remove('active');if(!current)load()};
el('workspace-analysis').onclick=()=>{el('analysis-workspace').hidden=false;el('prompt-workspace').hidden=true;el('workspace-analysis').classList.add('active');el('workspace-prompts').classList.remove('active')};
el('prompt-kind').onchange=()=>{preserve();kind=el('prompt-kind').value;current=null;revision++;comparison++;load()};
el('prompt-yaml').oninput=()=>{clearTimeout(timer);revision++;el('prompt-save').disabled=true;yamlColor(el('prompt-yaml').value,el('prompt-highlight'));timer=setTimeout(preview,250)};
el('prompt-yaml').onscroll=()=>{el('prompt-highlight').scrollTop=el('prompt-yaml').scrollTop;el('prompt-highlight').scrollLeft=el('prompt-yaml').scrollLeft};
el('prompt-yaml').onkeydown=e=>{if(e.key==='Tab'&&!el('prompt-yaml').readOnly){e.preventDefault();el('prompt-yaml').setRangeText('  ',e.target.selectionStart,e.target.selectionEnd,'end');el('prompt-yaml').oninput()}};
el('prompt-history').onchange=compare;
el('prompt-save').onclick=async()=>{if(!current)return;const selected=kind,text=el('prompt-yaml').value;el('prompt-save').disabled=true;try{const saved=await api('/api/prompts/'+selected,{yaml:text,expected_version:current.version,note:el('prompt-note').value});const draft=drafts.get(selected);if(draft&&draft.yaml!==text)drafts.set(selected,{...draft,current:saved});else drafts.delete(selected);if(selected!==kind)return;current=saved;populate();el('prompt-note').value='';await preview();message(el('prompt-yaml').value===text?'저장 완료 · 다음 실행부터 이 버전을 사용합니다.':'저장은 완료됐으며 이후 편집한 내용은 아직 저장하지 않았습니다.')}catch(error){message(error.message,true);el('prompt-save').disabled=false}};
el('prompt-latest').onclick=async()=>{preserve();const selected=kind;try{const latest=await api('/api/prompts/'+selected);if(selected!==kind)return;current=latest;populate();message('최신 기준 버전을 불러왔습니다. 편집 중인 YAML은 유지했습니다. 비교 후 저장하세요.')}catch(error){message(error.message,true)}};
window.addEventListener('beforeunload',e=>{preserve();if([...drafts.values()].some(d=>d.yaml!==d.current.yaml)){e.preventDefault();e.returnValue=''}});
})();
