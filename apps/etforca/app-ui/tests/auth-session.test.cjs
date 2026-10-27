const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');
const query = require('@tanstack/react-query');

// 실제 TS 모듈·Zustand·TanStack Query를 실행하고 기기 저장소·라우터·HTTP만 대역으로 교체한다.
const app = path.resolve(__dirname, '..');
const success = (result) => ({ status: 200, json: async () => ({ isSuccess: true, result }) });
const failure = (code = 'COMMON401', status = 401) => ({ status, json: async () => ({ isSuccess: false, code, message: code }) });
const credentials = (name) => ({ accessToken: name, refreshToken: `refresh-${name}`, me: { nick: name, handle: name } });
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}
const tick = () => new Promise((r) => setImmediate(r));

function fixture(t, { native = false, beforeWrite = async () => {} } = {}) {
  const memory = new Map();
  const modules = new Map();
  const routes = [];
  const calls = [];
  let placeholder; let placeholderRevision;
  let respond = () => { throw new Error('unexpected HTTP request'); };
  const router = { push: (route) => routes.push(route) };
  function load(relative) {
    let filename = path.resolve(app, relative);
    if (!path.extname(filename)) filename = fs.existsSync(filename + '.ts') ? filename + '.ts' : path.join(filename, 'index.ts');
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} };
    modules.set(filename, module);
    const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }, fileName: filename,
    }).outputText;
    vm.runInNewContext(source, {
      module, exports: module.exports, console, setTimeout, clearTimeout,
      process: { env: { EXPO_PUBLIC_API_MODE: 'http' } },
      localStorage: { getItem: (key) => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value), removeItem: (key) => memory.delete(key) },
      fetch: (url, init) => { calls.push({ url, ...init }); return respond(url, init); },
      require: (name) => {
        if (filename.endsWith('/community/queries.ts') && name === '@/store/session') return {
          useSession: Object.assign((select) => select(useSession.getState()), { getState: useSession.getState }),
          useRequireLogin: () => (_reason, fn) => fn(),
        };
        if (filename.endsWith('/useSessionQuery.ts') && name === '@/store/session') return { useSession: (select) => select(useSession.getState()) };
        if (filename.endsWith('/useSessionQuery.ts') && name === 'react') return { useCallback: (fn, [revision]) => {
          if (revision !== placeholderRevision) { placeholderRevision = revision; placeholder = fn; }
          return placeholder;
        } };
        if (filename.endsWith('/home/queries.ts') && name === 'react') return { useEffect: (effect) => effect() };
        if (filename.endsWith('/queries.ts') && name === '@tanstack/react-query') return {
          ...query, useQuery: (options) => options, useMutation: (options) => options, useQueryClient: () => queryClient,
        };
        if (name === 'react-native') return { Platform: { OS: native ? 'ios' : 'web' } };
        if (name === 'expo-secure-store') return {
          getItemAsync: async (key) => memory.get(key) ?? null,
          setItemAsync: async (key, value) => { await beforeWrite(key, value); memory.set(key, value); },
          deleteItemAsync: async (key) => { await beforeWrite(key, null); memory.delete(key); },
        };
        if (name === 'expo-constants') return { expoConfig: {} };
        if (name === 'expo-device') return { isDevice: true };
        if (name === 'expo-notifications') return {
          setNotificationHandler: () => {},
          getPermissionsAsync: async () => ({ granted: true }),
          getExpoPushTokenAsync: async () => ({ data: 'device-push-token' }),
        };
        if (name === 'expo-router') return { router, useRouter: () => router };
        if (name === '@/lib/analytics') return { track: () => {} };
        if (name === './mock' && filename.endsWith('/api/index.ts')) return { mockClient: {} };
        if (name.startsWith('@/')) return load('src/' + name.slice(2));
        if (name.startsWith('.')) return load(path.relative(app, path.resolve(path.dirname(filename), name)));
        return require(name);
      },
    }, { filename });
    return module.exports;
  }
  const { useSession } = load('src/store/session.ts');
  const { tokens } = load('src/api/http/storage.ts');
  const { queryClient } = load('src/lib/queryClient.ts');
  const { request } = load('src/api/http/fetch.ts');
  const { api } = load('src/api/index.ts');
  const { useWatchGroup } = load('src/store/watch.ts');
  t.after(() => queryClient.clear());
  useSession.setState({ restored: true, onboarded: true });
  return {
    tokens, queryClient, request, api, useSession, useWatchGroup, routes, calls, memory,
    registerPush: () => load('src/features/notification/push.ts').registerPush(false),
    prefetchHome: (groups) => load('src/features/home/queries.ts').usePrefetchBriefs(groups),
    updateMeOptions: () => load('src/features/community/queries.ts').useUpdateMe(),
    voteOptions: () => load('src/features/community/queries.ts').useVote('123'),
    likeOptions: () => load('src/features/community/queries.ts').useToggleLike(),
    createGroupOptions: () => load('src/features/watch/queries.ts').useCreateGroup(),
    homeOptions: (group) => load('src/features/home/queries.ts').useHomeBrief(group),
    watchOptions: (group) => load('src/features/watch/queries.ts').useWatchList(group, true),
    respond: (fn) => { respond = fn; },
    async member(name = 'member-a') { await tokens.replace(name, `refresh-${name}`, 'login'); },
    seed() {
      for (const key of [['watch', 'groups'], ['home', 'brief', 'old'], ['member', 'me'], ['community', 'voteStat', '123'], ['noti', 'unread']]) queryClient.setQueryData(key, { owner: 'old' });
      useWatchGroup.getState().setGroup('old-group');
    },
    assertCleared() {
      for (const entry of queryClient.getQueryCache().getAll()) assert.equal(entry.state.data, undefined, JSON.stringify(entry.queryKey));
      assert.equal(useWatchGroup.getState().group, 'base');
    },
  };
}

