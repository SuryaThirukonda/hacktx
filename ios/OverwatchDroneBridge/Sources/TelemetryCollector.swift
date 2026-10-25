import CoreLocation
import DJISDK
import Foundation

/// Caches the latest flight-controller / battery / gimbal / simulator state from the DJI delegates (main queue) so the
/// 10 Hz telemetry timer (bridge core queue) can read a consistent snapshot. Thread-safe.
final class TelemetryCollector: NSObject, DJIFlightControllerDelegate, DJIBatteryDelegate, DJIGimbalDelegate,
                                DJISimulatorDelegate {
    struct Cache {
        var fcUptimeMs: Double?          // DeviceClock ms when the last DJIFlightControllerState arrived
        var lat: Double?
        var lon: Double?
        var altRelM: Double?
        var yawDeg: Double?
        var pitchDeg = 0.0
        var rollDeg = 0.0
        var velNED: [Double]?
        var sats: Int?
        var gpsLevel: Int?
        var flying = false
        var motorsOn = false
        var flightMode: String?
        var batteryPct: Double?
        var gimbalPitchDeg: Double?
        var simActive = false
        var simUptimeMs: Double?
        var stateCount = 0
    }

    private let cache = Locked(Cache())

    var snapshot: Cache { cache.current }

    /// Main queue. Safe to call repeatedly (e.g. after every component (re)connect).
    func attach(to aircraft: DJIAircraft) {
        if let fc = aircraft.flightController {
            fc.delegate = self
            fc.simulator?.delegate = self
        }
        aircraft.battery?.delegate = self
        aircraft.gimbal?.delegate = self
    }

    func reset() {
        cache.set(Cache())
    }

    // MARK: DJIFlightControllerDelegate (10 Hz)

    func flightController(_ fc: DJIFlightController, didUpdate state: DJIFlightControllerState) {
        let now = DeviceClock.nowMs()
        var lat: Double?
        var lon: Double?
        if let location = state.aircraftLocation {
            let c = location.coordinate
            // No GPS fix is reported as (0, 0) / invalid: send null, never a fake position.
            if CLLocationCoordinate2DIsValid(c) && !(abs(c.latitude) < 1e-7 && abs(c.longitude) < 1e-7) {
                lat = c.latitude
                lon = c.longitude
            }
        }
        let attitude = state.attitude
        let level = Int(state.gpsSignalLevel.rawValue)       // DJIGPSSignalLevel: 0...5, 255 = none
        let sats = Int(state.satelliteCount)
        let simActive = fc.simulator?.isSimulatorActive ?? false
        let velocity = [Double(state.velocityX), Double(state.velocityY), Double(state.velocityZ)]
        cache.update { c in
            c.fcUptimeMs = now
            c.lat = lat
            c.lon = lon
            c.altRelM = Wire.finite(state.altitude)
            c.yawDeg = Wire.finite(attitude.yaw)
            c.pitchDeg = Wire.finite(attitude.pitch) ?? 0
            c.rollDeg = Wire.finite(attitude.roll) ?? 0
            c.velNED = velocity.allSatisfy { $0.isFinite } ? velocity : nil
            c.sats = sats
            c.gpsLevel = (0...5).contains(level) ? level : nil
            c.flying = state.isFlying
            c.motorsOn = state.areMotorsOn
            c.flightMode = state.flightModeString
            c.simActive = simActive
            c.stateCount += 1
        }
    }

    // MARK: DJIBatteryDelegate

    func battery(_ battery: DJIBattery, didUpdate batteryState: DJIBatteryState) {
        let pct = Double(batteryState.chargeRemainingInPercent)
        cache.update { $0.batteryPct = pct }
    }

    // MARK: DJIGimbalDelegate

    func gimbal(_ gimbal: DJIGimbal, didUpdate state: DJIGimbalState) {
        let pitch = Double(state.attitudeInDegrees.pitch)
        cache.update { $0.gimbalPitchDeg = pitch.isFinite ? pitch : nil }
    }

    // MARK: DJISimulatorDelegate

    /// Liveness only; `simActive` itself comes from `DJISimulator.isSimulatorActive` with every FC state.
    func simulator(_ simulator: DJISimulator, didUpdate state: DJISimulatorState) {
        let now = DeviceClock.nowMs()
        cache.update { $0.simUptimeMs = now }
    }
}
