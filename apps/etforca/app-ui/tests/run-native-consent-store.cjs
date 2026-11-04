const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const app = path.resolve(__dirname, '..');
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'etforca-consent-store-'));
function run(command, args) { execFileSync(command, args, { cwd: app, stdio: 'inherit' }); }
// Requires a JDK and Swift command-line tools; this does not compile either mobile app.
run('javac', ['-d', out, 'modules/ads-consent/android/src/main/java/com/alphaeveryday/adsconsent/ConsentStore.java', 'tests/native-consent/TestConsentStore.java']);
run('java', ['-cp', out, 'TestConsentStore']);
const swift = path.join(out, 'swift-tests');
run('swiftc', ['-module-cache-path', path.join(out, 'cache'), 'modules/ads-consent/ios/ConsentStore.swift', 'tests/native-consent/TestConsentStore.swift', '-o', swift]);
run(swift, []);
