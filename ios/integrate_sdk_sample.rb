#!/usr/bin/env ruby
# Update only the existing sample's background relay. SDK UI, bundle ID, DJI key and signing are preserved.
require 'xcodeproj'
require 'fileutils'
require 'digest'

sample = File.expand_path(ARGV.fetch(0) { abort 'Usage: ruby integrate_sdk_sample.rb /path/to/DJISDKSwiftDemo.xcodeproj' })
root = File.dirname(sample)
fpv = File.join(root, 'DJISDKSwiftDemo/Camera/fpv/CameraFPVViewController.swift')
original = File.read(fpv)
marker = original.index('// Background adapter for the EXISTING DJI sample FPV screen.') ||
         original.index(/(?:@available\(iOS 13\.0, \*\)\s*)?(?:final )?class DJIVideoRelay\b/)
# A clean DJI sample can use the checked-in FPV screen; existing customized UI is preserved.
fpv_ui = marker ? original[0...marker] : File.read(File.join(__dir__, 'SDKSample/CameraFPVViewController.swift'))
project = Xcodeproj::Project.open(sample)
target = project.targets.find { |t| t.name == 'DJISDKSwiftDemo' }
abort 'Sample app target missing' unless target
backup = File.join(__dir__, 'sdk-sample-backups', Digest::SHA256.hexdigest(original)[0, 16])
FileUtils.mkdir_p(backup)
FileUtils.cp(fpv, File.join(backup, 'CameraFPVViewController.swift')) unless File.exist?(File.join(backup, 'CameraFPVViewController.swift'))
FileUtils.cp(File.join(sample, 'project.pbxproj'), File.join(backup, 'project.pbxproj')) unless File.exist?(File.join(backup, 'project.pbxproj'))
group = project.groups.find { |g| g.display_name == 'OverwatchRelayCore' } || project.new_group('OverwatchRelayCore')
files = Dir[File.join(__dir__, 'Shared/Sources/*.swift')] + Dir[File.join(__dir__, 'OverwatchDroneBridge/Sources/*.swift')]
files.reject! { |f| %w[BridgeModel.swift OverwatchDroneBridgeApp.swift].include?(File.basename(f)) }
files.each do |path|
  ref = group.files.find { |f| f.path == path } || group.new_file(path)
  target.source_build_phase.add_file_reference(ref, true) unless target.source_build_phase.files_references.include?(ref)
end
target.build_configurations.each { |c| c.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '17.0' }
File.write(fpv, fpv_ui + File.read(File.join(__dir__, 'SDKSampleRelay.swift')))
project.save
puts "Updated existing SDK sample relay; FPV UI, bundle/signing and DJI key retained. Backup: #{backup}"
