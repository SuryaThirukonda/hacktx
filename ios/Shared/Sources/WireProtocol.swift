import Foundation
import os

// Wire-level helpers shared by OverwatchResponder and OverwatchDroneBridge.
// Contract: docs/contracts.md (schema_version 1.0), backend/overwatch/schemas.py, backend/overwatch/sources/live.py.

enum Wire {
    static let schemaVersion = "1.0"

    /// Byte 0 of every binary WebSocket message (device → Mac).
    enum FrameKind: UInt8 {
        case jpeg = 0x01
        case h264 = 0x02
    }

    /// `[1 byte kind][8 bytes capture timestamp: Float64 big-endian, device-clock milliseconds][payload]`
    /// (mirror of `pack_binary_frame` in backend/overwatch/sources/live.py).
    static func binaryFrame(kind: FrameKind, captureTsMs: Double, payload: Data) -> Data {
        var out = Data(capacity: payload.count + 9)
        out.append(kind.rawValue)
        var bits = captureTsMs.bitPattern.bigEndian
        withUnsafeBytes(of: &bits) { out.append(contentsOf: $0) }
        out.append(payload)
        return out
    }

    /// Builds the device-socket URL from what a person typed into the host field:
    /// "192.168.1.20" → ws://192.168.1.20:8000<path>, "mac.local:9000", "ws://10.0.0.5:8000", "https://host" → wss.
    /// `?token=` is appended when an access token is set (OVERWATCH_TOKEN on the Mac).
    static func socketURL(host raw: String, path: String, token: String?) -> URL? {
        var host = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else { return nil }
        var scheme = "ws"
        if let r = host.range(of: "://") {
            let given = host[..<r.lowerBound].lowercased()
            scheme = (given == "wss" || given == "https") ? "wss" : "ws"
            host = String(host[r.upperBound...])
        }
        if let slash = host.firstIndex(of: "/") {
            host = String(host[..<slash])
        }
        guard !host.isEmpty else { return nil }
        var comps = URLComponents()
        comps.scheme = scheme
        let parts = host.split(separator: ":", omittingEmptySubsequences: false)
        if parts.count == 2, let port = Int(parts[1]) {
            comps.host = String(parts[0])
            comps.port = port
        } else {
            comps.host = host
            if scheme == "ws" { comps.port = 8000 }
        }
        comps.path = path
        if let t = token?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty {
            comps.queryItems = [URLQueryItem(name: "token", value: t)]
        }
        return comps.url
    }

    /// `nil` for NaN / ±inf. JSONEncoder refuses non-finite numbers, and missing geometry is null, never 0.
    static func finite(_ value: Double?) -> Double? {
        guard let v = value, v.isFinite else { return nil }
        return v
    }

    static func rounded(_ value: Double, _ places: Int) -> Double {
        let p = pow(10.0, Double(places))
        return (value * p).rounded() / p
    }
}

/// Every timestamp a device puts on the wire (capture_ts_ms, binary-frame timestamps, sync replies) uses ONE clock:
/// seconds since boot excluding sleep. This is the timebase of `ARFrame.timestamp` and `CACurrentMediaTime()`, so
/// ARKit capture times and sync replies are directly comparable (the Mac's offset estimate stays valid).
enum DeviceClock {
    static func nowS() -> Double { ProcessInfo.processInfo.systemUptime }
    static func nowMs() -> Double { ProcessInfo.processInfo.systemUptime * 1000.0 }
}

enum WireJSON {
    private static let log = Logger(subsystem: "com.overwatch", category: "json")

    /// A fresh encoder per call keeps this safe to use from any queue.
    static func encode<T: Encodable>(_ value: T) -> String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        do {
            let data = try encoder.encode(value)
            return String(data: data, encoding: .utf8)
        } catch {
            log.error("encode \(String(describing: T.self), privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
            return nil
        }
    }

    static func decode<T: Decodable>(_ type: T.Type, from text: String) -> T? {
        guard let data = text.data(using: .utf8) else { return nil }
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            log.debug("decode \(String(describing: T.self), privacy: .public) failed: \(String(describing: error), privacy: .public)")
            return nil
        }
    }
}

/// Fields shared by the Mac → device messages, read before committing to a full schema.
struct WireProbe: Decodable {
    let type: String?
    let t0: Double?
    let alert_id: String?
    let detail: String?
}

/// Reply to the server-initiated `{"type":"sync","t0":…}`: t1 = device receive time, t2 = device send time (ms).
struct SyncReply: Encodable {
    var type = "sync_reply"
    let t0: Double
    let t1: Double
    let t2: Double
}

/// First message on every device socket. `synthetic: false` tells the Mac this is real sensor data
/// (backend/scripts/fake_devices.py sends `synthetic: true`).
struct HelloMessage: Encodable {
    var type = "hello"
    let device: String
    let source_id: String
    let app: String
    let text: String
    var synthetic = false
    var schema_version = Wire.schemaVersion
    var pilot_override_protocol: Int? = nil
}

/// Minimal lock-protected box for state shared across queues.
final class Locked<Value> {
    private var value: Value
    private let lock = NSLock()

    init(_ value: Value) {
        self.value = value
    }

    var current: Value {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    func set(_ newValue: Value) {
        lock.lock()
        value = newValue
        lock.unlock()
    }

    @discardableResult
    func update<R>(_ body: (inout Value) -> R) -> R {
        lock.lock()
        defer { lock.unlock() }
        return body(&value)
    }
}