for (const method of ['GET', 'PUT']) test(`${method}: refresh rejection removes membership and old data before another write`, async (t) => {
  const f = fixture(t);
  await f.member(); f.seed();
  f.respond((url) => url.endsWith('/auth/refresh') ? failure('COMMON400', 400) : failure());
  const run = () => f.request(method, '/watch-groups');
  if (method === 'GET') await f.queryClient.fetchQuery({ queryKey: ['watch', 'request'], queryFn: run, retry: false }).catch(() => {});
  else await f.queryClient.getMutationCache().build(f.queryClient, { mutationFn: run }).execute().catch(() => {});
  assert.equal(await f.tokens.access(), null);
  assert.equal(await f.tokens.refresh(), null);
  assert.equal(f.useSession.getState().loggedIn, false);
  assert.equal(f.useSession.getState().onboarded, true);
  assert.equal(f.routes.length, 1);
  f.assertCleared();
  f.respond(() => success(undefined));
  await f.request('PUT', '/watch-groups/base/etfs');
  assert.equal(f.calls.at(-1).headers.Authorization, undefined);
  assert.ok(f.calls.at(-1).headers['X-Device-Id']);
});

test('simultaneous read/write failures share refresh and navigate only once', async (t) => {
  const f = fixture(t); await f.member(); f.seed();
  const gate = deferred(); const started = deferred();
  f.respond((url) => { if (url.endsWith('/auth/refresh')) { started.resolve(); return gate.promise; } return failure(); });
  const pending = Promise.allSettled([f.request('GET', '/me'), f.request('PUT', '/watch-groups/base/etfs'), f.request('GET', '/home/brief')]);
  await started.promise; await tick(); gate.resolve(failure('COMMON400', 400)); await pending;
  assert.equal(f.calls.filter((r) => r.url.endsWith('/auth/refresh')).length, 1);
  assert.equal(f.routes.length, 1); assert.equal(f.useSession.getState().loggedIn, false); f.assertCleared();
});

for (const scenario of ['network', '500', '429', 'malformed', 'missing-result']) test(`refresh ${scenario} keeps membership and cached data`, async (t) => {
  const f = fixture(t); await f.member(); f.seed();
  f.respond((url) => {
    if (!url.endsWith('/auth/refresh')) return failure();
    if (scenario === 'network') throw new Error('offline');
    if (scenario === 'malformed') return { status: 200, json: async () => { throw new Error('invalid JSON'); } };
    if (scenario === 'missing-result') return success(undefined);
    return failure('COMMON400', Number(scenario));
  });
  await assert.rejects(f.request('GET', '/me'), (e) => e.code === 'NETWORK');
  assert.equal(await f.tokens.access(), 'member-a');
  assert.equal(f.useSession.getState().loggedIn, true);
  assert.equal(f.queryClient.getQueryData(['member', 'me']).owner, 'old');
  assert.equal(f.routes.length, 0);
});

