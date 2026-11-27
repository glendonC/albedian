import AlbedianCaptureCore
import Foundation
import XCTest

/// The Credits screen carries every line a credited source's terms require, in the records' own words (AUDIT-C's ask of
/// 19 Nov, after the web's credits-2).
final class CreditsWordsTests: XCTestCase {
    private func sources(_ path: String) throws -> [[String: Any]] {
        let data = try Data(contentsOf: repositoryRoot.appendingPathComponent(path))
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        return try XCTUnwrap(root["sources"] as? [[String: Any]])
    }

    private func source(_ id: String, in path: String) throws -> [String: Any] {
        try XCTUnwrap(try sources(path).first { $0["id"] as? String == id }, id)
    }

    private func row(_ label: String) throws -> CreditsWords.Row {
        try XCTUnwrap(CreditsWords.rows.first { $0.label == label }, label)
    }

    private let stars = "apps/web/public/catalog/stars/gaia-dr3/PROVENANCE.json"

    func testTheApprovedRowsStayInTheirOrderAndKeepTheirOwnLinesFirst() throws {
        XCTAssertEqual(CreditsWords.rows.map(\.label), ["Milky Way photograph", "Star catalogs", "Type", "Icons"])
        XCTAssertEqual(Array(try row("Milky Way photograph").lines.prefix(3)), ["ESO/S. Brunier", "The Milky Way panorama, eso0932a", "CC BY 4.0"])
        XCTAssertEqual(Array(try row("Star catalogs").lines.prefix(2)), ["ESA, Hipparcos 2007", "ESA/Gaia/DPAC, Gaia DR3"])
        XCTAssertEqual(try row("Type").lines, ["Geist, SIL Open Font License"])
        XCTAssertEqual(try row("Icons").lines, ["Phosphor Icons, MIT License"])
        XCTAssertEqual(CreditsWords.rows.map(\.page), [.milkyWayPhotograph, .starCatalogs, nil, nil])
    }

    func testThePanoramasChangeIsIndicatedAsItsTermsAskWordedFromTheCropAndTheShade() throws {
        let eso = try source("eso0932a", in: "apps/ios/notices/eso/PROVENANCE.json")
        let conditions = try XCTUnwrap((eso["license"] as? [String: Any])?["conditions"] as? String)
        XCTAssertTrue(conditions.contains("modifications must be indicated"))
        XCTAssertEqual(CreditsWords.milkyWayChange(),
                       "On the home a part of it is shown, 23 percent of its width at the middle and all of its height, reprojected so its stars stay round toward the top and bottom, and darkened toward the bottom, up to 90 percent black.")
        XCTAssertEqual(try row("Milky Way photograph").lines.last, CreditsWords.milkyWayChange())
        // The crop is the one the record's recipe made
        let record = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/notices/eso/PROVENANCE.json"), encoding: .utf8)
        XCTAssertTrue(record.contains("the full height and \(HomeBackdrop.cropColumns) of the \(HomeBackdrop.panoramaColumns) columns"))
    }

    func testTheStarCatalogsCarryTheirLicenceTheGaiaAcknowledgementAndTheStarNamesCitation() throws {
        let lines = try row("Star catalogs").lines
        for id in ["gaia-dr3", "hipparcos-2007"] {
            XCTAssertEqual((try source(id, in: stars)["license"] as? [String: Any])?["spdx"] as? String, "CC-BY-NC-3.0-IGO", id)
        }
        XCTAssertEqual(lines.filter { $0 == "CC BY-NC 3.0 IGO" }.count, 1)
        // Every acknowledgement the catalog records hold, word for word, once
        let acknowledgements = Set(try sources(stars).compactMap { $0["acknowledgement"] as? String })
        XCTAssertEqual(acknowledgements, [CreditsWords.gaiaAcknowledgement])
        XCTAssertEqual(lines.filter { $0 == CreditsWords.gaiaAcknowledgement }.count, 1)
        // The star names the overlay draws: their terms ask to cite, and the citation, credit and licence are the record's
        let names = try source("iau-csn", in: stars)
        let licence = try XCTUnwrap(names["license"] as? [String: Any])
        XCTAssertTrue((licence["conditions"] as? String ?? "").contains("cite"))
        XCTAssertEqual(CreditsWords.starNamesCredit, names["credit"] as? String)
        XCTAssertEqual(CreditsWords.starNamesCitation, names["citation"] as? String)
        XCTAssertEqual(CreditsWords.starNamesLicence, licence["name"] as? String)
        XCTAssertEqual(Array(lines.suffix(3)), [CreditsWords.starNamesCredit, CreditsWords.starNamesCitation, CreditsWords.starNamesLicence])
    }

    /// credits-esa-words-m (ASSETS' note to MOBILE after credits-esa-form-one, the lead's 00:26 ruling): each catalog is
    /// credited as its record credits it, Hipparcos "ESA" as ESA's catalogue page asks, and every star source whose terms
    /// ask to cite shows its record's citation word for word, once, as the web's Credits does; one that does not ask
    /// shows none.
    func testEachCatalogIsCreditedAndCitedAsItsRecordSays() throws {
        let lines = try row("Star catalogs").lines
        let hipparcos = try source("hipparcos-2007", in: stars)
        let gaia = try source("gaia-dr3", in: stars)
        XCTAssertEqual(lines[0], "\(try XCTUnwrap(hipparcos["credit"] as? String)), Hipparcos 2007")
        XCTAssertEqual(lines[1], "\(try XCTUnwrap(gaia["credit"] as? String)), Gaia DR3")
        for source in try sources(stars) {
            let id = source["id"] as? String ?? "?"
            let conditions = (source["license"] as? [String: Any])?["conditions"] as? String ?? ""
            let citation = try XCTUnwrap(source["citation"] as? String, id)
            let asks = conditions.range(of: "\\bcite\\b", options: [.regularExpression, .caseInsensitive]) != nil
            XCTAssertEqual(lines.filter { $0 == citation }.count, asks ? 1 : 0, id)
        }
        XCTAssertEqual(CreditsWords.gaiaCitation, gaia["citation"] as? String)
        // Gaia's citation follows its acknowledgement, as the web's block orders acknowledgements before citations
        let acknowledgement = try XCTUnwrap(lines.firstIndex(of: CreditsWords.gaiaAcknowledgement))
        XCTAssertEqual(lines[acknowledgement + 1], CreditsWords.gaiaCitation)
    }
}
