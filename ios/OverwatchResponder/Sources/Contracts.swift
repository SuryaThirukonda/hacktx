import Foundation

// Responder-side mirror of backend/overwatch/schemas.py (schema_version 1.0) and docs/contracts.md.
// Property names ARE the wire keys (snake_case) on purpose, so they can never drift from the pydantic models.
// Uplink structs encode every key explicitly, emitting `null` for missing geometry (never 0).

// MARK: - Uplink: /ws/phone

struct DepthSector: Codable, Equatable, Identifiable {
    var sector: String              // left | front_left | front | front_right | right
    var nearest_m: Double?          // null + confidence "unknown" = not observed; null + other = observed and clear
    var confidence: String          // high | medium | low | unknown
    var num_valid_points: Int

    var id: String { sector }

    static func unknown(_ name: String) -> DepthSector {
        DepthSector(sector: name, nearest_m: nil, confidence: "unknown", num_valid_points: 0)
    }

    enum CodingKeys: String, CodingKey {
        case sector, nearest_m, confidence, num_valid_points
    }

    init(sector: String, nearest_m: Double?, confidence: String, num_valid_points: Int) {
        self.sector = sector
        self.nearest_m = nearest_m
        self.confidence = confidence
        self.num_valid_points = num_valid_points
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sector = try c.decode(String.self, forKey: .sector)
        nearest_m = try c.decodeIfPresent(Double.self, forKey: .nearest_m)
        confidence = try c.decodeIfPresent(String.self, forKey: .confidence) ?? "unknown"
        num_valid_points = try c.decodeIfPresent(Int.self, forKey: .num_valid_points) ?? 0
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(sector, forKey: .sector)
        try c.encode(nearest_m, forKey: .nearest_m)
        try c.encode(confidence, forKey: .confidence)
        try c.encode(num_valid_points, forKey: .num_valid_points)
    }
}

struct GPSFix: Codable, Equatable {
    var lat: Double
    var lon: Double
    var accuracy_m: Double?
}

/// `{"type":"phone_observation", ...}` at ~10 Hz.
struct PhoneObservation: Encodable {
    var schema_version = Wire.schemaVersion
    var type = "phone_observation"
    var session_id: String?
    var source_id = "responder-iphone"
    var seq: Int
    var capture_ts_ms: Double                 // ARFrame.timestamp * 1000 (DeviceClock timebase)
    var arkit_tracking_state: String          // normal | limited | not_available
    var tracking_reason: String?
    var camera_pose_4x4: [[Double]]?          // ARKit world-from-camera, row-major, y up; only when tracking is normal
    var camera_intrinsics_3x3: [[Double]]?    // at depth-map resolution
    var image_resolution: [Int]?              // capturedImage [w, h] (sensor-native)
    var image_orientation: String?            // up | down | left | right: rotation that made the JPEG/boxes upright
    var lidar_supported: Bool
    var depth_mode: String                    // sceneDepth | smoothedSceneDepth | none
    var depth_valid_fraction: Double?
    var sectors: [DepthSector]
    var obstacle_points: [[Double]]?          // ARKit world, floor removed, <= 400 voxel-downsampled
    var floor_y_m: Double?
    var heading_deg: Double?
    var heading_accuracy_deg: Double?
    var head_yaw_deg: Double?
    var gps: GPSFix?
    var battery_pct: Double?
    var vision_boxes: [[Double]]?             // normalised xyxy, TOP-LEFT origin, upright image (same as the JPEG)
    var vision_ranges_m: [Double?]?
    var local_alert_active: Bool

