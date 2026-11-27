import Foundation

/// The Credits screen's rows (credit, UIUX-credit-APPROVED), with every line a credited source's terms require, as the
/// web's Credits page carries them (credits-2, AUDIT-C's ask of 19 Nov): a change where the terms ask that changes be
/// indicated, each licence by name, an acknowledgement a record holds, and a citation where the terms ask to cite. The
/// words of each are the records' own (`apps/web/public/catalog/stars/gaia-dr3/PROVENANCE.json`,
/// `apps/ios/notices/eso/PROVENANCE.json`); the tests hold them to the records.
public enum CreditsWords {
    public struct Row: Equatable, Sendable {
        public let label: String
        public let lines: [String]
        public let page: OutsidePage?
    }

    /// The Gaia DR3 record's acknowledgement, word for word. Shown, never opened.
    public static let gaiaAcknowledgement = "This work has made use of data from the European Space Agency (ESA) mission Gaia (https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium). Funding for the DPAC has been provided by national institutions, in particular the institutions participating in the Gaia Multilateral Agreement."

    /// The Gaia DR3 record's citation, word for word: ESA asks research using Gaia DR3 to cite the mission paper and the
    /// release paper (credits-esa-words-m, after ASSETS' credits-esa-form-one).
    public static let gaiaCitation = "Gaia Collaboration, Prusti, T., et al. 2016, A&A 595, A1; Gaia Collaboration, Vallenari, A., et al. 2023, A&A 674, A1"

    /// The IAU catalog of star names: its terms ask to mention the source and cite it; the aim overlay draws its names.
    public static let starNamesCredit = "IAU Working Group on Star Names (WGSN)"
    public static let starNamesCitation = "IAU Working Group on Star Names, IAU Catalog of Star Names"
    /// The licence as the IAU record names it (the IAU gives no version).
    public static let starNamesLicence = "Creative Commons Attribution, as the IAU states it, with no version given"

    /// What the app changes in the Milky Way panorama, whose terms ask that modifications be indicated: the part it
    /// ships (`HomeBackdrop`), each row reprojected so the stars stay round toward the top and bottom
    /// (home-edge-and-name-one), and the shade the home draws over it.
    public static func milkyWayChange(_ backdrop: HomeBackdrop.Type = HomeBackdrop.self) -> String {
        let width = Int((Double(backdrop.cropColumns) / Double(backdrop.panoramaColumns) * 100).rounded())
        let darkest = Int(((backdrop.shade.map(\.opacity).max() ?? 0) * 100).rounded())
        return "On the home a part of it is shown, \(width) percent of its width at the middle and all of its height, "
            + "reprojected so its stars stay round toward the top and bottom, and darkened toward the bottom, up to \(darkest) percent black."
    }

    public static let rows: [Row] = [
        Row(label: "Milky Way photograph",
            lines: ["ESO/S. Brunier", "The Milky Way panorama, eso0932a", "CC BY 4.0", milkyWayChange()], page: .milkyWayPhotograph),
        Row(label: "Star catalogs",
            lines: ["ESA, Hipparcos 2007", "ESA/Gaia/DPAC, Gaia DR3", "CC BY-NC 3.0 IGO", gaiaAcknowledgement, gaiaCitation,
                    starNamesCredit, starNamesCitation, starNamesLicence],
            page: .starCatalogs),
        Row(label: "Type", lines: ["Geist, SIL Open Font License"], page: nil),
        Row(label: "Icons", lines: ["Phosphor Icons, MIT License"], page: nil),
    ]
}

/// home-credit-m (UIUX-homecredit-READY, DECIDED-BY-LEAD 27 Nov 00:21): ESO's credit carried on the home's Milky Way,
/// as ESO's terms ask that a credit not be hidden or parted from the picture: the app's name for the picture, then
/// ESO's credit unaltered (`apps/ios/notices/eso/PROVENANCE.json`), in Geist 12, its 16 pt box 16 pt under Start.
public enum HomeCredit {
    public static let line = "Milky Way: ESO/S. Brunier"
    public static let size = 12.0
    public static let gapUnderStart = 16.0
    public static let boxHeight = 16.0
}

/// The home's Milky Way: the part of the ESO panorama the app ships (its record's recipe: the full height and 1386 of
/// the 6000 columns about column 3005, each row in the sinusoidal projection) and the shade drawn over it, from the bottom up (arrive p-home's
/// `linear-gradient(to top, rgba(0,0,0,.9) 0%, rgba(0,0,0,.35) 34%, rgba(0,0,0,0) 55%)`).
public enum HomeBackdrop {
    public static let cropColumns = 1386
    public static let panoramaColumns = 6000
    public static let shade: [(opacity: Double, location: Double)] = [(0.9, 0), (0.35, 0.34), (0, 0.55)]
}
