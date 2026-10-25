import CoreLocation
import CoreMotion
import Foundation
import UIKit

struct SensorSnapshot {
    var headingDeg: Double?           // true heading (magnetic + 10° accuracy penalty if true north is unavailable)
    var headingAccuracyDeg: Double?
    var headingUptimeS: Double?
    var gps: GPSFix?
    var gpsUptimeS: Double?
    var headYawCWDeg: Double?         // AirPods yaw in the headphones' own reference frame, clockwise positive
    var headUptimeS: Double?
    var batteryPct: Double?
    var locationAuthorized = false
    var headTrackingAvailable = false
}

/// Compass + GPS (CoreLocation), optional AirPods head tracking (CMHeadphoneMotionManager), battery level.
/// `start`/`setHeadTracking` must be called on the main thread; `snapshot` is safe from any thread.
final class SensorHub: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private let headphones = CMHeadphoneMotionManager()
    private let state = Locked(SensorSnapshot())
    private var started = false
    private(set) var headTrackingActive = false

    var snapshot: SensorSnapshot { state.current }

    func start() {
        guard !started else { return }
        started = true
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = kCLDistanceFilterNone
        manager.headingFilter = 1.0
        // The responder UI is portrait-locked. VERIFY: with the phone held upright (camera forward) confirm that
        // trueHeading tracks the camera's facing direction on your iOS version; if it tracks the top edge instead,
        // hold the phone tilted or rely on the drone/ARKit heading sources on the Mac.
        manager.headingOrientation = .portrait
        let available = headphones.isDeviceMotionAvailable
        state.update { $0.headTrackingAvailable = available }
        if manager.authorizationStatus == .notDetermined {
            manager.requestWhenInUseAuthorization()
        }
        startUpdatesIfAuthorized()
        UIDevice.current.isBatteryMonitoringEnabled = true
        updateBattery()
        NotificationCenter.default.addObserver(self, selector: #selector(batteryChanged),
                                               name: UIDevice.batteryLevelDidChangeNotification, object: nil)
    }

    private func startUpdatesIfAuthorized() {
        let status = manager.authorizationStatus
        let ok = status == .authorizedWhenInUse || status == .authorizedAlways
        state.update { $0.locationAuthorized = ok }
        if ok {
            manager.startUpdatingLocation()
        }
        if CLLocationManager.headingAvailable() {
            manager.startUpdatingHeading()  // magnetic heading works without location permission
        }
    }

    // MARK: CLLocationManagerDelegate (main thread)

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        startUpdatesIfAuthorized()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        let now = DeviceClock.nowS()
        let accuracy = newHeading.headingAccuracy
        var heading: Double?
        var headingAccuracy: Double?
        if accuracy >= 0 {
            if newHeading.trueHeading >= 0 {
                heading = newHeading.trueHeading
                headingAccuracy = accuracy
            } else if newHeading.magneticHeading >= 0 {
                heading = newHeading.magneticHeading   // no true north without a location fix
                headingAccuracy = accuracy + 10.0
            }
        }
        state.update { s in
            s.headingDeg = heading
            s.headingAccuracyDeg = headingAccuracy
            s.headingUptimeS = now
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let loc = locations.last, loc.horizontalAccuracy >= 0,
              CLLocationCoordinate2DIsValid(loc.coordinate) else { return }
        let fix = GPSFix(lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
                         accuracy_m: Wire.rounded(loc.horizontalAccuracy, 1))
        let now = DeviceClock.nowS()
        state.update { s in
            s.gps = fix
            s.gpsUptimeS = now
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // Transient (e.g. kCLErrorLocationUnknown); CoreLocation keeps trying.
    }

    func locationManagerShouldDisplayHeadingCalibration(_ manager: CLLocationManager) -> Bool {
        true
    }

    // MARK: AirPods head tracking (main thread)

    func setHeadTracking(_ enabled: Bool) {
        if enabled {
            guard !headTrackingActive, headphones.isDeviceMotionAvailable else { return }
            headTrackingActive = true
            headphones.startDeviceMotionUpdates(to: OperationQueue.main) { [weak self] motion, _ in
                guard let self = self, let motion = motion else { return }
                // CoreMotion yaw is counter-clockwise positive about the vertical; the wire uses clockwise degrees.
                // VERIFY: turning your head to the right must make head_yaw_deg positive.
                let yawCW = -motion.attitude.yaw * 180.0 / .pi
                let now = DeviceClock.nowS()
                self.state.update { s in
                    s.headYawCWDeg = yawCW
                    s.headUptimeS = now
                }
            }
        } else {
            guard headTrackingActive else { return }
            headphones.stopDeviceMotionUpdates()
            headTrackingActive = false
            state.update { s in
                s.headYawCWDeg = nil
                s.headUptimeS = nil
            }
        }
    }

    // MARK: Battery

    @objc private func batteryChanged() {
        updateBattery()
    }

    private func updateBattery() {
        let level = UIDevice.current.batteryLevel
        let pct: Double? = level >= 0 ? Wire.rounded(Double(level) * 100.0, 0) : nil
        state.update { $0.batteryPct = pct }
    }
}
