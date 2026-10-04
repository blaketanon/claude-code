import CoreLocation
import Foundation

enum Timecode {
    /// Index of the frame on screen at `seconds`. The small epsilon absorbs
    /// floating-point error when a time lands exactly on a frame boundary.
    static func frameIndex(at seconds: Double, fps: Double) -> Int {
        max(0, Int((seconds * fps + 0.001).rounded(.down)))
    }

    /// Splits a time into a main part and a highlighted tail, e.g. ("00:00:02", ":14").
    static func parts(seconds: Double, fps: Double, format: TimeFormat) -> (main: String, tail: String) {
        switch format {
        case .frames:
            let frame = frameIndex(at: seconds, fps: fps)
            let whole = Int(Double(frame) / fps)
            let nominal = max(1, Int(fps.rounded()))
            let ff = min(nominal - 1, max(0, frame - Int((Double(whole) * fps).rounded())))
            return (
                String(format: "%02d:%02d:%02d", whole / 3600, (whole / 60) % 60, whole % 60),
                String(format: ":%02d", ff)
            )
        case .milliseconds:
            let ms = Int((max(0, seconds) * 1000).rounded())
            let s = ms / 1000
            return (String(format: "%02d:%02d", s / 60, s % 60), String(format: ".%03d", ms % 1000))
        }
    }

    /// File-name friendly stamp, e.g. "00-00-02-14".
    static func fileStamp(seconds: Double, fps: Double) -> String {
        let p = parts(seconds: seconds, fps: fps, format: .frames)
        return (p.main + p.tail).replacingOccurrences(of: ":", with: "-")
    }

    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return s >= 3600
            ? String(format: "%d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60)
            : String(format: "%d:%02d", s / 60, s % 60)
    }

    /// Snaps measured rates such as 29.968 to the broadcast rate they approximate.
    static func niceRate(_ fps: Double) -> Double {
        for standard in [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 120, 240] where abs(fps - standard) < 0.01 * standard {
            return standard
        }
        return (fps * 100).rounded() / 100
    }

    static func rateLabel(_ fps: Double) -> String {
        let r = niceRate(fps)
        return r == r.rounded() ? String(Int(r)) : String(format: "%.2f", r)
    }
}

/// ISO 6709 location strings as stored by QuickTime, e.g. "+37.8199-122.4783+010.000/".
enum ISO6709 {
    static func parse(_ string: String) -> CLLocation? {
        let pattern = #/([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?/#
        guard let match = string.firstMatch(of: pattern),
              let lat = Double(match.1), let lon = Double(match.2),
              abs(lat) <= 90, abs(lon) <= 180, lat != 0 || lon != 0
        else { return nil }
        let altitude = match.3.flatMap { Double($0) }
        return CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon),
            altitude: altitude ?? 0,
            horizontalAccuracy: 0,
            verticalAccuracy: altitude == nil ? -1 : 0,
            timestamp: Date()
        )
    }
}

/// QuickTime creation dates, e.g. "2026-09-12T11:42:07-0700". Keeps the UTC offset,
/// which `AVMetadataItem.dateValue` throws away.
enum QuickTimeDate {
    static func parse(_ string: String) -> (date: Date, timeZone: TimeZone?)? {
        let pattern = #/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?/#
        guard let m = string.firstMatch(of: pattern) else { return nil }

        var timeZone: TimeZone?
        if let zone = m.7 {
            if zone == "Z" {
                timeZone = TimeZone(secondsFromGMT: 0)
            } else {
                let digits = zone.dropFirst().filter(\.isNumber)
                let hours = Int(digits.prefix(2)) ?? 0
                let minutes = Int(digits.suffix(2)) ?? 0
                let sign = zone.first == "-" ? -1 : 1
                timeZone = TimeZone(secondsFromGMT: sign * (hours * 3600 + minutes * 60))
            }
        }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone ?? .current
        var parts = DateComponents()
        parts.year = Int(m.1)
        parts.month = Int(m.2)
        parts.day = Int(m.3)
        parts.hour = Int(m.4)
        parts.minute = Int(m.5)
        parts.second = Int(m.6)
        guard let date = calendar.date(from: parts) else { return nil }
        return (date, timeZone)
    }
}
