import Foundation

/// Minimal H.264 Annex-B helpers for forwarding the DJI raw video feed to the Mac (binary kind 0x02).
enum H264Scan {
    struct NAL {
        let start: Int      // offset of the start code (00 00 01 or 00 00 00 01)
        let payload: Int    // offset of the NAL header byte
        let type: UInt8     // nal_unit_type: 5 = IDR, 7 = SPS, 8 = PPS, 9 = AUD
    }

    /// Every NAL unit start in `bytes`, in order.
    static func nalUnits(in bytes: [UInt8]) -> [NAL] {
        var out: [NAL] = []
        let n = bytes.count
        var i = 0
        while i + 3 < n {
            if bytes[i] == 0 && bytes[i + 1] == 0 {
                if bytes[i + 2] == 1 {
                    out.append(NAL(start: i, payload: i + 3, type: bytes[i + 3] & 0x1F))
                    i += 3
                    continue
                }
                if bytes[i + 2] == 0 && i + 4 < n && bytes[i + 3] == 1 {
                    out.append(NAL(start: i, payload: i + 4, type: bytes[i + 4] & 0x1F))
                    i += 4
                    continue
                }
            }
            i += 1
        }
        return out
    }
}

/// Streaming removal of the `00 00 00 01 09 10` access-unit delimiters that the DJI Mini 2 (and Lightbridge 2)
/// raw feed carries. DJIWidget's DJIVideoPreviewer strips exactly these (DJILB2AUDRemoveParser, used for
/// H264EncoderType_DJIMini2) before decoding; we do the same so a stock FFmpeg/PyAV parser on the Mac sees clean
/// access units. Handles a delimiter split across two chunks by holding back at most 5 bytes.
final class AUDStripper {
    private static let pattern: [UInt8] = [0x00, 0x00, 0x00, 0x01, 0x09, 0x10]
    private var carry: [UInt8] = []
    private(set) var removed = 0

    func reset() {
        carry.removeAll()
    }

    func process(_ chunk: Data) -> Data {
        var input = carry
        input.append(contentsOf: chunk)
        carry.removeAll(keepingCapacity: true)
        let p = Self.pattern
        let n = input.count
        var out = Data(capacity: n)
        var runStart = 0
        var i = 0
        while i + 6 <= n {
            if input[i] == p[0] && input[i + 1] == p[1] && input[i + 2] == p[2] && input[i + 3] == p[3]
                && input[i + 4] == p[4] && input[i + 5] == p[5] {
                if i > runStart {
                    out.append(contentsOf: input[runStart..<i])
                }
                removed += 1
                i += 6
                runStart = i
                continue
            }
            i += 1
        }
        // Hold back the longest tail that could be the beginning of a delimiter completed by the next chunk.
        var keep = 0
        let maxKeep = min(p.count - 1, n - runStart)
        if maxKeep > 0 {
            for k in stride(from: maxKeep, through: 1, by: -1) where Array(input[(n - k)..<n]) == Array(p[0..<k]) {
                keep = k
                break
            }
        }
        if n - keep > runStart {
            out.append(contentsOf: input[runStart..<(n - keep)])
        }
        if keep > 0 {
            carry.append(contentsOf: input[(n - keep)..<n])
        }
        return out
    }
}
