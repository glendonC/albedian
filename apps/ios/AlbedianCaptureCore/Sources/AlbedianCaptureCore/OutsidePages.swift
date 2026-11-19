import Foundation

/// The pages the app hands to the system's Safari view (`SFSafariViewController`), and no other (mobile-4b, the lead's
/// answers of 2026-11-18). Safari's own process makes their requests, never the app, so the app's one gated
/// connection to the laptop (`CaptureUploader.permits`) stays the only one it opens itself.

/// The two Credits rows that link out (UIUX-credit-APPROVED), each to its source's own page as the repository's
/// records name it.
public enum OutsidePage: CaseIterable, Sendable {
    /// ESO's page for the Milky Way panorama eso0932a (the record's link; `apps/web/public/textures/PROVENANCE.json`).
    case milkyWayPhotograph
    /// The archive the repository's Hipparcos 2007 and Gaia DR3 records name for both catalogs
    /// (`apps/web/public/catalog/stars/gaia-dr3/PROVENANCE.json`).
    case starCatalogs

    public var address: URL {
        switch self {
        case .milkyWayPhotograph: URL(string: "https://www.eso.org/public/images/eso0932a/")!
        case .starCatalogs: URL(string: "https://gea.esac.esa.int/archive/")!
        }
    }
}

/// The reveal of one stored upload on the paired laptop's web app: `<paired address>/?upload=<sha256>`, the address
/// the web opens without an upload or a new solve (`apps/web/src/services/openUpload.ts`).
public enum RevealPage {
    /// The address for this photo, or nil when the hash is not a contract SHA-256 (the web would ignore it).
    public static func address(of sha256: String, on laptop: LaptopAddress) -> URL? {
        guard PhotoHash.isContractHash(sha256) else { return nil }
        return URL(string: laptop.description + "/?upload=" + sha256)
    }
}

/// The one decision before the Safari view opens a page.
public enum SafariViewGate {
    /// Whether `url` is one of the fixed credit pages, or the reveal of a contract hash on the paired laptop exactly as
    /// `RevealPage` writes it.
    public static func permits(_ url: URL, paired: LaptopAddress?) -> Bool {
        if OutsidePage.allCases.contains(where: { $0.address.absoluteString == url.absoluteString }) { return true }
        guard let paired else { return false }
        let prefix = paired.description + "/?upload="
        let text = url.absoluteString
        guard text.hasPrefix(prefix) else { return false }
        return RevealPage.address(of: String(text.dropFirst(prefix.count)), on: paired)?.absoluteString == text
    }
}
