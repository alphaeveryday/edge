package com.alphaeveryday.adsconsent

import android.app.Application
import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import com.facebook.FacebookSdk
import com.google.firebase.analytics.FirebaseAnalytics
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.functions.Queues
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import org.json.JSONObject

// Application.onCreate runs before the first Activity and before background services.
object AdsConsentStartup {
  var preference: Boolean? = null
    private set
  private var metaStarted = false

  private fun readPreference(context: Context): Boolean? {
    val raw = ConsentStore(context.filesDir).read() ?: return null
    val root = JSONObject(raw)
    require(root.optString("version") == "v1") { "Invalid PostHog storage version" }
    val content = root.getJSONObject("content")
    if (!content.has("opted_out")) return null
    val value = content.get("opted_out")
    require(value is Boolean) { "Invalid PostHog opt-out" }
    return !value
  }

  @JvmStatic
  fun configure(application: Application) {
    preference = try {
      readPreference(application) ?: if (application.packageManager
        .getApplicationInfo(application.packageName, PackageManager.GET_META_DATA)
        .metaData?.getBoolean("ETFOrcaAnalyticsConfigured", false) == true) true else null
    } catch (error: Exception) {
      Log.e("AdsConsent", "Analytics preference restore failed", error)
      false
    }
    // Firebase core still starts normally for push; its eager Analytics registrar is removed.
    preference?.let { FirebaseAnalytics.getInstance(application).setAnalyticsCollectionEnabled(it) }
    configureMeta(application, preference)
  }

  private fun configureMeta(context: Context, on: Boolean?) {
    if (!metaStarted) {
      if (on == false) return
      val metadata = context.packageManager.getApplicationInfo(context.packageName, PackageManager.GET_META_DATA).metaData
      if (metadata?.getString("com.facebook.sdk.ApplicationId").isNullOrEmpty() ||
          metadata?.getString("com.facebook.sdk.ClientToken").isNullOrEmpty()) return
      FacebookSdk.setAutoInitEnabled(false)
      FacebookSdk.sdkInitialize(context.applicationContext)
      metaStarted = true
      // This runs before the first Activity is created; no lifecycle replay is needed.
    }
    if (on != null) {
      FacebookSdk.setAutoLogAppEventsEnabled(on)
      FacebookSdk.setAdvertiserIDCollectionEnabled(on)
    }
    FacebookSdk.fullyInitialize()
  }

  fun setCollectionEnabled(context: Context, on: Boolean, persist: Boolean) {
    // A disk failure must not prevent an immediate revocation.
    if (!on) applyCollection(context, false)
    if (persist) {
      val store = ConsentStore(context.filesDir)
      val raw = store.read()
      if (raw != null) readPreference(context) // Do not silently replace malformed storage.
      val root = if (raw == null) JSONObject().put("version", "v1").put("content", JSONObject()) else JSONObject(raw)
      root.getJSONObject("content").put("opted_out", !on)
      store.write(root.toString())
    }
    if (on) applyCollection(context, true)
  }

  private fun applyCollection(context: Context, on: Boolean) {
    preference = on
    FirebaseAnalytics.getInstance(context).setAnalyticsCollectionEnabled(on)
    configureMeta(context, on)
  }
}

class AdsConsentModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("AdsConsent")
    Function("getInitialPreference") { AdsConsentStartup.preference }
    AsyncFunction("setCollectionEnabled") { on: Boolean, persist: Boolean ->
      val context = appContext.reactContext ?: throw Exceptions.ReactContextLost()
      AdsConsentStartup.setCollectionEnabled(context, on, persist)
    }.runOnQueue(Queues.MAIN)
    AsyncFunction("setTrackingAllowed") { _: Boolean -> Unit }
  }
}