test('successful refresh retries with the new token and keeps the current user cache', async (t) => {
  const f = fixture(t); await f.member(); f.seed();
  f.respond((url, init) => url.endsWith('/auth/refresh') ? success(credentials('renewed')) : init.headers.Authorization === 'Bearer renewed' ? success('ok') : failure());
  assert.equal(await f.request('GET', '/me'), 'ok');
  assert.equal(await f.tokens.access(), 'renewed'); assert.equal(f.routes.length, 0);
  assert.equal(f.useSession.getState().loggedIn, true);
  assert.equal(f.queryClient.getQueryData(['member', 'me']).owner, 'old');
});

test('final rejection after refresh expires the session', async (t) => {
  const f = fixture(t); await f.member();
  f.respond((url) => url.endsWith('/auth/refresh') ? success(credentials('renewed')) : failure());
  await assert.rejects(f.request('GET', '/me'));
  assert.equal(await f.tokens.access(), null); assert.equal(f.routes.length, 1);
});

for (const method of ['login', 'signup', 'social']) test(`${method}: guest data and selection are gone when login succeeds`, async (t) => {
  const f = fixture(t); f.seed();
  f.respond(() => success(credentials('member-b')));
  if (method === 'login') await f.api.auth.login('b@example.test', 'password');
  else if (method === 'signup') await f.api.auth.signup({ email: 'b@example.test', password: 'password', nick: 'b', code: '123456' });
  else await f.api.auth.social({ provider: 'apple', token: 'social-token' });
  f.useSession.getState().login();
  assert.equal(await f.tokens.access(), 'member-b');
  assert.equal(f.useSession.getState().loggedIn, true); f.assertCleared();
});

test('account A to B replaces an Infinity member query even when loggedIn stays true', async (t) => {
  const f = fixture(t); await f.member(); f.seed();
  f.respond((url) => url.endsWith('/auth/login') ? success(credentials('member-b')) : success(credentials('member-b').me));
  const observer = new query.QueryObserver(f.queryClient, { queryKey: ['member', 'me'], queryFn: () => f.api.member.me(), staleTime: Infinity });
  const seen = []; const unsubscribe = observer.subscribe((r) => seen.push(r.data?.nick)); t.after(unsubscribe);
  await f.api.auth.login('b@example.test', 'password'); await tick();
  assert.ok(seen.includes(undefined));
  assert.equal(observer.getCurrentResult().data.nick, 'member-b');
  assert.equal(f.useSession.getState().loggedIn, true);
});

test('late guest query cannot repopulate the member cache after login', async (t) => {
  const f = fixture(t); const old = deferred(); const started = deferred();
  f.respond((url) => { if (url.endsWith('/auth/login')) return success(credentials('member-b')); started.resolve(); return old.promise; });
  const pending = f.queryClient.fetchQuery({ queryKey: ['watch', 'groups'], retry: false, queryFn: () => f.api.watch.groups() }).catch(() => {});
  await started.promise; await f.api.auth.login('b@example.test', 'password');
  old.resolve(success([{ key: 'guest-group' }])); await pending; await tick();
  assert.equal(f.queryClient.getQueryData(['watch', 'groups']), undefined);
});

for (const late of ['success', 'rejection']) test(`late A refresh ${late} cannot overwrite or expire B`, async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred();
  f.respond((url) => {
    if (url.endsWith('/auth/login')) return success(credentials('member-b'));
    if (url.endsWith('/auth/refresh')) { started.resolve(); return old.promise; }
    return failure();
  });
  const pending = f.request('GET', '/me').catch((e) => e);
  await started.promise; await f.api.auth.login('b@example.test', 'password');
  old.resolve(late === 'success' ? success(credentials('old-a')) : failure('COMMON400', 400));
  assert.ok(query.isCancelledError(await pending));
  assert.equal(await f.tokens.access(), 'member-b'); assert.equal(f.routes.length, 0);
  assert.equal(f.useSession.getState().loggedIn, true);
});

test('late mutation mapping cannot run its cache-writing success callback for B', async (t) => {
  const f = fixture(t); await f.member(); const mapping = deferred(); const started = deferred(); let writes = 0;
  f.respond((url) => url.endsWith('/auth/login') ? success(credentials('member-b')) : success({ nick: 'a', handle: 'a' }));
  const mutation = f.queryClient.getMutationCache().build(f.queryClient, {
    mutationFn: async () => { const me = await f.api.member.update({ nick: 'a' }); started.resolve(); await mapping.promise; return me; },
    onSuccess: (me) => { writes++; f.queryClient.setQueryData(['member', 'me'], me); },
  });
  const pending = mutation.execute().catch((e) => e);
  await started.promise; await f.api.auth.login('b@example.test', 'password'); mapping.resolve();
  assert.ok(query.isCancelledError(await pending)); assert.equal(writes, 0);
  assert.equal(f.queryClient.getQueryData(['member', 'me']), undefined);
});

