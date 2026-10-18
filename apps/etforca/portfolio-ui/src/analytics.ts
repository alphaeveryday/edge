import posthog from 'posthog-js';
import { preview } from './preview';

export type Screen = 'input_holdings' | 'input_funding' | 'input_horizon' | 'input_interests' | 'input_question' | 'loading' | 'queued' | 'running' | 'result' | 'failed' | 'dashboard_overview' | 'dashboard_dividends' | 'dashboard_returns' | 'dashboard_backtest';
const events = new Set(['$pageview', '$pageleave', 'portfolio_screen_engagement', 'portfolio_scroll_pause', 'portfolio_feedback', 'portfolio_action']);
const properties = new Set(['token', 'distinct_id', '$lib', '$lib_version', '$device_type', '$browser', '$os', '$viewport_height', '$viewport_width', '$screen_height', '$screen_width', '$process_person_profile', '$cookieless_mode', 'screen', 'section', 'visible_seconds', 'pause_seconds', 'scroll_y', 'scroll_depth_pct', 'max_scroll_depth_pct', 'action', 'rating']);
let initialized = false;
const preferenceKey = 'orca-usage-analytics';
export function analyticsEnabled() { try { return localStorage.getItem(preferenceKey) !== 'off' && navigator.doNotTrack !== '1'; } catch { return false; } }
export function setAnalyticsEnabled(enabled: boolean) {
  try { localStorage.setItem(preferenceKey, enabled ? 'on' : 'off'); } catch { return; }
  if (initialized) {
    if (enabled) { posthog.set_config({ disable_persistence: false }); posthog.opt_in_capturing({ captureEventName: false }); posthog.startSessionRecording(true); }
    else { posthog.stopSessionRecording(); posthog.opt_out_capturing(); posthog.set_config({ disable_persistence: true }); }
  } else if (enabled) initialize();
  window.dispatchEvent(new Event('orca-analytics-change'));
}
export function trackAction(action: 'holding_add_opened' | 'holding_edit_opened' | 'holdings_saved' | 'holdings_save_failed' | 'backtest_requested' | 'backtest_completed' | 'backtest_failed' | 'portfolio_saved' | 'newsletter_requested') {
  if (!initialize()) return;
  try { posthog.capture('portfolio_action', { action }); } catch { /* Optional analytics. */ }
}

export type FeedbackRating = 'good' | 'okay' | 'needs_improvement';
export function trackFeedback(action: 'shown' | 'dismissed' | 'submitted', rating?: FeedbackRating) {
  if (!initialize()) return;
  try {
    posthog.capture('portfolio_feedback', { screen: 'result', section: 'feedback', action, ...(rating ? { rating } : {}) }, { send_instantly: true });
  } catch { /* Analytics must not interrupt reading the report. */ }
}

function initialize() {
  if (preview || !analyticsEnabled()) return false;
  if (!['etforca.com', 'www.etforca.com'].includes(location.hostname) || location.pathname !== '/portfolio/') return false;
  if (!initialized) {
    try {
      posthog.init('phc_yiMfdC2Hi7iPBWHg7F9n3dBokvnXkjcsqFzN8Bs496R3', {
        api_host: 'https://us.i.posthog.com', persistence: 'localStorage+cookie', persistence_name: 'orca_portfolio',
        cookie_expiration: 365, secure_cookie: true, cross_subdomain_cookie: false, person_profiles: 'never', respect_dnt: true,
        save_referrer: false, save_campaign_params: false, disable_capture_url_hashes: true,
        capture_pageview: false, capture_pageleave: false, autocapture: false,
        disable_session_recording: false, disable_surveys: true, capture_performance: false, enable_recording_console_log: false,
        session_recording: {
          maskAllInputs: true, maskTextSelector: '*',
          blockSelector: 'svg, canvas, img, video, iframe, .dividend-plot, .gain, .loss, .step .chips, input[type="hidden"], input[type="file"], .ph-no-capture',
          // rrweb's _cssText contains the public stylesheet needed to reconstruct the layout.
          maskAttributeFn: (name, value) => name === 'class' ? value.replace(/\b(gain|loss)\b/g, 'financial-value') : ['style', '_cssText', 'type', 'role', 'width', 'height', 'aria-expanded', 'aria-pressed', 'data-variant', 'data-tone'].includes(name) || name === 'id' && ['root', 'report'].includes(value) ? value : '',
          recordHeaders: false, recordBody: false, recordCrossOriginIframes: false,
          maskCapturedNetworkRequestFn: data => Object.keys(data).length === 1 ? { ...data, name: 'https://etforca.com/portfolio/' } : null,
        },
        loaded: instance => { instance.opt_in_capturing(); instance.startSessionRecording(true); },
        advanced_disable_feature_flags: true, enable_heatmaps: false, capture_dead_clicks: false,
        before_send: event => {
          if (event?.event === '$snapshot') {
            event.properties = Object.fromEntries(Object.entries(event.properties).filter(([key]) => ['token', 'distinct_id', '$session_id', '$window_id', '$snapshot_data', '$snapshot_bytes', '$lib', '$lib_version'].includes(key)));
            return event;
          }
          if (!event || !events.has(event.event)) return null;
          event.properties = Object.fromEntries(Object.entries(event.properties).filter(([key]) => properties.has(key) || ['$session_id', '$window_id'].includes(key)));
          event.properties.$current_url = 'https://etforca.com/portfolio/';
          const screen = event.properties.screen;
          event.properties.$pathname = screen ? `/portfolio/${String(screen).replaceAll('_', '/')}` : '/portfolio/';
          event.properties.$current_url = 'https://etforca.com' + event.properties.$pathname;
          event.properties.$host = 'etforca.com';
          return { event: event.event, properties: event.properties, timestamp: event.timestamp, uuid: event.uuid };
        },
      });
      initialized = true;
    } catch (error) { console.warn('PostHog initialization failed', error); return false; }
  }
  return true;
}

