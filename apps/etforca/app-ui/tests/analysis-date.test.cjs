const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const { QueryClient, QueryObserver } = require('@tanstack/react-query');

// 실제 화면의 클릭 핸들러·조회 옵션·HTTP를 실행한다. 네이티브 렌더러와 네트워크만 대역이다.
function fixture(t) {
  const root = path.resolve(__dirname, '..');
  const modules = new Map();
  const routes = [];
  const calls = [];
  let params = { code: '950001', axis: '이슈', date: '2026-09-24' };
  let stateIndex = 0;
  let states = [];
  let response = async (url) => ({ status: 200, json: async () => ({ isSuccess: true, result: { axis: 'issue', headline: new URL(url).searchParams.get('date') ?? 'latest' } }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  t.after(() => client.clear());
  const node = (type, props) => ({ type, props });
  const colors = new Proxy({}, { get: () => '#000' });
  const signal = new Proxy({}, { get: () => ({ color: '#000', label: '상승' }) });
  function load(relative) {
    let file = path.resolve(root, relative);
    if (!path.extname(file)) file = ['.ts', '.tsx', '/index.ts'].map((ext) => file + ext).find(fs.existsSync);
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} };
    modules.set(file, module);
    const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      fileName: file, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(source, {
      module, exports: module.exports, console, setTimeout, clearTimeout, process: { env: {} },
      fetch: (url, init) => { calls.push({ url, init }); return response(url, init); },
      require: (name) => {
        if (name === 'react/jsx-runtime') return { jsx: node, jsxs: node, Fragment: 'Fragment' };
        if (name === 'react') return {
          useState: (initial) => [states[stateIndex++] ?? initial, () => {}], useRef: (value) => ({ current: value }),
          useEffect: () => {}, useCallback: (fn) => fn,
        };
        if (name === 'react-native') return { View: 'View', Text: 'Text', Pressable: 'Pressable' };
        if (name === 'expo-router') return { useLocalSearchParams: () => params, useRouter: () => ({ push: (route) => routes.push(route) }), useFocusEffect: () => {} };
        if (name === 'react-native-safe-area-context') return { useSafeAreaInsets: () => ({ top: 0 }) };
        if (name === '@tanstack/react-query') return { useQuery: (options) => options };
        if (name === '@/api') return { api: load('src/api/http/index.ts').httpClient };
        if (name === './storage') return { tokens: { version: () => 0, assertCurrent: () => {} } };
        if (name === '@/theme/typography') return { fam: {} };
        if (name === '@/theme/theme') return { createStyles: () => () => ({}), useColors: () => colors, useSignal: () => signal };
        if (name === '@/components/ui') return new Proxy({}, { get: (_, key) => key });
        if (name === '@/components/state') return { QueryState: 'QueryState', Loading: 'Loading' };
        if (name === '@/features/etf/queries') return { useEtf: () => ({}) };
        if (name === '@/features/community/queries') return { useVoteStat: () => ({}) };
        if (name === '@/lib/analytics') return { track: () => {} };
        if (name === '@/lib/usePullRefresh') return { usePullRefresh: () => ({ scroll: {} }) };
        if (name === '@/lib/useScrollFocus') return { useScrollFocus: () => ({}) };
        if (/AxisNav|VoteCard|Disclaimer|FactorRow|HintSheet/.test(name)) return new Proxy({}, { get: (_, key) => key });
        if (name.startsWith('@/')) return load('src/' + name.slice(2));
        if (name.startsWith('.')) return load(path.relative(root, path.resolve(path.dirname(file), name)));
        return require(name);
      },
    }, { filename: file });
    return module.exports;
  }
  return { load, routes, calls, client, params: (value) => { params = value; }, respond: (fn) => { response = fn; }, render: (fn, props, values = []) => { stateIndex = 0; states = values; return fn(props); } };
}
function descendants(tree) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(descendants);
  return [tree, ...descendants(tree.props?.children)];
}
function routeParams(route) {
  if (typeof route !== 'string') return route.params;
  return Object.fromEntries(new URL(route, 'https://app.test').searchParams);
}
const daily = {
  date: '2026-09-24', now: 'up', dates: [], axes: [{ axis: '이슈', dir: 'help', hasPage: true }],
  today: [], args: [], neg: [], pos: [],
};

