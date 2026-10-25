import SwiftUI

/// Responder-centred compass: 12 o'clock = the way the responder faces. The arrow points at the subject using
/// `responder_view.kin.bearing_rel_deg` (clockwise positive); range / closing speed / TTC underneath.
struct CompassRoseView: View {
    let kin: RelativeKinematics?
    let subjectState: String?
    let verified: Bool
    let stale: Bool

    private var bearing: Double? {
        guard let k = kin, k.isValid, !stale else { return nil }
        return k.bearing_rel_deg
    }

    private var arrowColor: Color {
        guard let k = kin else { return .gray }
        if let ttc = k.ttc_s, ttc < 3.0 { return .red }
        if let closing = k.closing_mps, closing > 1.0 { return .orange }
        return .cyan
    }

    var body: some View {
        VStack(spacing: 8) {
            GeometryReader { geo in
                let size = min(geo.size.width, geo.size.height)
                ZStack {
                    Circle().fill(Color.white.opacity(0.04))
                    Circle().stroke(Color.white.opacity(0.28), lineWidth: 2)
                    ForEach(0..<12, id: \.self) { i in
                        Capsule()
                            .fill(i == 0 ? Color.white : Color.white.opacity(0.45))
                            .frame(width: i % 3 == 0 ? 4 : 2, height: i % 3 == 0 ? 16 : 9)
                            .offset(y: -size / 2 + 12)
                            .rotationEffect(.degrees(Double(i) * 30))
                    }
                    Text("12")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Color.white.opacity(0.8))
                        .offset(y: -size / 2 + 32)
                    Image(systemName: "figure.stand")
                        .font(.system(size: max(12, size * 0.1)))
                        .foregroundStyle(Color.white.opacity(0.75))
                    if let b = bearing {
                        SubjectArrow()
                            .fill(arrowColor)
                            .frame(width: size * 0.16, height: size * 0.3)
                            .offset(y: -size * 0.28)
                            .rotationEffect(.degrees(b))
                    } else {
                        Text(noBearingText)
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(Color.white.opacity(0.55))
                            .frame(width: size * 0.7)
                            .offset(y: size * 0.2)
                    }
                }
                .frame(width: size, height: size)
                .position(x: geo.size.width / 2, y: geo.size.height / 2)
            }
            readout
        }
    }

    private var noBearingText: String {
        if stale { return "No data from the Mac" }
        if let reason = kin?.reason, !(kin?.isValid ?? false) { return reason }
        return "Subject bearing unknown"
    }

    private var readout: some View {
        HStack(spacing: 18) {
            metric("RANGE", value: kin?.isValid == true ? kin?.range_m.map { String(format: "%.0f m", $0) } : nil)
            metric("CLOSING", value: kin?.isValid == true ? kin?.closing_mps.map { String(format: "%.1f m/s", $0) } : nil)
            metric("TTC", value: kin?.isValid == true ? kin?.ttc_s.map { String(format: "%.1f s", $0) } : nil)
            metric("CLOCK", value: kin?.isValid == true ? kin?.clock.map { "\($0)" } : nil)
        }
        .overlay(alignment: .topTrailing) {
            if let state = subjectState, state != "UNLOCKED" {
                Text(verified ? state : "\(state) · UNVERIFIED")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Color.white.opacity(0.6))
                    .offset(y: -16)
            }
        }
    }

    private func metric(_ title: String, value: String?) -> some View {
        VStack(spacing: 2) {
            Text(value ?? "—")
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(Color.white)
            Text(title)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Color.white.opacity(0.5))
        }
        .frame(minWidth: 60)
    }
}

/// Arrow pointing "up" (towards 12 o'clock) before rotation.
struct SubjectArrow: Shape {
    func path(in rect: CGRect) -> Path {
        let w = rect.width
        let h = rect.height
        let shoulder = rect.minY + h * 0.42
        var p = Path()
        p.move(to: CGPoint(x: rect.midX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: shoulder))
        p.addLine(to: CGPoint(x: rect.midX + w * 0.17, y: shoulder))
        p.addLine(to: CGPoint(x: rect.midX + w * 0.17, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.midX - w * 0.17, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.midX - w * 0.17, y: shoulder))
        p.addLine(to: CGPoint(x: rect.minX, y: shoulder))
        p.closeSubpath()
        return p
    }
}