export function trackScreen(screen: Screen) {
  let stop = startScreen(screen);
  const restart = () => { stop(); stop = startScreen(screen); };
  window.addEventListener('orca-analytics-change', restart);
  return () => { stop(); window.removeEventListener('orca-analytics-change', restart); };
}

function startScreen(screen: Screen) {
  let stop = () => {};
  // React StrictMode's discarded mount must not create a second visit.
  const start = window.setTimeout(() => {
    if (!initialize()) return;
    const capture = (event: string, values: Record<string, string | number> = {}) => {
      try {
        posthog.capture(event, { screen, ...values }, ended || document.visibilityState !== 'visible'
          ? { send_instantly: true, transport: 'sendBeacon' } : undefined);
      } catch { /* Analytics must not interrupt an analysis. */ }
    };
    let visibleSince = document.visibilityState === 'visible' ? performance.now() : null;
    let visibleMs = 0;
    let maxDepth = 0;
    let pauseSince = visibleSince;
    let pausePosition = position();
    let ended = false;
    let viewed = false;
    function view() {
      if (!viewed && document.visibilityState === 'visible') { viewed = true; capture('$pageview'); }
    }
    function position() {
      const height = Math.max(document.documentElement.scrollHeight, innerHeight);
      const element = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      return {
        section: element?.closest('[data-analytics-section]')?.getAttribute('data-analytics-section') || screen,
        scroll_y: Math.round(scrollY),
        scroll_depth_pct: Math.min(100, Math.round((scrollY + innerHeight) / height * 100)),
      };
    }
    function flushPause() {
      if (pauseSince === null) return;
      const seconds = (performance.now() - pauseSince) / 1000;
      if (seconds >= 2) capture('portfolio_scroll_pause', { ...pausePosition, pause_seconds: Math.round(seconds * 10) / 10 });
      pauseSince = null;
    }
    function scroll() {
      if (ended || document.visibilityState !== 'visible') return;
      flushPause();
      pausePosition = position();
      maxDepth = Math.max(maxDepth, pausePosition.scroll_depth_pct);
      pauseSince = performance.now();
    }
    function flushTime() {
      if (visibleSince !== null) visibleMs += performance.now() - visibleSince;
      visibleSince = null;
      if (visibleMs >= 1) capture('portfolio_screen_engagement', { visible_seconds: Math.round(visibleMs) / 1000, max_scroll_depth_pct: maxDepth });
      visibleMs = 0;
    }
    function visibility() {
      if (ended) return;
      if (document.visibilityState !== 'visible') { flushPause(); flushTime(); }
      else { view(); visibleSince = performance.now(); pauseSince = visibleSince; pausePosition = position(); }
    }
    function end() {
      if (ended) return;
      ended = true;
      flushPause(); flushTime();
      if (viewed) capture('$pageleave', { max_scroll_depth_pct: maxDepth });
    }
    function resume() {
      if (!ended) return;
      ended = false; viewed = false;
      visibility();
    }
    maxDepth = pausePosition.scroll_depth_pct;
    view();
    const heartbeat = window.setInterval(() => {
      if (!ended && document.visibilityState === 'visible') {
        flushTime(); visibleSince = performance.now();
      }
    }, 15000);
    const layout = new ResizeObserver(scroll);
    layout.observe(document.body);
    window.addEventListener('scroll', scroll, { passive: true });
    window.addEventListener('resize', scroll);
    window.addEventListener('pagehide', end);
    window.addEventListener('pageshow', resume);
    document.addEventListener('visibilitychange', visibility);
    stop = () => {
      end(); clearInterval(heartbeat); layout.disconnect();
      window.removeEventListener('scroll', scroll); window.removeEventListener('resize', scroll);
      window.removeEventListener('pagehide', end); window.removeEventListener('pageshow', resume);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, 0);
  return () => { clearTimeout(start); stop(); };
}
