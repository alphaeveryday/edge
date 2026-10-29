import Foundation

@main
struct TestConsentStore {
  static func main() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let store = ConsentStore(directory: directory)
    let absent = try store.preference()
    assert(absent == nil)
    let file = directory.appendingPathComponent(".posthog-rn.json")
    func save(_ raw: String) throws { try Data(raw.utf8).write(to: file) }
    try save(#"{"version":"v1","content":{"opted_out":true,"distinct_id":"keep-me"}}"#)
    let refused = try store.preference()
    assert(refused == false)
    try store.writePreference(true)
    let permitted = try ConsentStore(directory: directory).preference()
    let retained = try store.read()
    assert(permitted == true && retained!.contains("keep-me"))
    for raw in ["broken", #"{"version":"v2","content":{}}"#, #"{"version":"v1","content":[]}"#,
                #"{"version":"v1","content":{"opted_out":1}}"#, #"{"version":"v1","content":{"opted_out":"false"}}"#] {
      try save(raw)
      do { _ = try store.preference(); fatalError("invalid preference accepted") } catch {}
    }
    try save(#"{"version":"v1","content":{}}"#)
    let unspecified = try store.preference()
    assert(unspecified == nil)
    do { _ = try ConsentStore(directory: file).read(); fatalError("list failure hidden") } catch {}
    try FileManager.default.removeItem(at: file)
    try FileManager.default.createDirectory(at: file, withIntermediateDirectories: true)
    do { _ = try store.read(); fatalError("read failure hidden") } catch {}
    print("Swift consent preference: 11 scenarios passed")
  }
}