for (const action of ['logout', 'deleteAccount']) test(`${action} clears previous queries and group; deletion restarts onboarding`, async (t) => {
  const f = fixture(t); await f.member(); f.seed();
  f.respond(() => success(undefined));
  if (action === 'logout') await f.api.auth.logout(); else await f.api.member.deleteAccount();
  f.useSession.getState().logout(action === 'deleteAccount');
  f.assertCleared(); assert.equal(await f.tokens.access(), null);
  assert.equal(f.useSession.getState().loggedIn, false);
  assert.equal(f.useSession.getState().onboarded, action !== 'deleteAccount');
  assert.equal(f.routes.length, 0);
});

test('login form rejection and guest COMMON401 never clear an existing member token', async (t) => {
  const f = fixture(t); await f.member();
  f.respond(() => failure('AUTH4001'));
  await assert.rejects(f.api.auth.login('bad@example.test', 'bad'));
  f.respond(() => failure());
  await assert.rejects(f.request('POST', '/auth/login', { auth: 'device' }));
  assert.equal(await f.tokens.access(), 'member-a'); assert.equal(f.routes.length, 0);
});

test('restore cannot overwrite a login that completed while the old me request was pending', async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred();
  f.useSession.setState({ restored: false, loggedIn: false });
  f.respond((url) => { if (url.endsWith('/auth/login')) return success(credentials('member-b')); started.resolve(); return old.promise; });
  const restoring = f.useSession.getState().restore(); await started.promise;
  await f.api.auth.login('b@example.test', 'password'); old.resolve(failure()); await restoring;
  assert.equal(f.useSession.getState().loggedIn, true); assert.equal(f.useSession.getState().restored, true);
  assert.equal(await f.tokens.access(), 'member-b'); assert.equal(f.routes.length, 0);
});

test('missing refresh token expires once and does not leave a stuck refresh promise', async (t) => {
  const f = fixture(t); await f.tokens.replace('member-a', null, 'login');
  f.respond(() => failure());
  await assert.rejects(f.request('GET', '/me'));
  assert.equal(f.useSession.getState().loggedIn, false);
  await f.member('member-b');
  f.respond((url) => url.endsWith('/auth/refresh') ? success(credentials('renewed-b')) : success('ok'));
  assert.equal(await f.request('GET', '/me'), 'ok');
  assert.equal(f.routes.length, 1);
});

test('an A response arriving after B login neither refreshes nor expires B', async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred();
  f.respond((url) => { if (url.endsWith('/auth/login')) return success(credentials('member-b')); started.resolve(); return old.promise; });
  const pending = f.request('GET', '/me').catch((e) => e); await started.promise;
  await f.api.auth.login('b@example.test', 'password'); old.resolve(failure());
  assert.ok(query.isCancelledError(await pending)); assert.equal(await f.tokens.access(), 'member-b');
  assert.equal(f.calls.filter((r) => r.url.endsWith('/auth/refresh')).length, 0);
  assert.equal(f.routes.length, 0);
});

test('an old logout does not discard B tokens', async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred();
  f.respond((url) => { if (url.endsWith('/auth/login')) return success(credentials('member-b')); started.resolve(); return old.promise; });
  const pending = f.api.auth.logout().catch((e) => e); await started.promise;
  await f.api.auth.login('b@example.test', 'password'); old.resolve(success(undefined));
  assert.ok(query.isCancelledError(await pending));
  assert.equal(await f.tokens.access(), 'member-b'); assert.equal(f.useSession.getState().loggedIn, true);
});

test('a refresh completed by another request is reused for a late 401 from the same session', async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred();
  f.respond((url, init) => {
    if (url.endsWith('/slow') && init.headers.Authorization === 'Bearer member-a') { started.resolve(); return old.promise; }
    if (url.endsWith('/auth/refresh')) return success(credentials('renewed'));
    return init.headers.Authorization === 'Bearer renewed' ? success('ok') : failure();
  });
  const pending = f.request('GET', '/slow'); await started.promise;
  assert.equal(await f.request('GET', '/fast'), 'ok'); old.resolve(failure());
  assert.equal(await pending, 'ok');
  assert.equal(f.calls.filter((r) => r.url.endsWith('/auth/refresh')).length, 1);
});

