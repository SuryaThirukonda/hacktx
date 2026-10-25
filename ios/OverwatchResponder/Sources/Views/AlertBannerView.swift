import SwiftUI

enum SeverityStyle {
    static func background(_ severity: String) -> Color {
        switch severity {
        case "critical": return Color(red: 0.86, green: 0.08, blue: 0.10)
        case "local_proximity": return Color(red: 0.96, green: 0.45, blue: 0.02)
        case "warning": return Color(red: 0.98, green: 0.76, blue: 0.05)
        case "sensor_fault": return Color(red: 0.52, green: 0.30, blue: 0.86)
        case "attention": return Color(red: 0.12, green: 0.45, blue: 0.86)
        default: return Color(white: 0.28)
        }
    }

    static func foreground(_ severity: String) -> Color {
        severity == "warning" ? .black : .white
    }

    static func label(_ severity: String) -> String {
        switch severity {
        case "local_proximity": return "PROXIMITY"
        case "sensor_fault": return "SENSOR FAULT"
        default: return severity.uppercased()
        }
    }
}

/// Large severity-coloured banner for the current Mac alert (or the on-device proximity rule).
struct AlertBannerView: View {
    let alert: WireAlert?
    let acked: Bool
    let localProximityText: String?

    var body: some View {
        Group {
            if let alert = alert {
                alertBody(alert)
            } else if let local = localProximityText {
                banner(severity: "local_proximity", title: local, subtitle: "Local rule · works without the Mac",
                       tag: "PROXIMITY")
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text("No active alerts")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Color.white.opacity(0.7))
                    Text("Voice and haptics will announce anything new")
                        .font(.footnote)
                        .foregroundStyle(Color.white.opacity(0.45))
                }
                .frame(maxWidth: .infinity, minHeight: 92, alignment: .leading)
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 16).fill(Color.white.opacity(0.07)))
            }
        }
    }

    private func alertBody(_ alert: WireAlert) -> some View {
        var parts: [String] = []
        if let clock = alert.clock { parts.append("\(clock) o'clock") }
        if let range = alert.range_m { parts.append(String(format: "%.0f m", range)) }
        if let closing = alert.closing_mps, closing > 0.05 { parts.append(String(format: "closing %.1f m/s", closing)) }
        if let ttc = alert.ttc_s { parts.append(String(format: "TTC %.1f s", ttc)) }
        if alert.can_read_as_confirmed_location == false && (alert.kind == "CLOSING_FAST" || alert.kind == "APPROACH") {
            parts.append("unconfirmed geometry")
        }
        let tag = acked ? "\(SeverityStyle.label(alert.severity)) · ACKED" : SeverityStyle.label(alert.severity)
        return banner(severity: alert.severity, title: alert.displayText,
                      subtitle: parts.isEmpty ? alert.kind : parts.joined(separator: " · "), tag: tag)
            .opacity(acked ? 0.6 : 1.0)
    }

    private func banner(severity: String, title: String, subtitle: String, tag: String) -> some View {
        let fg = SeverityStyle.foreground(severity)
        return VStack(alignment: .leading, spacing: 6) {
            Text(tag)
                .font(.caption.weight(.heavy))
                .tracking(1.2)
                .foregroundStyle(fg.opacity(0.85))
            Text(title)
                .font(.title2.weight(.bold))
                .foregroundStyle(fg)
                .lineLimit(3)
                .minimumScaleFactor(0.6)
                .fixedSize(horizontal: false, vertical: true)
            Text(subtitle)
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(fg.opacity(0.85))
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, minHeight: 92, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 16).fill(SeverityStyle.background(severity)))
        .accessibilityElement(children: .combine)
    }
}