test('brief links use the displayed publication, including a weekly or placeholder result', (t) => {
  const f = fixture(t);
  const page = f.load('app/etf/[code]/brief.tsx').default;
  // A pending week selection differs from the publication still displayed on screen.
  const tree = f.render(page, {}, [{ week: '2026-09-14' }]);
  const content = tree.props.children(daily);
  const axis = descendants(content).find((n) => n.type === 'Pressable' && n.props.children?.some?.((c) => c?.props?.children === '이슈'));
  assert.ok(axis);
  axis.props.onPress();
  assert.equal(routeParams(f.routes[0]).date, daily.date);
});

test('analysis sheet detail link preserves its publication date', (t) => {
  const f = fixture(t);
  const { DailySheet } = f.load('src/features/analysis/DailySheet.tsx');
  const tree = f.render(DailySheet, { code: '950001', daily, open: true, entry: 'etf_page', onClose: () => {} }, [true]);
  const row = descendants(tree).find((n) => n.type === 'FactorRow');
  assert.ok(row);
  row.props.onSelect();
  assert.equal(routeParams(f.routes[0]).date, daily.date);
});

for (const [kind, axis] of [['factor', '이슈'], ['metric', '밸류']]) {
  test(`${kind} screen sends its date through query options and HTTP`, async (t) => {
    const f = fixture(t);
    f.params({ code: '950001', axis, date: daily.date });
    const tree = f.render(f.load(`app/${kind}/[code]/[axis].tsx`).default);
    const options = descendants(tree).find((n) => n.type === 'QueryState').props.query;
    await f.client.fetchQuery(options);
    assert.equal(new URL(f.calls[0].url).searchParams.get('date'), daily.date);
    assert.ok(options.queryKey.includes(daily.date));
  });
}

test('metric to factor navigation retains the same publication', (t) => {
  const f = fixture(t);
  f.params({ code: '950001', axis: '밸류', date: daily.date });
  const tree = f.render(f.load('app/metric/[code]/[axis].tsx').default);
  const content = descendants(tree).find((n) => n.type === 'QueryState').props.children({ tiles: [], hasDetail: true });
  descendants(content).find((n) => n.type === 'Pressable').props.onPress();
  assert.equal(routeParams(f.routes[0]).date, daily.date);
});

for (const hook of ['useFactor', 'useMetric']) {
  test(`${hook}: late historical response cannot replace the newly selected date`, async (t) => {
    const f = fixture(t);
    let release;
    f.respond((url) => {
      const date = new URL(url).searchParams.get('date');
      const res = { status: 200, json: async () => ({ isSuccess: true, result: { axis: 'issue', headline: date } }) };
      return date === daily.date ? new Promise((resolve) => { release = () => resolve(res); }) : Promise.resolve(res);
    });
    const query = f.load('src/features/analysis/queries.ts')[hook];
    const old = query('950001', '이슈', daily.date);
    const current = query('950001', '이슈', '2026-09-25');
    const observer = new QueryObserver(f.client, old);
    const unsubscribe = observer.subscribe(() => {});
    t.after(unsubscribe);
    const pending = f.client.fetchQuery(old);
    observer.setOptions(current);
    await f.client.fetchQuery(current);
    assert.ok(release, 'the historical request must include its date');
    release();
    await pending;
    assert.equal(observer.getCurrentResult().data.headline, '2026-09-25');
    assert.equal(f.client.getQueryData(old.queryKey).headline, daily.date);
    assert.equal(f.client.getQueryData(current.queryKey).headline, '2026-09-25');
  });
}

test('undated detail requests retain the existing latest endpoint', async (t) => {
  const f = fixture(t);
  await f.load('src/api/http/index.ts').httpClient.analysis.factor('950001', '이슈');
  assert.equal(new URL(f.calls[0].url).search, '');
});