test('an authenticated 500 never expires the session even with a misleading 401 envelope', async (t) => {
  const f = fixture(t); await f.member(); f.respond(() => failure('COMMON401', 500));
  await assert.rejects(f.request('GET', '/me'), (e) => e.code === 'NETWORK');
  assert.equal(await f.tokens.access(), 'member-a'); assert.equal(f.routes.length, 0);
});

test('account changes publish a new revision for push reassociation', async (t) => {
  const f = fixture(t); await f.member(); const before = f.useSession.getState().revision;
  await f.member('member-b');
  assert.equal(f.useSession.getState().loggedIn, true);
  assert.ok(f.useSession.getState().revision > before);
});

// 화면의 실제 mutation 선언을 실행해 전환 직후 onSuccess까지 차단되지 않는지 확인한다.
function screenMutation(relative, name, context) {
  const filename = path.join(app, relative);
  const ast = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let initializer;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === name) initializer = node.initializer.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(initializer, `${relative}: ${name}`);
  const source = ts.transpileModule(`result = ${initializer}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const scope = { ...context, result: undefined };
  vm.runInNewContext(source, scope);
  return scope.result;
}

for (const kind of ['email', 'signup', 'apple', 'kakao', 'delete']) test(`${kind} screen runs success navigation after its own session transition`, async (t) => {
  const f = fixture(t); let navigations = 0; let nickScreens = 0;
  if (kind === 'delete') await f.member(); else f.seed();
  f.respond(() => success(kind === 'delete' ? undefined : { ...credentials('b'), newMember: kind === 'kakao' }));
  const context = {
    useMutation: (options) => f.queryClient.getMutationCache().build(f.queryClient, options),
    useSession: f.useSession, tokens: f.tokens,
    api: f.api, email: 'b@example.test', pw: 'password', nick: 'b', code: '123456',
    login: f.useSession.getState().login, track: () => {}, toast: () => {}, setErr: () => {}, setSocialErr: () => {}, setDelOpen: () => {},
    done: () => navigations++, leave: () => navigations++, router: { push: () => nickScreens++ },
    kakaoLogin: async () => ({ token: 'kakao' }), appleLogin: async () => ({ token: 'apple' }),
  };
  const screen = kind === 'delete' ? 'app/profile/index.tsx' : kind === 'signup' ? 'app/auth/signup.tsx' : 'app/login.tsx';
  const name = kind === 'delete' ? 'del' : ['apple', 'kakao'].includes(kind) ? 'social' : 'submit';
  await screenMutation(screen, name, context).execute(kind);
  assert.equal(navigations + nickScreens, 1); f.assertCleared();
  assert.equal(f.useSession.getState().loggedIn, kind !== 'delete');
});

test('profile ignores completion of a logout from the previous account', async (t) => {
  const f = fixture(t); await f.member(); const old = deferred(); const started = deferred(); let navigations = 0;
  f.respond((url) => { if (url.endsWith('/auth/login')) return success(credentials('member-b')); started.resolve(); return old.promise; });
  const signOut = screenMutation('app/profile/index.tsx', 'signOut', { api: f.api, isCancelledError: query.isCancelledError, leave: () => { navigations++; f.useSession.getState().logout(false); } });
  const pending = signOut(); await started.promise; await f.api.auth.login('b@example.test', 'password'); old.resolve(success(undefined)); await pending;
  assert.equal(navigations, 0); assert.equal(f.useSession.getState().loggedIn, true);
});


test('delayed SecureStore refresh writes cannot persist A tokens over a later B login', async (t) => {
  const gate = deferred(); const started = deferred();
  const f = fixture(t, { native: true, beforeWrite: async (key, value) => {
    if (key === 'etforca.access' && value === 'renewed-a') { started.resolve(); await gate.promise; }
  } });
  await f.member();
  const oldSave = f.tokens.save('renewed-a', 'refresh-renewed-a').catch((e) => e);
  await started.promise;
  const login = f.member('member-b'); await tick(); gate.resolve();
  assert.ok(query.isCancelledError(await oldSave)); await login;
  assert.equal(await f.tokens.access(), 'member-b');
  assert.equal(f.memory.get('etforca.access'), 'member-b');
  assert.equal(f.memory.get('etforca.refresh'), 'refresh-member-b');
});

test('token removal remains last when SecureStore is still saving a login', async (t) => {
  const gate = deferred(); const started = deferred();
  const f = fixture(t, { native: true, beforeWrite: async (key, value) => {
    if (key === 'etforca.access' && value === 'member-a') { started.resolve(); await gate.promise; }
  } });
  const signingIn = f.member().catch((e) => e); await started.promise;
  const expiring = f.useSession.getState().expire(); await tick(); gate.resolve();
  assert.ok(query.isCancelledError(await signingIn)); await expiring;
  assert.equal(await f.tokens.access(), null);
  assert.equal(f.memory.has('etforca.access'), false); assert.equal(f.memory.has('etforca.refresh'), false);
  assert.equal(f.useSession.getState().loggedIn, false);
  // 로그인이 화면에 확립되기 전 취소된 경우에는 추가 로그인 이동이 없다.
  assert.equal(f.routes.length, 0);
});

test('switching groups during a session change must not keep the old group as placeholder data', async (t) => {
  const f = fixture(t); f.seed();
  const key = (group) => ['home', 'brief', group];
  f.queryClient.setQueryData(key('old-group'), { owner: 'guest' });
  f.respond((url) => url.endsWith('/auth/login') ? success(credentials('member-b')) : new Promise(() => {}));
  const options = f.homeOptions;
  const observer = new query.QueryObserver(f.queryClient, options('old-group'));
  const unsubscribe = observer.subscribe(() => {}); t.after(unsubscribe);
  const unwatch = f.useWatchGroup.subscribe((state) => observer.setOptions(options(state.group))); t.after(unwatch);
  const unsession = f.useSession.subscribe(() => observer.setOptions(options(f.useWatchGroup.getState().group))); t.after(unsession);
  await f.api.auth.login('b@example.test', 'password');
  assert.equal(observer.getCurrentResult().data, undefined);
});


test('a social SDK result from before B login cannot replace B with A', async (t) => {
  const f = fixture(t); const sdk = deferred(); const started = deferred();
  f.respond(() => success(credentials('member-b')));
  const social = screenMutation('app/login.tsx', 'social', {
    useMutation: (options) => f.queryClient.getMutationCache().build(f.queryClient, options),
    useSession: f.useSession, tokens: f.tokens, api: f.api,
    kakaoLogin: () => { started.resolve(); return sdk.promise; },
    isApiError: () => false, setSocialErr: () => {},
  });
  const pending = social.execute('kakao').catch((e) => e); await started.promise;
  await f.api.auth.login('b@example.test', 'password'); sdk.resolve({ token: 'member-a' });
  assert.ok(query.isCancelledError(await pending)); assert.equal(await f.tokens.access(), 'member-b');
  assert.equal(f.calls.filter((r) => r.url.endsWith('/auth/social')).length, 0);
});

test('an offline member write fails now and is never replayed as a guest', async (t) => {
  const f = fixture(t); await f.member();
  query.onlineManager.setOnline(false); t.after(() => query.onlineManager.setOnline(true));
  f.queryClient.mount(); t.after(() => f.queryClient.unmount());
  f.respond(() => { throw new Error('offline'); });
  const mutation = f.queryClient.getMutationCache().build(f.queryClient, { mutationFn: () => f.api.watch.setMembers('base', ['A']) });
  const pending = mutation.execute().catch((e) => e); await tick();
  assert.equal(mutation.state.isPaused, false); assert.equal((await pending).code, 'NETWORK');
  await f.api.auth.logout().catch(() => {});
  const before = f.calls.length; f.respond(() => success(undefined)); query.onlineManager.setOnline(true); await tick();
  assert.equal(f.calls.length, before);
});

for (const kind of ['homeOptions', 'watchOptions']) test(`${kind}: pending group placeholder is kept only within the same account`, async (t) => {
  const f = fixture(t); await f.member();
  const options = f[kind]; const old = options('old');
  f.queryClient.setQueryData(old.queryKey, [{ owner: 'member-a' }]);
  f.respond((url) => url.endsWith('/auth/login') ? success(credentials('member-b')) : new Promise(() => {}));
  const observer = new query.QueryObserver(f.queryClient, old);
  const unsubscribe = observer.subscribe(() => {}); t.after(unsubscribe);
  observer.setOptions(options('pending'));
  assert.equal(observer.getCurrentResult().data[0].owner, 'member-a');
  assert.equal(observer.getCurrentResult().isPlaceholderData, true);
  const unsession = f.useSession.subscribe(() => observer.setOptions(options('pending'))); t.after(unsession);
  await f.api.auth.login('b@example.test', 'password');
  assert.equal(observer.getCurrentResult().data, undefined);
});


test('a group creation waiting for invalidation cannot select its old group after B logs in', async (t) => {
  const f = fixture(t); await f.member();
  const refresh = deferred(); const refreshing = deferred();
  f.queryClient.setQueryData(['watch', 'groups'], []);
  f.respond((url, init) => {
    if (url.endsWith('/auth/login')) return success(credentials('member-b'));
    if (init.method === 'POST') return success({ key: 'a-created', label: 'A' });
    if (init.headers.Authorization === 'Bearer member-b') return success([]);
    refreshing.resolve(); return refresh.promise;
  });
  const groups = new query.QueryObserver(f.queryClient, { queryKey: ['watch', 'groups'], queryFn: () => f.api.watch.groups(), staleTime: Infinity });
  const ungroup = groups.subscribe(() => {}); t.after(ungroup);
  const observer = new query.MutationObserver(f.queryClient, f.createGroupOptions());
  const unsubscribe = observer.subscribe(() => {}); t.after(unsubscribe);
  const pending = observer.mutate('A', { onSuccess: (group) => f.useWatchGroup.getState().setGroup(group.key) }).catch((e) => e);
  await refreshing.promise; await f.api.auth.login('b@example.test', 'password'); refresh.resolve(success([{ key: 'a-created' }]));
  assert.ok(query.isCancelledError(await pending)); assert.equal(f.useWatchGroup.getState().group, 'base');
});

for (const action of ['create', 'delete']) test(`${action} group UI callback cannot alter a later account selection`, async (t) => {
  const f = fixture(t); await f.member(); let callback;
  const context = {
    useSession: f.useSession, name: 'A', group: { key: 'a-created' },
    create: { isPending: false, mutate: (_name, options) => { callback = options.onSuccess; } },
    del: { mutate: (_key, options) => { callback = options.onSuccess; } },
    setName: () => {}, toast: () => {}, onClose: () => {},
    onCreated: (group) => f.useWatchGroup.getState().setGroup(group.key), setGroup: f.useWatchGroup.getState().setGroup,
  };
  const submit = screenMutation(action === 'create' ? 'src/features/watch/NewGroupSheet.tsx' : 'src/features/watch/DeleteGroupDialog.tsx', action === 'create' ? 'submit' : 'run', context);
  submit(); await f.member('member-b'); f.useWatchGroup.getState().setGroup('b-selected');
  callback({ key: 'a-created' }); assert.equal(f.useWatchGroup.getState().group, 'b-selected');
});

test('home prefetch preserves same-session placeholders on shared query keys', async (t) => {
  const f = fixture(t); await f.member(); const old = f.homeOptions('old');
  f.queryClient.setQueryData(old.queryKey, { etfs: [], group: 'old' });
  f.respond(() => new Promise(() => {}));
  const observer = new query.QueryObserver(f.queryClient, old);
  const unsubscribe = observer.subscribe(() => {}); t.after(unsubscribe);
  f.prefetchHome(['old']);
  observer.setOptions(f.homeOptions('pending'));
  assert.equal(observer.getCurrentResult().data.group, 'old');
  assert.equal(observer.getCurrentResult().isPlaceholderData, true);
});


for (const mode of ['query', 'mutation']) test(`${mode}: already-resolved A work cannot refill B's Infinity profile cache`, async (t) => {
  // resolve 이후 query의 setData·전역 onSuccess·훅 onSuccess 사이의 각 microtask 경계.
  for (let gap = 0; gap < 22; gap++) {
    const f = fixture(t); await f.member();
    const old = deferred(); const login = deferred(); const oldStarted = deferred(); const loginStarted = deferred();
    f.respond((url) => {
      if (url.endsWith('/auth/login')) { loginStarted.resolve(); return login.promise; }
      oldStarted.resolve(); return old.promise;
    });
    const run = mode === 'query'
      ? f.queryClient.fetchQuery({ queryKey: ['member', 'me'], queryFn: () => f.api.member.me(), staleTime: Infinity, retry: false })
      : f.queryClient.getMutationCache().build(f.queryClient, f.updateMeOptions()).execute({ nick: 'a' });
    const result = run.catch(() => {}); await oldStarted.promise;
    const signingIn = f.api.auth.login('b@example.test', 'password'); await loginStarted.promise;
    old.resolve(success({ nick: 'old-a', handle: 'a' }));
    for (let i = 0; i < gap; i++) await Promise.resolve();
    login.resolve(success(credentials('member-b')));
    await signingIn; await result; await tick();
    assert.equal(await f.tokens.access(), 'member-b');
    assert.notEqual(f.queryClient.getQueryData(['member', 'me'])?.nick, 'old-a', `gap=${gap}`);
    f.respond(() => success(credentials('member-b').me));
    const me = await f.queryClient.fetchQuery({ queryKey: ['member', 'me'], queryFn: () => f.api.member.me(), staleTime: Infinity });
    assert.equal(me.nick, 'member-b', `gap=${gap}`);
    f.queryClient.clear();
  }
});

