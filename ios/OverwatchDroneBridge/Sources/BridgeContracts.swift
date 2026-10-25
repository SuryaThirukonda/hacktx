import Foundation

struct PilotOverrideMessage: Encodable {
    var type = "audit"
    var event = "pilot_override"
    let text: String
}

// Bridge-side mirror of backend/overwatch/schemas.py (schema_version 1.0) and docs/contracts.md (`/ws/bridge`).
// Property names ARE the wire keys (snake_case) on purpose.

enum BridgeIdentity {
    static let sourceId = "mini2-bridge"
    static let app = "OverwatchDroneBridge"
}

// MARK: - Uplink

/// `{"type":"telemetry", ...}` at 10 Hz. Every key is written explicitly (null for missing geometry, never 0).
struct DroneTelemetry: Encodable {
    var schema_version = Wire.schemaVersion
    var type = "telemetry"
    var source_id = BridgeIdentity.sourceId
    var seq: Int
    var capture_ts_ms: Double        // DeviceClock ms when the flight-controller state arrived
    var lat: Double?
    var lon: Double?
    var alt_rel_m: Double?           // barometric, above take-off
    var yaw_deg: Double?             // aircraft yaw, true north, clockwise
    var pitch_deg: Double
    var roll_deg: Double
    var vel_ned: [Double]?           // [north, east, down] m/s (DJI velocityX/Y/Z)
    var gimbal_pitch_deg: Double?
    var battery_pct: Double?
    var gps_level: Int?
    var sats: Int?
    var flying: Bool
    var vstick_enabled: Bool
    var sim: Bool                    // DJI simulator running (props off): the Mac treats it as a safe command target
    var flight_mode: String?

    enum CodingKeys: String, CodingKey {
        case schema_version, type, source_id, seq, capture_ts_ms, lat, lon, alt_rel_m, yaw_deg, pitch_deg, roll_deg,
             vel_ned, gimbal_pitch_deg, battery_pct, gps_level, sats, flying, vstick_enabled, sim, flight_mode
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schema_version, forKey: .schema_version)
        try c.encode(type, forKey: .type)
        try c.encode(source_id, forKey: .source_id)
        try c.encode(seq, forKey: .seq)
        try c.encode(capture_ts_ms, forKey: .capture_ts_ms)
        try c.encode(lat, forKey: .lat)
        try c.encode(lon, forKey: .lon)
        try c.encode(alt_rel_m, forKey: .alt_rel_m)
        try c.encode(yaw_deg, forKey: .yaw_deg)
        try c.encode(pitch_deg, forKey: .pitch_deg)
        try c.encode(roll_deg, forKey: .roll_deg)
        try c.encode(vel_ned, forKey: .vel_ned)
        try c.encode(gimbal_pitch_deg, forKey: .gimbal_pitch_deg)
        try c.encode(battery_pct, forKey: .battery_pct)
        try c.encode(gps_level, forKey: .gps_level)
        try c.encode(sats, forKey: .sats)
        try c.encode(flying, forKey: .flying)
        try c.encode(vstick_enabled, forKey: .vstick_enabled)
        try c.encode(sim, forKey: .sim)
        try c.encode(flight_mode, forKey: .flight_mode)
    }
}

/// `{"type":"audit","text":"watchdog hover"}` — safety-relevant events, shown in the dashboard's audit log.
struct AuditMessage: Encodable {
    var type = "audit"
    let text: String
    var source_id = BridgeIdentity.sourceId
    var ts_ms = DeviceClock.nowMs()
}

/// `{"type":"status", ...}` — sent on connect and whenever the product / virtual-stick / simulator state changes.
struct StatusMessage: Encodable {
    var type = "status"
    let text: String
    var source_id = BridgeIdentity.sourceId
    let product: String?
    let product_connected: Bool
    let vstick_enabled: Bool
    let sim: Bool
    let real_flight_armed: Bool
    let control_inhibited: Bool
    let control_phase: String
    let pilot_reason: String?
    let control_token: String
    let motion_profile: String
    let translation_supported: Bool
    let translation_armed: Bool
    let web_control_supported: Bool
}

// MARK: - Downlink

/// Untyped JSON value for `BridgeCommand.payload` (`dict[str, Any]` on the Mac).
enum JSONValue: Decodable, Equatable {
    case number(Double)
    case string(String)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([JSONValue].self) {
            self = .array(a)
        } else if let o = try? c.decode([String: JSONValue].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported JSON value")
        }
    }

    var double: Double? {
        switch self {
        case .number(let n): return n.isFinite ? n : nil
        case .string(let s): return Double(s)
        case .bool(let b): return b ? 1 : 0
        default: return nil
        }
    }

    var bool: Bool? {
        switch self {
        case .bool(let b): return b
        case .number(let n): return n != 0
        case .string(let s): return ["true", "1", "yes", "on"].contains(s.lowercased())
        default: return nil
        }
    }
}

/// Mac → bridge command: `{"type":..., "seq":..., "ts_ms":..., "payload":{...}}` (see the BridgeCommand table in
/// docs/contracts.md). `vstick` payloads are `VStickCommand.model_dump()`: forward_mps, right_mps, up_mps,
/// yaw_rate_dps, ttl_ms, seq, ts_ms.
struct BridgeCommand: Decodable {
    let type: String
    let seq: Double?
    let ts_ms: Double?
    let payload: [String: JSONValue]?

    func number(_ key: String) -> Double? { payload?[key]?.double }
    func flag(_ key: String) -> Bool? { payload?[key]?.bool }
    func string(_ key: String) -> String? {
        if case .string(let value) = payload?[key] { return value }
        return nil
    }
}
