#!/usr/bin/env ruby
# Reuse the working sample app's local registration/signing values in the GENERATED project only.
# Nothing is installed on a phone. Generated Xcode projects are git-ignored.
require 'xcodeproj'
require 'pathname'

sample = ARGV.fetch(0) { abort 'Usage: ruby configure_bridge_from_sample.rb /path/to/DJISDKSwiftDemo.xcodeproj' }
sample = File.expand_path(sample)
source = Xcodeproj::Project.open(sample)
target = source.targets.find { |t| t.name == 'DJISDKSwiftDemo' }
abort 'DJISDKSwiftDemo target missing' unless target
settings = target.build_configurations.first.build_settings
bundle = settings['PRODUCT_BUNDLE_IDENTIFIER']
team = settings['DEVELOPMENT_TEAM']
root = File.dirname(sample)
plist_path = File.expand_path(settings.fetch('INFOPLIST_FILE').gsub('$(SRCROOT)', root).gsub('${SRCROOT}', root), root)
key = Xcodeproj::Plist.read_from_path(plist_path)['DJISDKAppKey']
abort 'Working sample app must have a DJI app key, literal bundle identifier and team' if
  [key, bundle, team].any? { |v| !v.is_a?(String) || v.empty? || v.include?('$(') }
project = Xcodeproj::Project.open(File.join(__dir__, 'Overwatch.xcodeproj'))
bridge = project.targets.find { |t| t.name == 'OverwatchDroneBridge' }
bridge.build_configurations.each do |config|
  config.build_settings['DJISDK_APP_KEY'] = key
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = bundle
  config.build_settings['DEVELOPMENT_TEAM'] = team
end
project.save
puts 'Configured the generated bridge project from the local working DJI sample. DJI key was not printed.'
puts 'Re-run after regenerating the Xcode project. Installing this bundle would replace the sample app.'
