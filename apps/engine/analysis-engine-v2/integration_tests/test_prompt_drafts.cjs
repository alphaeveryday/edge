const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('edits made after save starts survive switching kinds and use the saved version', async () => {
  const nodes = new Map();
  const element = () => ({value:'',classList:{add(){},remove(){}},append(){},replaceChildren(){}});
  const get = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
  const record = (yaml,version) => ({yaml,version,source:'test',history:[]});
  let finishSave;
  const submissions = [];
  const context = vm.createContext({
    document:{getElementById:get,createElement:element,createTextNode:()=>({})},
    window:{addEventListener(){}},config:{csrf_token:'test'},setTimeout,clearTimeout,
    async request(url,options={}) {
      if(url.endsWith('/preview'))return {json:async()=>({system_prompt:'preview'})};
      if(options.method==='POST') {
        submissions.push(JSON.parse(options.body));
        return new Promise(resolve=>{finishSave=()=>resolve({json:async()=>record('submitted','v2')})});
      }
      return {json:async()=>record('original','v1')};
    },
  });
  const settle = () => new Promise(resolve=>setImmediate(resolve));
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/edge_analysis_v2/dashboard/static/prompts.js'),'utf8'),context);
  get('workspace-prompts').onclick();await settle();
  get('prompt-yaml').value='submitted';
  const pending=get('prompt-save').onclick();
  get('prompt-yaml').value='newer unsaved edit';
  get('prompt-kind').value='movement';get('prompt-kind').onchange();await settle();
  finishSave();await pending;
  get('prompt-kind').value='outlook';get('prompt-kind').onchange();await settle();
  assert.equal(get('prompt-yaml').value,'newer unsaved edit');
  const next=get('prompt-save').onclick();
  assert.equal(submissions[1].expected_version,'v2','preserved edits must be based on the completed save');
  finishSave();await next;
});
