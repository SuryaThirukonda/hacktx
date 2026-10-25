import ARKit
import Foundation
import SceneKit
import SwiftUI

/// Bounded mesh vertices in ARKit metres, gravity aligned (+y up), arbitrary yaw.
/// This scan has NO geographic alignment until an operator supplies a calibrated transform.
final class LocalScan {
    private let lock = NSLock()
    private var anchors: [UUID: [SIMD3<Float>]] = [:]
    private var capturedAt: Double = 0
    private(set) var frameID = UUID().uuidString
    let maxPoints = 20_000

    func reset() {
        lock.lock(); defer { lock.unlock() }
        anchors.removeAll(); frameID = UUID().uuidString; capturedAt = 0
    }

    func update(_ meshes: [ARMeshAnchor], timestamp: Double) {
        lock.lock(); defer { lock.unlock() }
        for anchor in meshes {
            guard anchors[anchor.identifier] != nil || anchors.count < 64 else { continue }
            let v = anchor.geometry.vertices
            var points: [SIMD3<Float>] = []
            // At most 300 vertices per anchor; total <20k, independent of session duration.
            for i in stride(from: 0, to: v.count, by: max(1, (v.count + 299) / 300)) {
                let p = v.buffer.contents().advanced(by: v.offset + i * v.stride).assumingMemoryBound(to: SIMD3<Float>.self).pointee
                let w = anchor.transform * SIMD4<Float>(p.x, p.y, p.z, 1)
                points.append(SIMD3<Float>(w.x, w.y, w.z))
            }
            anchors[anchor.identifier] = points
        }
        capturedAt = timestamp
    }

    func remove(_ ids: [UUID]) {
        lock.lock(); defer { lock.unlock() }
        for id in ids { anchors.removeValue(forKey: id) }
    }

    func snapshot() -> (points: [SIMD3<Float>], timestamp: Double, frame: String) {
        lock.lock(); defer { lock.unlock() }
        return (Array(anchors.values.flatMap { $0 }.prefix(maxPoints)), capturedAt, frameID)
    }

    func ply() -> Data {
        let s = snapshot()
        let header = "ply\nformat ascii 1.0\ncomment ARKit local frame \(s.frame) units metres up_axis y\ncomment capture_system_uptime_s \(s.timestamp)\nelement vertex \(s.points.count)\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
        return (header + s.points.map { "\($0.x) \($0.y) \($0.z)" }.joined(separator: "\n") + "\n").data(using: .utf8)!
    }
}

struct LocalScanView: View {
    @EnvironmentObject var model: ResponderModel
    @State private var points: [SIMD3<Float>] = []
    @State private var incidentID = ""
    @State private var status = "Local ARKit frame · metres · Y up · not aligned to the incident map"
    @State private var uploading = false

    var body: some View {
        VStack(spacing: 12) {
            Text("Local LiDAR scan").font(.headline)
            Text(model.ar.meshSupported ? "Scene reconstruction supported" : "Scene reconstruction unavailable on this device").font(.caption)
            ScanPreview(points: points).frame(minHeight: 280)
            Text("\(points.count) bounded mesh samples").font(.caption)
            TextField("Incident ID", text: $incidentID).textFieldStyle(.roundedBorder).autocorrectionDisabled()
            HStack {
                Button("Refresh preview") { points = model.ar.scan.snapshot().points }
                Button("Export to incident") { Task { await upload() } }
                    .disabled(points.isEmpty || incidentID.isEmpty || uploading)
            }
            Text(status).font(.caption).textSelection(.enabled)
        }.padding().task {
            points = model.ar.scan.snapshot().points
            do {
                let (data, _) = try await URLSession.shared.data(from: try endpoint("/api/incidents"))
                let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                incidentID = body?["current"] as? String ?? ""
            } catch { status = "Enter an incident ID and configure the Mac address in Settings." }
        }
    }

    private func endpoint(_ path: String, query: [URLQueryItem] = []) throws -> URL {
        let raw = model.host.trimmingCharacters(in: .whitespacesAndNewlines)
        var c = URLComponents(string: raw.contains("://") ? raw : "http://" + raw)
        guard c?.host != nil else { throw URLError(.badURL) }
        if c?.port == nil { c?.port = 8000 }
        c?.path = path
        c?.queryItems = query + (model.token.isEmpty ? [] : [URLQueryItem(name: "token", value: model.token)])
        guard let url = c?.url else { throw URLError(.badURL) }; return url
    }

    @MainActor private func upload() async {
        uploading = true; defer { uploading = false }
        do {
            let url = try endpoint("/api/incidents/\(incidentID)/scene/assets", query: [
                URLQueryItem(name: "name", value: "iPhone LiDAR \(model.ar.scan.frameID)"),
                URLQueryItem(name: "format", value: "ply"), URLQueryItem(name: "frame_kind", value: "arkit"),
                URLQueryItem(name: "up_axis", value: "y"), URLQueryItem(name: "provenance", value: "live"),
                URLQueryItem(name: "origin", value: "ARKit mesh samples; local metres; unaligned")])
            var req = URLRequest(url: url); req.httpMethod = "POST"
            req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
            req.httpBody = model.ar.scan.ply()
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                status = "Export failed: " + (String(data: data, encoding: .utf8) ?? "server error"); return
            }
            let asset = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            status = "Exported \(asset?["frame"] as? String ?? "local scan"). Open dashboard Scene viewer. No map alignment was added."
        } catch { status = "Export failed: \(error.localizedDescription)" }
    }
}

private struct ScanPreview: UIViewRepresentable {
    var points: [SIMD3<Float>]
    func makeUIView(context: Context) -> SCNView {
        let view = SCNView(); view.allowsCameraControl = true; view.autoenablesDefaultLighting = true
        view.backgroundColor = .black
        return view
    }
    func updateUIView(_ view: SCNView, context: Context) {
        let scene = SCNScene()
        if !points.isEmpty {
            let source = SCNGeometrySource(vertices: points.map { SCNVector3($0.x, $0.y, $0.z) })
            let indices = Array(0..<UInt32(points.count))
            let element = SCNGeometryElement(indices: indices, primitiveType: .point)
            element.pointSize = 4; element.minimumPointScreenSpaceRadius = 2; element.maximumPointScreenSpaceRadius = 5
            let geometry = SCNGeometry(sources: [source], elements: [element])
            geometry.firstMaterial?.diffuse.contents = UIColor.cyan
            scene.rootNode.addChildNode(SCNNode(geometry: geometry))
        }
        view.scene = scene
    }
}
