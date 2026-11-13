import Foundation
import XCTest

/// A run of every test in a shuffled order, for finding a test that needs another to have run first or that leaves
/// state behind. With ALBEDIAN_SWIFT_ORDER_SEED set, this suite holds every test of every class in `classes`, once
/// each, in an order shuffled by that seed (SplitMix64); it runs first in the process (its name sorts first), and the
/// classes then run again in XCTest's own order. Unset, it holds nothing. (`xctest -XCTest` takes a list of tests but
/// runs them sorted, and overriding a class's own suite stops `swift test --filter` from finding its tests, so the
/// order is set by this one extra suite.)
final class AAShuffledOrder: XCTestCase {
    /// Every test class. The order runner checks that this suite executes as many tests as a plain run, so a class
    /// missing here fails it.
    static let classes: [XCTestCase.Type] = [
        ContractBoundaryTests.self, ContractEdgeTableTests.self, ContractPropertyTests.self, InstantAndHashTests.self,
        JSONTextTests.self, PrivacyScanTests.self, RecordBuilderPropertyTests.self, RecordBuilderTests.self,
        SharedFixtureTests.self, UploadClientTests.self, RecordBuilderEdgeTests.self, JSONEdgeTests.self,
        LaptopAddressEdgeTests.self, InstantEdgeTests.self, CalendarDayTests.self, RequestGateTests.self, HTTPReaderTests.self,
        SensorMappingTests.self, MotionRingTests.self, SensorRecorderTests.self,
    ]

    override class var defaultTestSuite: XCTestSuite {
        guard let text = ProcessInfo.processInfo.environment["ALBEDIAN_SWIFT_ORDER_SEED"], let seed = UInt64(text) else {
            return XCTestSuite(name: "AAShuffledOrder (ALBEDIAN_SWIFT_ORDER_SEED not set)")
        }
        var tests = classes.flatMap { XCTestSuite(forTestCaseClass: $0).tests }
        var generator = SeededGenerator(seed: seed)
        tests.shuffle(using: &generator)
        let suite = XCTestSuite(name: "AAShuffledOrder seed \(seed)")
        for test in tests { suite.addTest(test) }
        return suite
    }
}