for (const kind of ['profile', 'vote', 'like', 'disclaimer']) test(`${kind}: the cache-writing callback itself rejects the previous session`, async (t) => {
  const f = fixture(t); await f.member();
  const options = kind === 'profile' ? f.updateMeOptions() : kind === 'vote' ? f.voteOptions() : kind === 'like' ? f.likeOptions()
    : screenMutation('src/features/disclaimer/DisclaimerSheet.tsx', 'accept', { useMutation: (o) => o, useSession: f.useSession, qc: f.queryClient, api: f.api });
  const revision = options.onMutate(); await f.member('member-b');
  options.onSuccess({ id: 'old-post', nick: 'old-a', handle: 'a', mine: 'buy' }, undefined, revision);
  f.assertCleared();
});


test('a mutation scheduled before B login cannot dispatch its A payload using B credentials', async (t) => {
  const f = fixture(t); await f.member(); f.respond(() => success(undefined));
  const mutation = f.queryClient.getMutationCache().build(f.queryClient, { mutationFn: () => f.api.watch.setMembers('base', ['A']) });
  const pending = mutation.execute().catch((e) => e);
  await f.member('member-b');
  assert.ok(query.isCancelledError(await pending));
  assert.equal(f.calls.filter((r) => r.method === 'PUT').length, 0);
});

