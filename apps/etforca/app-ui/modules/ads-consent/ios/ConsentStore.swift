import Foundation
import CoreFoundation

// No SDK initialization; errors must not be interpreted as an absent preference.
struct ConsentStore {
  let directory: URL
  private let name = ".posthog-rn.json"

  func read() throws -> String? { try readFile(name) }

  private func readFile(_ name: String) throws -> String? {
    let entries = try FileManager.default.contentsOfDirectory(atPath: directory.path)
    guard entries.contains(name) else { return nil }
    return try String(contentsOf: directory.appendingPathComponent(name), encoding: .utf8)
  }

  func preference() throws -> Bool? {
    guard let raw = try read() else { return nil }
    let data = try JSONSerialization.jsonObject(with: Data(raw.utf8))
    guard let root = data as? [String: Any], root["version"] as? String == "v1",
          let content = root["content"] as? [String: Any] else {
      throw NSError(domain: "AdsConsent", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid PostHog storage"])
    }
    guard let value = content["opted_out"] else { return nil }
    guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else {
      throw NSError(domain: "AdsConsent", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid PostHog opt-out"])
    }
    return !number.boolValue
  }

  // Only used when no PostHog client exists. Otherwise PostHog remains the sole writer.
  func writePreference(_ on: Bool) throws {
    var root: [String: Any] = ["version": "v1", "content": [:]]
    if let raw = try read() {
      _ = try preference()
      root = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as! [String: Any]
    }
    var content = root["content"] as! [String: Any]
    content["opted_out"] = !on
    root["content"] = content
    try JSONSerialization.data(withJSONObject: root).write(to: directory.appendingPathComponent(name), options: .atomic)
  }
}
