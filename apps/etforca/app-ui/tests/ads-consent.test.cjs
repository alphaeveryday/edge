const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const app = path.resolve(__dirname, '..');
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture({ initial = false, configured = true, platform = 'ios', nativeUpdate = async () => {}, tracking } = {}) {
  const calls = [], errors = [], cache = new Map();
  const ready = deferred();
  let client;
  class PostHog {
    constructor() { client = this; this.optedOut = !initial; calls.push(['client']); }
    register() {}
    ready() { return ready.promise; }
    optIn() { this.optedOut = false; calls.push(['optIn']); return Promise.resolve(); }
    optOut() { this.optedOut = true; calls.push(['optOut']); return Promise.resolve(); }
    capture(event) { if (!this.optedOut) calls.push(['event', event]); }
    screen(name) { if (!this.optedOut) calls.push(['screen', name]); }
  }
  const bridge = {
    getInitialPreference() { calls.push(['native-preference']); return initial; },
    async setCollectionEnabled(on, persist) { calls.push(['collection', on, persist]); await nativeUpdate(on); },
    async setTrackingAllowed(on) { calls.push(['tracking', on]); },
  };
  function load(name) {
    if (cache.has(name)) return cache.get(name).exports;
    const module = { exports: {} }; cache.set(name, module);
    const source = ts.transpileModule(fs.readFileSync(path.join(app, name), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(source, {
      module, exports: module.exports,
      console: { warn: (...args) => errors.push(args) },
      process: { env: { EXPO_PUBLIC_API_MODE: 'http', EXPO_PUBLIC_POSTHOG_KEY: configured ? 'test' : '' } },
      require(id) {
        if (id === 'react-native') return { Platform: { OS: platform } };
        if (id === 'expo-modules-core') return { requireNativeModule: () => bridge };
        if (id === 'expo-tracking-transparency') return { requestTrackingPermissionsAsync: () => {
          calls.push(['permission']); return tracking?.promise ?? Promise.resolve({ status: 'granted' });
        } };
        if (id === 'posthog-react-native') return PostHog;
        if (id === '@/store/session') return { useSession: { getState: () => ({ loggedIn: false }), subscribe() {} } };
        if (id.startsWith('@/lib/')) return load(`src/lib/${id.split('/').at(-1)}.ts`);
        return require(id);
      },
    }, { filename: name });
    return module.exports;
  }
  const analytics = load('src/lib/analytics.ts');
  const ads = load('src/lib/ads.ts');
  return { analytics, ads, calls, errors, ready, get client() { return client; },
    setOn(on) { analytics.useAnalyticsOn.getState().setOn(on); },
    async settle() { await tick(); await tick(); },
  };
}
for (const platform of ['ios', 'android']) {
  test(`${platform}: native refusal is used without waiting for PostHog readiness`, async () => {
    const f = fixture({ platform });
    assert.equal(f.analytics.useAnalyticsOn.getState().on, false);
    f.analytics.track('refused'); f.analytics.trackScreen('refused');
    await f.ads.startAds();
    assert.equal(f.calls.some(([n]) => ['event', 'screen', 'client', 'collection'].includes(n)), false);
  });
  test(`${platform}: native permission preserves events before the splash/ATT effect`, () => {
    const f = fixture({ platform, initial: true });
    f.analytics.track('onboarding_intro_viewed'); f.analytics.trackScreen('/');
    assert.deepEqual(f.calls.filter(([n]) => ['event', 'screen'].includes(n)), [['event', 'onboarding_intro_viewed'], ['screen', '/']]);
    assert.equal(f.calls.some(([n]) => n === 'collection'), false);
  });
}
test('revocation takes effect before a pending ATT response and PostHog ready', async () => {
  const tracking = deferred(), f = fixture({ initial: true, tracking });
  const prompt = f.ads.startAds(); await f.settle();
  f.setOn(false); f.analytics.track('after-off'); f.analytics.trackScreen('after-off');
  await f.settle();
  assert.ok(f.calls.some(([n, on]) => n === 'collection' && on === false));
  assert.ok(f.calls.some(([n]) => n === 'optOut'));
  tracking.resolve({ status: 'granted' }); await prompt;
  assert.equal(f.calls.some(([n, on]) => n === 'collection' && on === true), false);
  assert.equal(f.calls.some(([n]) => n === 'event' || n === 'screen'), false);
});
test('enabling an initially refused client still updates both SDKs immediately', async () => {
  const f = fixture(); f.setOn(true); await f.settle();
  assert.ok(f.calls.some(([n]) => n === 'optIn'));
  assert.deepEqual(f.calls.find(([n]) => n === 'collection'), ['collection', true, false]);
});
test('native-only explicit choices use the existing preference file', async () => {
  const f = fixture({ configured: false, initial: null });
  assert.equal(f.calls.some(([n]) => n === 'collection'), false);
  f.setOn(false); f.setOn(true); await f.settle();
  assert.deepEqual(f.calls.filter(([n]) => n === 'collection'), [['collection', false, true], ['collection', true, true]]);
  assert.equal(f.client, undefined);
});
test('a later refusal wins over an in-flight SDK enable', async () => {
  const enable = deferred();
  const f = fixture({ nativeUpdate: (on) => on ? enable.promise : Promise.resolve() });
  f.setOn(true); await f.settle(); f.setOn(false); enable.resolve(); await f.settle();
  assert.deepEqual(f.calls.filter(([n]) => n === 'collection').map((c) => c[1]), [true, false]);
});
test('failed native update is reported and does not prevent the next change', async () => {
  let first = true;
  const f = fixture({ nativeUpdate: async () => { if (first) { first = false; throw new Error('native failure'); } } });
  f.setOn(true); await f.settle(); f.setOn(false); await f.settle();
  assert.ok(f.errors.length); assert.equal(f.calls.filter(([n]) => n === 'collection').at(-1)[1], false);
});
test('web keeps memory preferences without a native module or ATT', async () => {
  const f = fixture({ platform: 'web' }); f.setOn(false); await f.ads.startAds(); await f.settle();
  assert.equal(f.calls.some(([n]) => ['native-preference', 'collection', 'tracking', 'permission'].includes(n)), false);
});
test('ATT is requested once and is independent of collection settings', async () => {
  const f = fixture({ initial: true }); await Promise.all([f.ads.startAds(), f.ads.startAds()]);
  assert.equal(f.calls.filter(([n]) => n === 'permission').length, 1);
  assert.equal(f.calls.some(([n]) => n === 'collection'), false);
});
test('native launch hooks precede React startup and are idempotent', () => {
  const { restoreBeforeReact, restoreBeforeActivity } = require('../plugins/withAdsConsent');
  const ios = restoreBeforeReact('FirebaseApp.configure()\nfactory.startReactNative()');
  assert.ok(ios.indexOf('AdsConsentStartup.configure') < ios.indexOf('factory.startReactNative'));
  assert.equal(restoreBeforeReact(ios), ios);
  const android = restoreBeforeActivity('super.onCreate()\nloadReactNative(this)');
  assert.ok(android.indexOf('AdsConsentStartup.configure') < android.indexOf('loadReactNative'));
  assert.equal(restoreBeforeActivity(android), android);
  assert.throws(() => restoreBeforeReact('unsupported'), /insertion not found/);
  assert.throws(() => restoreBeforeActivity('unsupported'), /insertion not found/);
});
test('native manifest keeps FCM core while routing Analytics and Meta through startup', () => {
  const { gateAndroid } = require('../plugins/withAdsConsent');
  const manifest = { manifest: { $: {}, application: [{
    provider: [{ $: { 'android:name': 'com.google.firebase.provider.FirebaseInitProvider' } }],
    activity: [{ $: { 'android:name': '.MainActivity' } }, { $: { 'android:name': 'com.facebook.CustomTabActivity' } }],
  }] } };
  gateAndroid(manifest); const once = JSON.stringify(manifest); gateAndroid(manifest);
  assert.equal(JSON.stringify(manifest), once);
  const app = manifest.manifest.application[0];
  assert.ok(app.provider.some((p) => p.$['android:name'].endsWith('FirebaseInitProvider') && !p.$['tools:node']));
  assert.ok(app.provider.some((p) => p.$['android:name'].endsWith('FacebookInitProvider') && p.$['tools:node'] === 'remove'));
  assert.ok(app.service[0]['meta-data'].some((m) => m.$['android:name'].endsWith('AnalyticsConnectorRegistrar') && m.$['tools:node'] === 'remove'));
  assert.deepEqual(app.activity.map((a) => a.$['android:name']), ['.MainActivity']);
});

for (const configured of [true, false]) test(`native defaults preserve existing no-PostHog behavior: configured=${configured}`, () => {
  const { gateAndroid, setAppleDefaults } = require('../plugins/withAdsConsent');
  const plist = setAppleDefaults({}, configured);
  for (const key of ['FIREBASE_ANALYTICS_COLLECTION_ENABLED', 'FacebookAutoLogAppEventsEnabled', 'FacebookAdvertiserIDCollectionEnabled']) {
    assert.equal(plist[key], !configured);
  }
  const manifest = { manifest: { $: {}, application: [{}] } };
  gateAndroid(manifest, configured);
  const metadata = Object.fromEntries(manifest.manifest.application[0]['meta-data'].map((m) => [m.$['android:name'], m.$['android:value']]));
  for (const key of ['firebase_analytics_collection_enabled', 'com.facebook.sdk.AutoLogAppEventsEnabled', 'com.facebook.sdk.AdvertiserIDCollectionEnabled']) {
    assert.equal(metadata[key], String(!configured));
  }
  assert.equal(metadata.ETFOrcaAnalyticsConfigured, String(configured));
});
test('no PostHog/no saved choice keeps the existing on UI default without overriding native SDK storage', () => {
  const f = fixture({ configured: false, initial: null });
  assert.equal(f.analytics.useAnalyticsOn.getState().on, true);
  assert.equal(f.calls.some(([n]) => n === 'collection'), false);
});
