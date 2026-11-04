import ExpoModulesCore
import FirebaseCore
import FirebaseAnalytics
import FBSDKCoreKit
import AppTrackingTransparency

// Called from AppDelegate before React Native starts, not from a JS effect.
public enum AdsConsentStartup {
  public private(set) static var preference: Bool?
  private static var metaStarted = false
  private static var launchOptions: [UIApplication.LaunchOptionsKey: Any]?

  private static func store() throws -> ConsentStore {
    let directory = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: false)
    return ConsentStore(directory: directory)
  }

  public static func configure(_ application: UIApplication, launchOptions: [UIApplication.LaunchOptionsKey: Any]?) {
    self.launchOptions = launchOptions
    do {
      preference = try store().preference()
      if preference == nil && Bundle.main.object(forInfoDictionaryKey: "ETFOrcaAnalyticsConfigured") as? Bool == true {
        preference = true // Preserve the existing first-install default.
      }
    } catch {
      preference = false
      NSLog("Analytics preference restore failed: %@", String(describing: error))
    }
    // Set the collection flag at native startup, before Firebase configures Analytics.
    if let on = preference { Analytics.setAnalyticsCollectionEnabled(on) }
    FirebaseApp.configure()
    // Reapply on the configured SDK; verify the pre-configuration call on device as well.
    if let on = preference { Analytics.setAnalyticsCollectionEnabled(on) }
    configureMeta(preference)
  }

  private static func configureMeta(_ on: Bool?) {
    if !metaStarted {
      guard on != false,
            let appID = Bundle.main.object(forInfoDictionaryKey: "FacebookAppID") as? String, !appID.isEmpty,
            let token = Bundle.main.object(forInfoDictionaryKey: "FacebookClientToken") as? String, !token.isEmpty else { return }
      // Read the system ATT status before starting; the JS prompt only updates this later.
      let trackingAllowed = ATTrackingManager.trackingAuthorizationStatus == .authorized
      Settings.shared.isAdvertiserTrackingEnabled = trackingAllowed
      ApplicationDelegate.shared.application(UIApplication.shared, didFinishLaunchingWithOptions: launchOptions)
      Settings.shared.isAdvertiserTrackingEnabled = trackingAllowed
      metaStarted = true
    }
    if let on = on {
      Settings.shared.isAutoLogAppEventsEnabled = on
      Settings.shared.isAdvertiserIDCollectionEnabled = on
    }
  }

  static func setCollectionEnabled(_ on: Bool, persist: Bool) throws {
    // A disk failure must not leave an already-running SDK collecting after revocation.
    if !on { applyCollection(false) }
    if persist { try store().writePreference(on) }
    if on { applyCollection(true) }
  }

  private static func applyCollection(_ on: Bool) {
    preference = on
    Analytics.setAnalyticsCollectionEnabled(on)
    configureMeta(on)
  }

  static func setTrackingAllowed(_ allowed: Bool) {
    if metaStarted { Settings.shared.isAdvertiserTrackingEnabled = allowed }
  }
}

public class AdsConsentModule: Module {
  public func definition() -> ModuleDefinition {
    Name("AdsConsent")
    Function("getInitialPreference") { AdsConsentStartup.preference }
    AsyncFunction("setCollectionEnabled") { (on: Bool, persist: Bool) in
      try AdsConsentStartup.setCollectionEnabled(on, persist: persist)
    }.runOnQueue(.main)
    AsyncFunction("setTrackingAllowed") { (allowed: Bool) in
      AdsConsentStartup.setTrackingAllowed(allowed)
    }.runOnQueue(.main)
  }
}
