require 'json'
require_relative '../../../node_modules/@react-native-firebase/app/firebase_spm'
firebase_package = JSON.parse(File.read(File.join(__dir__, '../../../node_modules/@react-native-firebase/app/package.json')))
firebase_version = ENV['FIREBASE_SDK_VERSION'] || firebase_package['sdkVersions']['ios']['firebase']

Pod::Spec.new do |s|
  s.name = 'AdsConsent'
  s.version = '1.0.0'
  s.summary = 'Consent-gated installation measurement'
  s.description = s.summary
  s.license = { :type => 'Proprietary' }
  s.author = 'Alpha Everyday'
  s.homepage = 'https://github.com/alphaeveryday/edge'
  s.source = { :git => s.homepage }
  s.platforms = { :ios => '16.4' }
  s.swift_version = '5.9'
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.dependency 'RNFBApp'
  s.dependency 'RNFBAnalytics'
  firebase_dependency(s, firebase_version, ['FirebaseCore', 'FirebaseAnalytics'], [])
  # Match RNFBAnalytics: these products must be shared between dynamic frameworks.
  spm_dependency(s,
    url: 'https://github.com/google/GoogleUtilities.git',
    requirement: { kind: 'upToNextMajorVersion', minimumVersion: '8.1.3' },
    products: ['GULNetwork', 'GULReachability', 'GULMethodSwizzler'])
  s.dependency 'FBSDKCoreKit', '19.0.0'
  s.source_files = '**/*.swift'
end
