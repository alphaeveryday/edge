// Run with: node --test integration_tests/test_review_refresh.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('polling updates the list without replacing content being read; manual refresh reloads it', async () => {
  const nodes = new Map();
  const element = () => ({children:[], textContent:'', innerHTML:'', dataset:{},
    classList:{toggle(){}}, append(...items){this.children.push(...items)},
    replaceChildren(...items){this.children=items}, querySelectorAll(){return []}});
  const get = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
  const row = {kind:'movement',analysis_id:'saved',etf_code:'091160',status:'completed'};
  const calls = [];
  let poll;
  let failReads = false;
  const context = vm.createContext({
    document:{hidden:false,getElementById:get,createElement:element},
    window:{addEventListener(){}},history:{replaceState(){}},location:{hash:''},
    setInterval(callback){poll=callback},
    async fetch(url){calls.push(url);if(failReads&&url==='/api/analyses')throw Error('DB disconnected');return {ok:true,
      async json(){return url==='/api/jobs'?[]:[row]},async text(){return 'saved content'}}},
  });
  const html = fs.readFileSync(path.join(__dirname,'../src/edge_analysis_v2/cloud_review.html'),'utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  await vm.runInContext(`mode='execution'; config={enabled:true}; openAnalysis(${JSON.stringify(row)})`,context);
  const original = get('detail').innerHTML;
  for(let i=0;i<3;i++){poll();await new Promise(resolve=>setImmediate(resolve))}
  assert.equal(calls.filter(url=>url.startsWith('/view/')).length,1);
  assert.equal(get('detail').innerHTML,original);
  assert.equal(calls.filter(url=>url==='/api/analyses').length,3);
  assert.equal(calls.filter(url=>url==='/api/jobs').length,3);
  const textBeforeError = get('detail').textContent;
  failReads=true;poll();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('detail').textContent,textBeforeError);
  assert.equal(get('detail').innerHTML,original);
  failReads=false;
  await get('refresh').onclick();
  assert.equal(calls.filter(url=>url.startsWith('/view/')).length,2);
});
