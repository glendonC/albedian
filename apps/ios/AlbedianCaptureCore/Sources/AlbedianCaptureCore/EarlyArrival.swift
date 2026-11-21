/// When each early ring comes in on the iPhone's wait (MOTION-READY-iphone section 6): brightest first, a planet with
/// no magnitude ahead of the stars, 67 ms apart with every start within 0.33 s, each on frame in. The first set comes in
/// from 0; on a swap to a better early pose the rings only in the new set come in once the shared rings' travel (frame
/// in, 0.4 s) is over, never during it. Under Reduce Motion the new set dissolves in together, from 0.
public enum EarlyArrival {
    public static let stepSeconds = 0.067
    public static let spanSeconds = 0.33

    public struct Ring: Equatable, Sendable {
        public let key: String
        public let mag: Double?
        public init(key: String, mag: Double?) {
            self.key = key
            self.mag = mag
        }
    }

    /// The wait before each arriving ring starts, by key: the rings in `now` that `shown` has not drawn yet.
    public static func delays(shown: Set<String>, now: [Ring], reduceMotion: Bool) -> [String: Double] {
        let arriving = now.enumerated().filter { !shown.contains($0.element.key) }.sorted { a, b in
            let (ma, mb) = (a.element.mag ?? -1, b.element.mag ?? -1)
            return ma != mb ? ma < mb : a.offset < b.offset
        }.map(\.element.key)
        if reduceMotion { return Dictionary(uniqueKeysWithValues: arriving.map { ($0, 0) }) }
        let start = shown.isEmpty ? 0 : MotionStyle.frameIn.seconds
        let step = arriving.count > 1 ? min(stepSeconds, spanSeconds / Double(arriving.count - 1)) : 0
        return Dictionary(uniqueKeysWithValues: arriving.enumerated().map { ($0.element, start + Double($0.offset) * step) })
    }
}