for (const provider of ['apple', 'kakao']) test(`${provider}: stale SDK result is rejected while B tokens await Zustand synchronization`, async (t) => {
  const f = fixture(t);
  const sdk = deferred(); const sdkStarted = deferred();
  const synchronizing = deferred(); const resume = deferred();
  const cancel = f.queryClient.cancelQueries.bind(f.queryClient);
  f.queryClient.cancelQueries = async (...args) => {
    await cancel(...args);
    synchronizing.resolve();
    await resume.promise;
  };
  t.after(() => { resume.resolve(); f.queryClient.cancelQueries = cancel; });
  f.respond((url) => success(credentials(url.endsWith('/auth/social') ? 'old-a' : 'member-b')));
  const social = screenMutation('app/login.tsx', 'social', {
    useMutation: (options) => f.queryClient.getMutationCache().build(f.queryClient, options),
    useSession: f.useSession, tokens: f.tokens, api: f.api,
    appleLogin: () => { sdkStarted.resolve(); return sdk.promise; },
    kakaoLogin: () => { sdkStarted.resolve(); return sdk.promise; },
    isApiError: () => false, setSocialErr: () => {},
  });
  const old = social.execute(provider).catch((e) => e); await sdkStarted.promise;
  const login = f.api.auth.login('b@example.test', 'password').catch((e) => e);
  await synchronizing.promise;
  assert.equal(await f.tokens.access(), 'member-b');
  assert.notEqual(f.tokens.version(), f.useSession.getState().revision);
  sdk.resolve({ token: 'old-a' }); await tick();
  resume.resolve(); await login;
  assert.ok(query.isCancelledError(await old));
  assert.equal(f.calls.filter((r) => r.url.endsWith('/auth/social')).length, 0);
  assert.equal(await f.tokens.access(), 'member-b');
  assert.equal(f.useSession.getState().loggedIn, true);
  assert.equal(f.useSession.getState().revision, f.tokens.version());
});

for (const next of ['member-b', 'guest']) test(`push registration finishes A before associating the device with ${next}`, async (t) => {
  const f = fixture(t); await f.member();
  const old = deferred(); const started = deferred();
  let deviceMember;
  f.respond(async (_url, init) => {
    const member = init.headers.Authorization ?? 'guest';
    if (member === 'Bearer member-a') { started.resolve(); await old.promise; }
    deviceMember = member;
    return success(undefined);
  });
  const registeringA = f.registerPush(); await started.promise;
  if (next === 'guest') await f.tokens.clear(); else await f.member(next);
  const registeringNext = f.registerPush(); await tick();
  const inFlight = f.calls.length;
  old.resolve(); await Promise.all([registeringA, registeringNext]);
  assert.equal(inFlight, 1, 'new association must wait for the in-flight server write');
  assert.equal(deviceMember, next === 'guest' ? 'guest' : `Bearer ${next}`);
  assert.equal(f.calls.length, 2, 'stale response cancellation must not block the latest registration');
});
