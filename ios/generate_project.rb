#!/usr/bin/env ruby
# Optional XcodeGen alternative using the xcodeproj gem already shipped with CocoaPods.
require 'xcodeproj'
require 'yaml'

Dir.chdir(__dir__)
spec = YAML.safe_load(File.read('project.yml'))
project = Xcodeproj::Project.new('Overwatch.xcodeproj')
spec.fetch('targets').each do |name, config|
  target = project.new_target(:application, name, :ios, '17.0')
  settings = spec.fetch('settings').fetch('base').merge(config.fetch('settings').fetch('base'))
  target.build_configurations.each { |build| build.build_settings.merge!(settings) }
  group = project.main_group.new_group(name)
  config.fetch('sources').each do |entry|
    Dir.glob("#{entry.fetch('path')}/**/*.swift").sort.each do |path|
      target.source_build_phase.add_file_reference(group.new_file(path))
    end
  end
  scheme = Xcodeproj::XCScheme.new
  scheme.add_build_target(target)
  scheme.set_launch_target(target)
  scheme.save_as('Overwatch.xcodeproj', name, true)
end
project.save
puts 'Generated Overwatch.xcodeproj. Run pod install, then open Overwatch.xcworkspace.'
