import AlbedianCaptureCore
import XCTest

final class EarlyArrivalTests: XCTestCase {
    private func ring(_ key: String, _ mag: Double?) -> EarlyArrival.Ring { EarlyArrival.Ring(key: key, mag: mag) }

    func testTheFirstSetComesInBrightestFirstSixtySevenMillisecondsApart() {
        let delays = EarlyArrival.delays(shown: [], now: [ring("c", 2.9), ring("a", 0.4), ring("b", 1.9)], reduceMotion: false)
        XCTAssertEqual(delays.count, 3)
        XCTAssertEqual(delays["a"]!, 0, accuracy: 1e-9)
        XCTAssertEqual(delays["b"]!, 0.067, accuracy: 1e-9)
        XCTAssertEqual(delays["c"]!, 0.134, accuracy: 1e-9)
    }

    func testAPlanetWithNoMagnitudeComesAheadOfTheStars() {
        let delays = EarlyArrival.delays(shown: [], now: [ring("Sirius", -1.46), ring("Jupiter", nil), ring("Rigel", 0.13)],
                                         reduceMotion: false)
        XCTAssertEqual(delays["Sirius"]!, 0, accuracy: 1e-9)
        XCTAssertEqual(delays["Jupiter"]!, 0.067, accuracy: 1e-9)
        XCTAssertEqual(delays["Rigel"]!, 0.134, accuracy: 1e-9)
    }

    func testManyRingsAllStartWithinTheSpan() {
        let now = (0..<11).map { ring("s\($0)", Double($0) * 0.2) }
        let delays = EarlyArrival.delays(shown: [], now: now, reduceMotion: false)
        XCTAssertEqual(delays["s0"]!, 0, accuracy: 1e-9)
        XCTAssertEqual(delays["s1"]!, 0.033, accuracy: 1e-9)
        XCTAssertEqual(delays["s10"]!, 0.33, accuracy: 1e-9)
        XCTAssertLessThanOrEqual(delays.values.max()!, EarlyArrival.spanSeconds + 1e-9)
    }

    func testOnASwapOnlyTheNewRingsArriveAndOnlyOnceTheTravelIsOver() {
        let delays = EarlyArrival.delays(shown: ["a", "b", "gone"], now: [ring("a", 0.4), ring("d", 2.5), ring("b", 1.9), ring("c", 2.1)],
                                         reduceMotion: false)
        XCTAssertEqual(Set(delays.keys), ["c", "d"])
        XCTAssertEqual(delays["c"]!, 0.4, accuracy: 1e-9)
        XCTAssertEqual(delays["d"]!, 0.467, accuracy: 1e-9)
    }

    func testOneRingStartsAtOnceAndASwapWithNothingNewHasNoArrivals() {
        XCTAssertEqual(EarlyArrival.delays(shown: [], now: [ring("a", 1)], reduceMotion: false), ["a": 0])
        XCTAssertEqual(EarlyArrival.delays(shown: ["a"], now: [ring("a", 1)], reduceMotion: false), [:])
    }

    func testUnderReduceMotionTheNewSetComesInTogetherAtOnce() {
        let delays = EarlyArrival.delays(shown: ["phone a"], now: [ring("laptop a", 0.4), ring("laptop b", 1.9)], reduceMotion: true)
        XCTAssertEqual(delays, ["laptop a": 0, "laptop b": 0])
    }
}