    enum CodingKeys: String, CodingKey {
        case schema_version, type, session_id, source_id, seq, capture_ts_ms, arkit_tracking_state, tracking_reason,
             camera_pose_4x4, camera_intrinsics_3x3, image_resolution, image_orientation, lidar_supported, depth_mode,
             depth_valid_fraction, sectors, obstacle_points, floor_y_m, heading_deg, heading_accuracy_deg,
             head_yaw_deg, gps, battery_pct, vision_boxes, vision_ranges_m, local_alert_active
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schema_version, forKey: .schema_version)
        try c.encode(type, forKey: .type)
        try c.encode(session_id, forKey: .session_id)
        try c.encode(source_id, forKey: .source_id)
        try c.encode(seq, forKey: .seq)
        try c.encode(capture_ts_ms, forKey: .capture_ts_ms)
        try c.encode(arkit_tracking_state, forKey: .arkit_tracking_state)
        try c.encode(tracking_reason, forKey: .tracking_reason)
        try c.encode(camera_pose_4x4, forKey: .camera_pose_4x4)
        try c.encode(camera_intrinsics_3x3, forKey: .camera_intrinsics_3x3)
        try c.encode(image_resolution, forKey: .image_resolution)
        try c.encode(image_orientation, forKey: .image_orientation)
        try c.encode(lidar_supported, forKey: .lidar_supported)
        try c.encode(depth_mode, forKey: .depth_mode)
        try c.encode(depth_valid_fraction, forKey: .depth_valid_fraction)
        try c.encode(sectors, forKey: .sectors)
        try c.encode(obstacle_points, forKey: .obstacle_points)
        try c.encode(floor_y_m, forKey: .floor_y_m)
        try c.encode(heading_deg, forKey: .heading_deg)
        try c.encode(heading_accuracy_deg, forKey: .heading_accuracy_deg)
        try c.encode(head_yaw_deg, forKey: .head_yaw_deg)
        try c.encode(gps, forKey: .gps)
        try c.encode(battery_pct, forKey: .battery_pct)
        try c.encode(vision_boxes, forKey: .vision_boxes)
        try c.encode(vision_ranges_m, forKey: .vision_ranges_m)
        try c.encode(local_alert_active, forKey: .local_alert_active)
    }
}

/// `{"type":"ack","alert_id":"a-12"}` when the responder acknowledges.
struct AckMessage: Encodable {
    var type = "ack"
    let alert_id: String
}

// MARK: - Downlink: alert, responder_view, transcript, ack, sync

/// Mirror of `Alert` (backend/overwatch/schemas.py); named WireAlert to avoid SwiftUI.Alert. Decoded leniently: only id/kind/severity are required.
struct WireAlert: Decodable, Equatable, Identifiable {
    let alert_id: String
    let kind: String
    let severity: String            // info | attention | warning | critical | sensor_fault | local_proximity
    let ts_ms: Double?
    let expires_ts_ms: Double?
    let text: String?
    let spoken_text: String?
    let bearing_rel_deg: Double?
    let clock: Int?
    let range_m: Double?
    let closing_mps: Double?
    let ttc_s: Double?
    let haptic: String?             // CLOSING_FAST | TARGET_LOST | REACQUIRED | PROXIMITY | DIRECTION_LEFT | DIRECTION_RIGHT
    let delivery: [String]?         // dashboard | phone_speech | phone_haptic
    let message_source: String?
    let ack_required: Bool?
    let acked: Bool?
    let can_read_as_confirmed_location: Bool?

    var id: String { alert_id }
    var wantsSpeech: Bool { delivery?.contains("phone_speech") ?? false }
    var wantsHaptic: Bool { delivery?.contains("phone_haptic") ?? false }
    var requiresAck: Bool { (ack_required ?? false) && !(acked ?? false) }
    var displayText: String {
        if let t = text, !t.isEmpty { return t }
        if let s = spoken_text, !s.isEmpty { return s }
        return kind
    }
}

/// Mirror of `RelativeKinematics` (the subject relative to the responder). Only `valid == true` geometry is drawn.
struct RelativeKinematics: Decodable, Equatable {
    let valid: Bool?
    let reason: String?
    let range_m: Double?
    let range_error_m: Double?
    let bearing_rel_deg: Double?    // clockwise from the responder's facing direction, (-180, 180]
    let bearing_abs_deg: Double?
    let clock: Int?
    let closing_mps: Double?
    let ttc_s: Double?
    let subject_speed_mps: Double?

    var isValid: Bool { valid ?? false }
}

/// Mirror of `ResponderState` (only the fields the phone shows).
struct ResponderStateWire: Decodable, Equatable {
    let position_source: String?
    let position_error_m: Double?
    let heading_deg: Double?
    let heading_source: String?
    let heading_valid: Bool?
}

/// `{"type":"responder_view", ...}` (~5 Hz) built by `Runtime.responder_view` in backend/overwatch/runtime.py.
struct ResponderView: Decodable {
    let ts_ms: Double?
    let session_id: String?
    let subject_state: String?
    let verified: Bool?
    let kin: RelativeKinematics?
    let responder: ResponderStateWire?
    let sectors: [DepthSector]?
    let local_alert: Bool?
    let alerts: [WireAlert]?
    let mode: String?
    let ai_paused: Bool?
    let profile: String?
}

struct TranscriptLine: Decodable, Equatable {
    let ts_ms: Double?
    let text: String?
    let source: String?
    let alert_id: String?
}
