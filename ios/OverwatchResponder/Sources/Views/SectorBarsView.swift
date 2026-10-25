import SwiftUI

/// LiDAR obstacle sectors, left → right. Three visibly different states:
/// * unknown (not observed: outside FOV / no valid depth) — dashed grey outline, "?";
/// * clear (observed, nothing in the height band) — short green bar, "clear";
/// * obstacle — bar height grows as it gets closer, red < 1.2 m, orange < 2 m, yellow beyond.
struct SectorBarsView: View {
    let sectors: [DepthSector]
    let lidarSupported: Bool

    private static let order = ["left", "front_left", "front", "front_right", "right"]
    private static let labels = ["L", "FL", "F", "FR", "R"]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("LIDAR")
                    .font(.caption.weight(.heavy))
                    .tracking(1.2)
                    .foregroundStyle(Color.white.opacity(0.6))
                Spacer()
                if !lidarSupported {
                    Text("No LiDAR on this device")
                        .font(.caption)
                        .foregroundStyle(Color.orange)
                }
            }
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(Array(Self.order.enumerated()), id: \.offset) { index, name in
                    SectorBar(sector: sectors.first(where: { $0.sector == name }) ?? DepthSector.unknown(name),
                              label: Self.labels[index])
                }
            }
        }
    }
}

private struct SectorBar: View {
    let sector: DepthSector
    let label: String

    private var isUnknown: Bool { sector.nearest_m == nil && sector.confidence == "unknown" }
    private var isClear: Bool { sector.nearest_m == nil && sector.confidence != "unknown" }

    private var fill: CGFloat {
        if let d = sector.nearest_m {
            return CGFloat(min(1.0, max(0.12, 1.0 - d / 5.0)))
        }
        return isClear ? 0.1 : 0.0
    }

    private var color: Color {
        if isUnknown { return .clear }
        if isClear { return .green }
        guard let d = sector.nearest_m else { return .gray }
        if d < 1.2 { return .red }
        if d < 2.0 { return .orange }
        return .yellow
    }

    private var valueText: String {
        if isUnknown { return "?" }
        if let d = sector.nearest_m { return String(format: "%.1f m", d) }
        return "clear"
    }

    var body: some View {
        VStack(spacing: 4) {
            GeometryReader { geo in
                ZStack(alignment: .bottom) {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.white.opacity(0.06))
                    if isUnknown {
                        RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                            .foregroundStyle(Color.gray)
                    } else {
                        RoundedRectangle(cornerRadius: 6)
                            .fill(color)
                            .frame(height: max(6, geo.size.height * fill))
                    }
                }
            }
            Text(valueText)
                .font(.caption.weight(.semibold).monospacedDigit())
                .foregroundStyle(isUnknown ? Color.gray : Color.white)
            Text(label)
                .font(.caption2.weight(.bold))
                .foregroundStyle(Color.white.opacity(0.5))
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label) \(valueText)")
    }
}
