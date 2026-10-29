const { withAppDelegate, withAndroidManifest, withInfoPlist, withMainApplication } = require('expo/config-plugins');

function restoreBeforeReact(contents) {
  const replacement = 'AdsConsentStartup.configure(application, launchOptions: launchOptions)';
  if (contents.includes(replacement)) return contents;
  if (!contents.includes('FirebaseApp.configure()')) throw new Error('withAdsConsent: Firebase AppDelegate insertion not found');
  return 'import AdsConsent\n' + contents.replace('FirebaseApp.configure()', replacement);
}
function restoreBeforeActivity(contents) {
  const invocation = 'com.alphaeveryday.adsconsent.AdsConsentStartup.configure(this)';
  if (contents.includes(invocation)) return contents;
  const anchor = 'super.onCreate()';
  if (!contents.includes(anchor)) throw new Error('withAdsConsent: Application onCreate insertion not found');
  return contents.replace(anchor, `${anchor}\n    ${invocation}`);
}
function gateAndroid(manifest, configured = true) {
  manifest.manifest.$['xmlns:tools'] = 'http://schemas.android.com/tools';
  const app = manifest.manifest.application[0];
  app.provider ??= [];
  const provider = 'com.facebook.internal.FacebookInitProvider';
  app.provider = app.provider.filter((p) => p.$['android:name'] !== provider);
  app.provider.push({ $: { 'android:name': provider, 'tools:node': 'remove' } });
  // The core-only dependency does not include the unused Facebook Login activities.
  app.activity = (app.activity ?? []).filter((a) => !['com.facebook.FacebookActivity', 'com.facebook.CustomTabActivity'].includes(a.$['android:name']));
  app.service ??= [];
  const name = 'com.google.firebase.components.ComponentDiscoveryService';
  let discovery = app.service.find((s) => s.$['android:name'] === name);
  if (!discovery) app.service.push(discovery = { $: { 'android:name': name } });
  const registrar = 'com.google.firebase.components:com.google.firebase.analytics.connector.internal.AnalyticsConnectorRegistrar';
  discovery['meta-data'] = (discovery['meta-data'] ?? []).filter((m) => m.$['android:name'] !== registrar);
  discovery['meta-data'].push({ $: { 'android:name': registrar, 'tools:node': 'remove' } });
  // SDK 19 adds optional metadata collection; preserve the existing measurement scope.
  app['meta-data'] ??= [];
  for (const key of ['AutoLogMetaDataEnabled', 'AutoLogProductImprovementEnabled']) {
    const name = `com.facebook.sdk.${key}`;
    app['meta-data'] = app['meta-data'].filter((m) => m.$['android:name'] !== name);
    app['meta-data'].push({ $: { 'android:name': name, 'android:value': 'false' } });
  }
  for (const [name, value] of Object.entries({
    ETFOrcaAnalyticsConfigured: configured,
    firebase_analytics_collection_enabled: !configured,
    'com.facebook.sdk.AutoLogAppEventsEnabled': !configured,
    'com.facebook.sdk.AdvertiserIDCollectionEnabled': !configured,
  })) {
    app['meta-data'] = app['meta-data'].filter((m) => m.$['android:name'] !== name);
    app['meta-data'].push({ $: { 'android:name': name, 'android:value': String(value) } });
  }
  return manifest;
}
function setAppleDefaults(plist, configured) {
  plist.ETFOrcaAnalyticsConfigured = configured;
  plist.FIREBASE_ANALYTICS_COLLECTION_ENABLED = !configured;
  plist.FacebookAutoLogAppEventsEnabled = !configured;
  plist.FacebookAdvertiserIDCollectionEnabled = !configured;
  plist.FBSDKAutoLogMetaDataEnabled = false;
  plist.FBSDKAutoLogProductImprovementEnabled = false;
  return plist;
}
module.exports = (config) => {
  const configured = !!process.env.EXPO_PUBLIC_POSTHOG_KEY && process.env.EXPO_PUBLIC_API_MODE === 'http';
  config = withAppDelegate(config, (c) => {
    if (c.modResults.language !== 'swift') throw new Error('withAdsConsent requires a Swift AppDelegate');
    c.modResults.contents = restoreBeforeReact(c.modResults.contents);
    return c;
  });
  config = withInfoPlist(config, (c) => {
    c.modResults = setAppleDefaults(c.modResults, configured);
    return c;
  });
  config = withMainApplication(config, (c) => {
    if (c.modResults.language !== 'kt') throw new Error('withAdsConsent requires a Kotlin MainApplication');
    c.modResults.contents = restoreBeforeActivity(c.modResults.contents);
    return c;
  });
  return withAndroidManifest(config, (c) => {
    c.modResults = gateAndroid(c.modResults, configured);
    return c;
  });
};
module.exports.restoreBeforeReact = restoreBeforeReact;
module.exports.restoreBeforeActivity = restoreBeforeActivity;
module.exports.gateAndroid = gateAndroid;
module.exports.setAppleDefaults = setAppleDefaults;
