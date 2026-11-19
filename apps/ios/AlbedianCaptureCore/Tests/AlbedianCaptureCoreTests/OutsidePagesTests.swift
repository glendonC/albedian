import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// mobile-4b (the lead's answers of 2026-11-18): the pages the app hands to the system's Safari view, and no other.
/// The reveal of one stored upload on the paired laptop's web app (`?upload=<sha256>`, `apps/web/src/services/
/// openUpload.ts`), and the two Credits rows that link out (UIUX-credit-APPROVED), from a fixed list.
final class OutsidePagesTests: XCTestCase {
    private let sha = String(repeating: "ab", count: 32)

    func testTheRevealIsThePairedAddressWithTheUploadsHash() throws {
        let laptop = try XCTUnwrap(LaptopAddress("http://172.20.10.3:5173"))
        XCTAssertEqual(RevealPage.address(of: sha, on: laptop)?.absoluteString, "http://172.20.10.3:5173/?upload=\(sha)")
        let v6 = try XCTUnwrap(LaptopAddress("http://[fe80::1%25en0]:5173"))
        XCTAssertEqual(RevealPage.address(of: sha, on: v6)?.absoluteString, "http://[fe80::1%25en0]:5173/?upload=\(sha)")
    }

    func testNoRevealForAHashTheWebWouldIgnore() throws {
        let laptop = try XCTUnwrap(LaptopAddress("http://172.20.10.3:5173"))
        for bad in [sha.uppercased(), String(sha.dropLast()), sha + "0", String(repeating: "0", count: 64), "", "\(sha)&x=1"] {
            XCTAssertNil(RevealPage.address(of: bad, on: laptop), bad)
        }
    }

    /// The two credit pages are the sources' own pages as the repository records them: ESO's page for eso0932a
    /// (UIUX-credit-APPROVED), and the archive the repository's Hipparcos and Gaia records name
    /// (`apps/web/public/catalog/stars/gaia-dr3/PROVENANCE.json`).
    func testTheCreditPagesAreTheFixedTwo() {
        XCTAssertEqual(OutsidePage.allCases.map(\.address.absoluteString),
                       ["https://www.eso.org/public/images/eso0932a/", "https://gea.esac.esa.int/archive/"])
    }

    func testTheRecordsStillNameTheseCreditPages() throws {
        let gaia = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/catalog/stars/gaia-dr3/PROVENANCE.json"))
        let eso = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/textures/PROVENANCE.json"))
        XCTAssertTrue(String(decoding: gaia, as: UTF8.self).contains("\"https://gea.esac.esa.int/archive/\""))
        XCTAssertTrue(String(decoding: eso, as: UTF8.self).contains("\"https://www.eso.org/public/images/eso0932a/\""))
    }

    /// The one gate before the Safari view opens: the fixed credit pages, or the reveal of a contract hash on the paired
    /// laptop exactly as `RevealPage` writes it; nothing else, and no reveal without a paired laptop.
    func testTheSafariViewOpensOnlyThePagesAllowed() throws {
        let laptop = try XCTUnwrap(LaptopAddress("http://172.20.10.3:5173"))
        let reveal = try XCTUnwrap(RevealPage.address(of: sha, on: laptop))
        XCTAssertTrue(SafariViewGate.permits(reveal, paired: laptop))
        XCTAssertFalse(SafariViewGate.permits(reveal, paired: nil))
        XCTAssertFalse(SafariViewGate.permits(reveal, paired: try XCTUnwrap(LaptopAddress("http://172.20.10.4:5173"))))
        for page in OutsidePage.allCases {
            XCTAssertTrue(SafariViewGate.permits(page.address, paired: nil))
            XCTAssertTrue(SafariViewGate.permits(page.address, paired: laptop))
        }
        for text in ["http://172.20.10.3:5173/?upload=\(sha)&x=1", "http://172.20.10.3:5173/x?upload=\(sha)", "http://172.20.10.3:5173/",
                     "https://www.eso.org/", "https://www.eso.org/public/images/eso0932a/?x", "https://gea.esac.esa.int/archive/x",
                     "http://172.20.10.3:5173/?upload=\(sha.uppercased())", "http://172.20.10.3:5173/?upload=\(sha)#x",
                     "http://172.20.10.3:5174/?upload=\(sha)", "https://172.20.10.3:5173/?upload=\(sha)"] {
            let url = try XCTUnwrap(URL(string: text), text)
            XCTAssertFalse(SafariViewGate.permits(url, paired: laptop), text)
        }
    }
}
