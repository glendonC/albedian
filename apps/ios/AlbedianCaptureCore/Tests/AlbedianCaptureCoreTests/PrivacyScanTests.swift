import CryptoKit
import Foundation
import XCTest

/// REQ-IOS-6.1, the design's "the companion never sends the device's name", and mobile-0's one connection, checked on
/// the code. The scan reads every regular file in the app's folder and in the core's sources (the app's folder is a
/// synchronized group, so Xcode builds any file put there), walks into their folders, and refuses anything else there
/// (a link, say) and a file of a kind it cannot read, unless `assetFiles` lists its path and its bytes; in each file it
/// takes out comments and the contents of literals by reading the syntax (`SourceReader`), and reads names as whole
/// tokens. Its first rule is a list of the forms that make an address, each refused outside the transport's own files
/// (`transportFiles`, `addressMaking`, `schemes`): `URL(...)` and `URL.init(...)` with any first label but the two file
/// ones (`fileAddressLabels`), `URLComponents`, `NSURLComponents`, `NSURL`, `.init(...)` with one of the labels in
/// `addressMaking`, the `#URL` macro, a decodable type in a file that names `URL`, and a literal that holds a scheme.
/// Its limit: an address made by a call or an initialiser this list does not hold is not seen by the scan. The names
/// below (`sendingNames`, `sendingPrefixes`, `addressReads`) are a second net, and they too are a list: a call of a
/// linked framework that reads an address and is not named is not seen by name. What stands behind both lists is the
/// check of what was built (`apps/ios/scripts/check-built.zsh`: the built code's own refusals and its four closed
/// lists) and, in the end, the owner's check on a real phone and behind a proxy. The scan cannot see a name the code
/// puts together from pieces (by the preprocessor, or while the app runs), nor what comes from outside these files (a
/// framework's own behaviour, a dependency).
final class PrivacyScanTests: XCTestCase {
    /// A list of runs of tokens, each refused wherever it appears in code: reading a name a person gave the device or
    /// the computer, turning a place into an address, writing to a log, and the URL loading system's stores that keep
    /// something between requests or runs (cookies, credentials, the cache, a background session). Its limit: a way of
    /// doing these that is not listed is not seen.
    static let privacy = [
        "UIDevice.current.name", "UIDevice().name", ".localizedName", "hostName", "Host.current", "CLGeocoder", "reverseGeocode",
        "print(", "NSLog(", "os_log(", "Logger(", "OSLog", "os_signpost", "debugPrint(", "dump(",
        ".background(withIdentifier", "HTTPCookieStorage", "URLCredentialStorage", "URLCache",
    ]

    /// The second net: a list of names, each refused as a whole token wherever it appears in code, from the URL loading
    /// system, CFNetwork, sockets and name lookups, streams, Bonjour, the Network framework's other parts, web views and
    /// browsers, links and URL opening, the share, mail, message and document sheets, the pasteboard, peer-to-peer and
    /// nearby radios, the cloud, maps and geocoding, Handoff, push and the store, and names looked up while the app
    /// runs. Its limit: a call that is not named here is not seen by name.
    static let sendingNames: Set<String> = [
        "URLSession", "URLSessionConfiguration", "URLRequest", "NSURLSession", "NSURLConnection", "NSURLRequest", "NSMutableURLRequest",
        "NSURLDownload", "CFNetwork",
        "socket", "connect", "sendto", "sendmsg", "getaddrinfo", "gethostbyname", "gethostbyname2", "res_query",
        "Stream", "InputStream", "OutputStream", "NSStream", "NSInputStream", "NSOutputStream", "getStreamsToHost", "getBoundStreams",
        "NetService", "NetServiceBrowser", "NSNetService", "NSNetServiceBrowser",
        "NWListener", "NWBrowser", "NWConnectionGroup", "NWMulticastGroup", "NWEthernetChannel",
        "WebKit", "WKWebView", "WebView", "WebPage", "SafariServices", "SFSafariViewController", "AuthenticationServices",
        "ASWebAuthenticationSession",
        "openURL", "OpenURLAction", "UIApplication", "NSWorkspace", "Link", "AsyncImage",
        "ShareLink", "UIActivityViewController", "MessageUI", "MFMailComposeViewController", "MFMessageComposeViewController",
        "UIDocumentInteractionController", "UIDocumentPickerViewController", "fileExporter",
        "UIPasteboard", "NSPasteboard", "PasteButton",
        "MultipeerConnectivity", "MCSession", "MCNearbyServiceAdvertiser", "MCNearbyServiceBrowser", "MCAdvertiserAssistant",
        "GameKit", "CoreBluetooth", "NearbyInteraction", "ExternalAccessory", "NetworkExtension",
        "CloudKit", "CKContainer", "CKDatabase", "NSUbiquitousKeyValueStore", "forUbiquityContainerIdentifier",
        "MapKit", "MKMapView", "MKLocalSearch",
        "NSUserActivity", "registerForRemoteNotifications", "StoreKit",
        "dlsym", "dlopen", "NSClassFromString", "NSSelectorFromString", "objc_getClass", "objc_lookUpClass", "objc_getRequiredClass",
        // Calls that read an address themselves (AUDIT L, the fourth version): XML, media, link previews, the photo
        // library (which iCloud may carry), and the raw data and string types' own readers
        "XMLParser", "NSXMLParser", "AVPlayer", "AVQueuePlayer", "AVURLAsset", "AVAsset", "LinkPresentation", "LPMetadataProvider",
        "LPLinkView", "Photos", "PhotosUI", "PHPhotoLibrary", "PHAssetCreationRequest", "NSData", "NSString", "NSDictionary", "NSArray",
    ]

    /// Reading the contents of an address with the standard types' own readers: a second net beside the first rule,
    /// each allowed only where `exceptions` lists it.
    static let addressReads = ["Data(contentsOf:", "Data.init(contentsOf:", "String(contentsOf:", "String.init(contentsOf:"]

    /// The transport's own files, by path under `apps/ios`: the only ones where the first rule lets an address that is
    /// not a file's be made (a file sharing the basename elsewhere, such as `AlbedianCapture/UploadClient.swift`, is refused).
    static let transportPaths: Set<String> = [
        "AlbedianCaptureCore/Sources/AlbedianCaptureCore/LaptopConnection.swift",
        "AlbedianCaptureCore/Sources/AlbedianCaptureCore/UploadClient.swift",
    ]

    /// The system's Safari view (mobile-4b, the lead's answer of 2026-11-18): the one file where its two names may stand,
    /// by path under `apps/ios`; it shows only pages `SafariViewGate` allows, and Safari's process makes the request.
    static let safariViewPath = "AlbedianCapture/Screens/SafariView.swift"
    static let safariViewNames: Set<String> = ["SafariServices", "SFSafariViewController"]

    /// Where the app's own code needs what the rules refuse: file, line, the code on it, and why.
    static let exceptions: [Exception] = [
        (file: "CaptureStore.swift", line: 98, code: "public func read(_ url: URL) throws -> Data { try Data(contentsOf: url) }",
         reason: "the store reads back each file it has just written, to count it kept only when the bytes are equal (P3); the address is a file's in the app's own folder"),
        (file: "OutsidePages.swift", line: 18, code: "case .milkyWayPhotograph: URL(string: \"https://www.eso.org/public/images/eso0932a/\")!",
         reason: "one of the two fixed credit pages (UIUX-credit-APPROVED); only handed to the system's Safari view after SafariViewGate, so Safari makes the request, not the app"),
        (file: "OutsidePages.swift", line: 19, code: "case .starCatalogs: URL(string: \"https://gea.esac.esa.int/archive/\")!",
         reason: "the other fixed credit page, the archive the repository's Hipparcos and Gaia records name; only handed to the system's Safari view after SafariViewGate"),
        (file: "OutsidePages.swift", line: 30, code: "return URL(string: laptop.description + \"/?upload=\" + sha256)",
         reason: "the reveal on the paired laptop's web app for a contract SHA-256 (the lead's answer, mobile-4b); only handed to the system's Safari view after SafariViewGate, never fetched by the app"),
        (file: "CompanionFlow.swift", line: 25, code: "public static let fixedScheme = \"http://\"",
         reason: "the scheme the connect screen shows fixed in front of the field (UIUX-connect-APPROVED); the typed text joined to it is read only as a pairing address (LaptopAddress: plain http to a local network host), and nothing is opened from it"),
    ]

    typealias Asset = (path: String, sha256: String, reason: String)

    /// The files the scan cannot read that the app is built with: each by its path under a built folder, its bytes'
    /// sha256 and why. A file the scan cannot read passes only when both its path and its bytes are listed here.
    static let assetFiles: [Asset] = [
        (path: "Fonts/Geist-Regular.ttf", sha256: "85a1c6b18a6b0a06dfe9fd4f6d6a5d4979f74ec861eaef4bc7868b5492b8a117",
         reason: "Geist v1.9.2, the approved type: a static TrueType file of the release's tag, byte for byte (SIL OFL 1.1, apps/ios/notices/geist, data/downloads.json)"),
        (path: "Fonts/Geist-SemiBold.ttf", sha256: "b9f52322d6d20a7ff1e7fe1310ff12ec851aebb83d24f8b4f1d4149298f489b2",
         reason: "Geist v1.9.2, the approved type: a static TrueType file of the release's tag, byte for byte (SIL OFL 1.1, apps/ios/notices/geist, data/downloads.json)"),
        (path: "Fonts/OFL.txt", sha256: "021caa749f89fecd7c2e40a50499de29e84d2bc19251659b47d499c2fbfffc17",
         reason: "Geist v1.9.2's SIL Open Font License 1.1 text, bundled beside the embedded fonts so the licence travels with every built copy (apps/ios/notices/geist/OFL.txt, data/downloads.json)"),
        (path: "Notices/Phosphor-LICENSE.txt", sha256: "ddbe6082ec3cf979db47e5af549d2849c5d6182b3e005ef91ce1dbb9eb122f11",
         reason: "Phosphor Icons 2.1.1's MIT copyright and permission notice, bundled so the notice travels with every built copy (apps/ios/notices/phosphor/LICENSE, data/downloads.json)"),
        (path: "Assets.xcassets/Contents.json", sha256: "f5fb0e1f3a8142892ed092efb5955c1c2cc537003a89ad7bbdb9dade7fe8985f",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/arrow-right.imageset/arrow-right.svg", sha256: "e45bc4a12592b8723110db42c6deffdf177e4fe7704cf50324963fa0a59b2d74",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/arrow-right.imageset/Contents.json", sha256: "37db3a9e43829b935a56492e19fc174cdc7819caa9d07cc518b2c08460e44b15",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/x.imageset/x.svg", sha256: "7397b15e1fa66eaf6ff9b59fe91d483cb5c3bcf75b2fe052097ab6f0593b513a",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/x.imageset/Contents.json", sha256: "3d768901fe385b8ae1bcca0c7fb6c85dba62453178251277d49cbebdb27e6275",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/paper-plane-tilt.imageset/paper-plane-tilt.svg", sha256: "0dbff2ada31c963dfd95bfe2ab2cb39a90fe7100eb7c1724577444a1d8a7c26d",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/paper-plane-tilt.imageset/Contents.json", sha256: "0653ef96a6beb15a46cc8a101b60b306d58ef9668b91a479e73637348e7295d1",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/arrow-counter-clockwise.imageset/arrow-counter-clockwise.svg", sha256: "4eb160d5ae781107c674481ac081962e6bce6281a646129e6c3f960b9dd5dac6",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/arrow-counter-clockwise.imageset/Contents.json", sha256: "9155f322a819068f24fecca253c61513b582a2e777e9158096f84e185bfb56e8",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/arrow-left.imageset/arrow-left.svg", sha256: "3c2001cf957d182fc54927e4b286cc8cf66dd97830f441ca2650d42a63a9006a",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/arrow-left.imageset/Contents.json", sha256: "9e4ddba329640c4d3e100746381bc6fd36430f279b14cda155191f237d19ce1f",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/arrow-square-out.imageset/arrow-square-out.svg", sha256: "8610e8f5c7d4952ae1ada34ef56fa7cbab1780bc85f25a5c9482b74144790fc8",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/arrow-square-out.imageset/Contents.json", sha256: "b2921a719bcbde94fbe8633026de1a92d6ab36f06e031628967ab0115eb1981d",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/wifi-slash.imageset/wifi-slash.svg", sha256: "39ed62ae822a3917ac70239bbaa2144152049783451f4687456aae8f044152fc",
         reason: "a Phosphor 2.1.1 regular icon the approved screens use, byte for byte from the package (MIT, apps/ios/notices/phosphor, data/downloads.json)"),
        (path: "Assets.xcassets/wifi-slash.imageset/Contents.json", sha256: "d34c9ed7681ca6a346d950afc69548a8c75fdb974e59d0a85f2c505dd05938bc",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Assets.xcassets/milky-way-home.imageset/milky-way-home.jpg", sha256: "d453e529eba662a0ee4eeb7fef16a40a22333325c2bcaf544de9aca71a231b71",
         reason: "the approved iPhone home's Milky Way: a crop of the web's own eso0932a panorama (ESO/S. Brunier, CC BY 4.0, apps/ios/notices/eso)"),
        (path: "Assets.xcassets/milky-way-home.imageset/Contents.json", sha256: "2769ecab90823d7c763d7a7f67c47d761dd014cb037aa156e5fdbf0add0630f4",
         reason: "the asset catalog's own description of its images, written for this app"),
        (path: "Fonts/Geist-wght.woff2", sha256: "2ffebe993e969069a9789d15164b7715d42491b5835516c5e3b935d5f81b05f1",
         reason: "Geist v1.9.2's variable font, the web's own copy byte for byte, for the approved light weight (SIL OFL 1.1, apps/ios/notices/geist)"),
    ]

    static func sha256(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    /// Whether `url`, found under `root`, is a listed asset: the same path under that folder and the same bytes.
    static func isListed(_ url: URL, under root: URL, in assets: [Asset]) -> Bool {
        // Both with their links resolved: a temporary folder is reached as /var and walked as /private/var
        let rootPath = root.resolvingSymlinksInPath().path
        let base = rootPath.hasSuffix("/") ? rootPath : rootPath + "/"
        let full = url.resolvingSymlinksInPath().path
        guard full.hasPrefix(base) else { return false }
        let path = String(full.dropFirst(base.count))
        guard let asset = assets.first(where: { $0.path == path }), let data = try? Data(contentsOf: url) else { return false }
        return sha256(data) == asset.sha256
    }

    /// The first labels of `URL(...)` the first rule lets through outside the transport: a file's address from a path.
    static let fileAddressLabels: Set<String> = ["fileURLWithPath", "filePath"]

    /// Names refused by their start: the C interfaces of CFNetwork, the Network framework, DNS service discovery and
    /// reachability, and the URL loading system's other types.
    static let sendingPrefixes = ["CFHTTP", "CFStream", "CFReadStream", "CFWriteStream", "CFSocket", "CFNetService", "CFHost", "nw_",
                                  "DNSService", "SCNetworkReachability", "NSURLSession", "URLSession"]

    /// The one connection type, and the calls that open, write and read it: each only at `clientPath`.
    static let clientPath = "AlbedianCaptureCore/Sources/AlbedianCaptureCore/LaptopConnection.swift"
    static let connectionType = "NWConnection"
    static let connectionCalls = ["NWConnection(", ".send(content:", ".receive(minimumIncompleteLength:"]

    struct Finding: Equatable, CustomStringConvertible {
        let file: String
        let line: Int
        let what: String
        var description: String { "\(file):\(line): \(what)" }
    }

    /// The folders the app is built from: the core's sources and the app's synchronized folder.
    static var builtRoots: [URL] {
        let ios = repositoryRoot.appendingPathComponent("apps/ios")
        return [ios.appendingPathComponent("AlbedianCaptureCore/Sources"), ios.appendingPathComponent("AlbedianCapture")]
    }

    /// Whether `pattern` (a run of tokens) appears in `tokens`, and at which lines.
    static func lines(of pattern: String, in tokens: [SourceReader.Token]) -> [Int] {
        let wanted = SourceReader.tokens(pattern).map(\.text)
        guard !wanted.isEmpty, tokens.count >= wanted.count else { return [] }
        return (0...(tokens.count - wanted.count)).filter { start in
            wanted.indices.allSatisfy { tokens[start + $0].text == wanted[$0] }
        }.map { tokens[$0].line }
    }

    /// The findings under `roots`: what is neither a regular file nor a folder, a file the scan cannot read, a privacy
    /// pattern, a sending name, a form of the first rule, a literal that holds a scheme, an address read outside a
    /// listed exception, and the connection type or a connection call outside the client file; plus how many times each
    /// connection call appears in it.
    static func scan(_ roots: [URL], exceptions: [Exception] = exceptions, assets: [Asset] = assetFiles) throws -> (findings: [Finding], inClient: [String: Int]) {
        var findings: [Finding] = []
        var inClient: [String: Int] = [:]
        let iosPrefix = repositoryRoot.appendingPathComponent("apps/ios").resolvingSymlinksInPath().path + "/"
        for root in roots {
            let rootPrefix = root.resolvingSymlinksInPath().path + "/"
            guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey]) else {
                findings.append(Finding(file: root.lastPathComponent, line: 0, what: "a folder the scan cannot open")); continue
            }
            for case let url as URL in walker {
                let name = url.lastPathComponent
                let resolved = url.resolvingSymlinksInPath().path
                let relPath = resolved.hasPrefix(iosPrefix)
                    ? String(resolved.dropFirst(iosPrefix.count))
                    : (resolved.hasPrefix(rootPrefix) ? String(resolved.dropFirst(rootPrefix.count)) : name)
                // A folder is walked into; anything else that is not a regular file (a link, say) is not read through
                let kind = try? url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey])
                if kind?.isDirectory == true { continue }
                guard kind?.isRegularFile == true else {
                    findings.append(Finding(file: name, line: 0, what: "not a regular file or a folder")); continue
                }
                guard let language = SourceReader.language(of: url) else {
                    // A listed asset passes by its path under this folder and its bytes; any other such file is refused
                    if !isListed(url, under: root, in: assets) {
                        findings.append(Finding(file: name, line: 0, what: "a kind of file the scan cannot read"))
                    }
                    continue
                }
                guard let text = try? String(contentsOf: url, encoding: .utf8) else {
                    findings.append(Finding(file: name, line: 0, what: "not UTF-8 text")); continue
                }
                let tokens: [SourceReader.Token]
                do { tokens = SourceReader.tokens(try SourceReader.code(text, language)) } catch {
                    findings.append(Finding(file: name, line: error.line, what: "unreadable: \(error.why)")); continue
                }
                for pattern in privacy { for line in lines(of: pattern, in: tokens) { findings.append(Finding(file: name, line: line, what: pattern)) } }
                for token in tokens where sendingNames.contains(token.text) || sendingPrefixes.contains(where: { token.text.hasPrefix($0) }) {
                    if relPath == safariViewPath, safariViewNames.contains(token.text) { continue }
                    findings.append(Finding(file: name, line: token.line, what: token.text))
                }
                // An exception counts only while its line holds the very code it lists
                let sourceLines = text.components(separatedBy: "\n")
                let exempted: (Int) -> Bool = { line in
                    exempt(name, line, line >= 1 && line <= sourceLines.count ? sourceLines[line - 1] : "", in: exceptions)
                }
                let isTransport = transportPaths.contains(relPath)
                findings += addressMaking(tokens, file: name, isTransport: isTransport, exempted: exempted)
                if let literals = try? SourceReader.split(text, language).literals {
                    findings += schemes(in: literals, file: name, isTransport: isTransport, exempted: exempted)
                }
                for pattern in addressReads {
                    for line in lines(of: pattern, in: tokens) where !exempted(line) { findings.append(Finding(file: name, line: line, what: pattern)) }
                }
                if relPath == clientPath {
                    for call in connectionCalls { inClient[call, default: 0] += lines(of: call, in: tokens).count }
                    for line in lines(of: "NWConnection.init", in: tokens) { findings.append(Finding(file: name, line: line, what: "NWConnection.init")) }
                } else {
                    for token in tokens where token.text == connectionType { findings.append(Finding(file: name, line: token.line, what: connectionType)) }
                    for call in connectionCalls.dropFirst() { for line in lines(of: call, in: tokens) { findings.append(Finding(file: name, line: line, what: call)) } }
                }
            }
        }
        return (findings, inClient)
    }

    typealias Exception = (file: String, line: Int, code: String, reason: String)

    /// Whether a listed exception excuses this line: the same file, the same line, and on it the very code the exception
    /// lists (leading and trailing spaces aside), so an exception stops counting once its line's code changes or moves.
    static func exempt(_ file: String, _ line: Int, _ code: String, in list: [Exception] = exceptions) -> Bool {
        list.contains { $0.file == file && $0.line == line && $0.code == code.trimmingCharacters(in: .whitespaces) }
    }

    /// The first rule's forms in code, each refused outside the transport's files: a URL made other than from a path,
    /// URL components, the Objective-C URL, `.init(...)` with a label from `reads` below, the `#URL` macro, and a
    /// decodable type in a file that names an address. A form not on this list is not seen.
    static func addressMaking(_ tokens: [SourceReader.Token], file: String, isTransport: Bool = false, exempted: (Int) -> Bool) -> [Finding] {
        guard !isTransport else { return [] }
        var found: [Finding] = []
        func text(_ k: Int) -> String? { k < tokens.count ? tokens[k].text : nil }
        for (index, token) in tokens.enumerated() {
            let what: String?
            switch token.text {
            case "URLComponents", "NSURLComponents", "NSURL":
                what = token.text
            case "URL":
                // URL( or URL.init( : allowed only as a file's address from a path
                let open = text(index + 1) == "(" ? index + 1 : (text(index + 1) == "." && text(index + 2) == "init" && text(index + 3) == "(" ? index + 3 : nil)
                if index > 0, text(index - 1) == "#" {
                    what = "#URL"
                } else if let open {
                    what = text(open + 2) == ":" && fileAddressLabels.contains(text(open + 1) ?? "") && !exempted(token.line) ? nil : "URL(" + (text(open + 1) ?? "")
                } else {
                    what = nil
                }
            case "init":
                let reads: Set<String> = ["string", "dataRepresentation", "resolvingBookmarkData", "resolvingAliasFileAt", "encodingInvalidCharacters"]
                what = index > 0 && text(index - 1) == "." && text(index + 1) == "(" && reads.contains(text(index + 2) ?? "") ? ".init(" + text(index + 2)! : nil
            default:
                what = nil
            }
            if let what, !exempted(token.line) { found.append(Finding(file: file, line: token.line, what: what)) }
        }
        if let decodable = tokens.first(where: { $0.text == "Codable" || $0.text == "Decodable" }), tokens.contains(where: { $0.text == "URL" }) {
            found.append(Finding(file: file, line: decodable.line, what: "a decodable type beside an address"))
        }
        return found
    }

    /// A literal that holds a scheme (`name://`, or one of the address schemes that need no slashes), outside the
    /// transport's files. Escapes are read as the characters they stand for.
    static func schemes(in literals: String, file: String, isTransport: Bool = false, exempted: (Int) -> Bool) -> [Finding] {
        guard !isTransport else { return [] }
        let pattern = try! NSRegularExpression(pattern: "[A-Za-z][A-Za-z0-9+.-]*://|\\b(https?|ftp|wss?|mailto|tel|sms|facetime|maps|data|javascript|itms[a-z-]*):",
                                               options: [.caseInsensitive])
        var found: [Finding] = []
        for (index, line) in literals.components(separatedBy: "\n").enumerated() where !exempted(index + 1) {
            // Read as written and with the gaps between pieces taken out, so a scheme split across two literals is one
            let joined = line.filter { !$0.isWhitespace }
            if pattern.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
                || pattern.firstMatch(in: joined, range: NSRange(joined.startIndex..., in: joined)) != nil {
                found.append(Finding(file: file, line: index + 1, what: "a literal that holds a scheme"))
            }
        }
        return found
    }

    func testFindsNothingOnItsListsInAnyFileTheAppIsBuiltFrom() throws {
        let (findings, inClient) = try Self.scan(Self.builtRoots)
        XCTAssertEqual(findings, [])
        XCTAssertEqual(inClient, ["NWConnection(": 1, ".send(content:": 1, ".receive(minimumIncompleteLength:": 1])
        var count = 0
        for root in Self.builtRoots { count += FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?.allObjects.count ?? 0 }
        XCTAssertGreaterThanOrEqual(count, 10)
    }

    func testExcusesAListedLineOnlyWhileItHoldsTheCodeTheExceptionLists() throws {
        let code = "let d = try Data(contentsOf: file)"
        let listed: [Exception] = [(file: "Store.swift", line: 2, code: code, reason: "a planted exception for this test")]
        // The listed line, holding the listed code: excused
        XCTAssertEqual(try scanPlanted(["Store.swift": "import Foundation\n    \(code)\n"], exceptions: listed), [])
        // The same line holding other code, the code moved to another line, and the same code in another file: refused
        XCTAssertEqual(try scanPlanted(["Store.swift": "import Foundation\nlet d = try Data(contentsOf: other)\n"], exceptions: listed).map(\.what),
                       ["Data(contentsOf:"])
        XCTAssertEqual(try scanPlanted(["Store.swift": "import Foundation\n\n\(code)\n"], exceptions: listed).map(\.line), [3])
        XCTAssertEqual(try scanPlanted(["Other.swift": "import Foundation\n\(code)\n"], exceptions: listed).map(\.what), ["Data(contentsOf:"])
        XCTAssertTrue(Self.exempt("Store.swift", 2, "  \(code)  ", in: listed))
        XCTAssertFalse(Self.exempt("Store.swift", 2, code + " ", in: []))
        XCTAssertFalse(Self.exempt("Store.swift", 2, "let d = try Data(contentsOf: file) // changed", in: listed))
    }

    func testListsEachExceptionAtTheLineThatHoldsItsCode() throws {
        // An exception names a line; if the code moves or changes, the exception no longer stands for it
        for exception in Self.exceptions {
            let url = try XCTUnwrap(Self.builtRoots.lazy.compactMap { root in
                FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?.compactMap { $0 as? URL }.first { $0.lastPathComponent == exception.file }
            }.first, exception.file)
            let lines = try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")
            XCTAssertGreaterThanOrEqual(lines.count, exception.line, exception.file)
            XCTAssertEqual(lines[exception.line - 1].trimmingCharacters(in: .whitespaces), exception.code, "\(exception.file):\(exception.line)")
            XCTAssertFalse(exception.reason.isEmpty)
        }
    }

    func testPassesAFileItCannotReadOnlyWhenItsPathAndItsBytesAreListed() throws {
        let bytes = "planted font bytes"
        let listed: [Asset] = [(path: "Fonts/A.ttf", sha256: Self.sha256(Data(bytes.utf8)), reason: "a planted asset for this test")]
        let refused = ["a kind of file the scan cannot read"]
        // The listed path holding the listed bytes: passed
        XCTAssertEqual(try scanPlanted(["Fonts/A.ttf": bytes], assets: listed), [])
        // Other bytes at the listed path, the listed bytes at another path or name, and nothing listed: refused
        XCTAssertEqual(try scanPlanted(["Fonts/A.ttf": bytes + " "], assets: listed).map(\.what), refused)
        XCTAssertEqual(try scanPlanted(["Other/A.ttf": bytes], assets: listed).map(\.what), refused)
        XCTAssertEqual(try scanPlanted(["A.ttf": bytes], assets: listed).map(\.what), refused)
        XCTAssertEqual(try scanPlanted(["Fonts/B.ttf": bytes], assets: listed).map(\.what), refused)
        XCTAssertEqual(try scanPlanted(["Fonts/A.ttf": bytes]).map(\.what), refused)
        // A listed file the scan can read is still read: a listing never hides code
        let code = "import Foundation\nprint(1)\n"
        let listedCode: [Asset] = [(path: "Code.swift", sha256: Self.sha256(Data(code.utf8)), reason: "a planted listing of code")]
        XCTAssertEqual(try scanPlanted(["Code.swift": code], assets: listedCode).map(\.what), ["print("])
    }

    func testListsEachAssetWithTheBytesItHolds() throws {
        // An asset is listed by its path under a built folder and its bytes; if either changes, the listing no longer stands
        for asset in Self.assetFiles {
            let url = try XCTUnwrap(Self.builtRoots.lazy.map { $0.appendingPathComponent(asset.path) }.first {
                FileManager.default.fileExists(atPath: $0.path)
            }, asset.path)
            XCTAssertEqual(Self.sha256(try Data(contentsOf: url)), asset.sha256, asset.path)
            XCTAssertNil(SourceReader.language(of: url), "\(asset.path) is a file the scan reads, so it needs no listing")
            XCTAssertFalse(asset.reason.isEmpty)
        }
        XCTAssertEqual(Set(Self.assetFiles.map(\.path)).count, Self.assetFiles.count, "each asset is listed once")
    }

    func testOpensTheOneConnectionOnlyToTheEndpointTheGateApproved() throws {
        // The one place a connection opens takes the endpoint `permits` approved, the same value
        let uploader = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/AlbedianCaptureCore/Sources/AlbedianCaptureCore/UploadClient.swift"),
                                  encoding: .utf8)
        let code = try SourceReader.code(uploader, .swift)
        let gate = try XCTUnwrap(code.range(of: "guard let approved = Self.permits(method: method, path: path, to: address) else"))
        let call = try XCTUnwrap(code.range(of: "await transport.exchange(request, to: approved.endpoint"))
        XCTAssertLessThan(gate.lowerBound, call.lowerBound)
        XCTAssertFalse(code[gate.upperBound..<call.lowerBound].contains("func "), "the gate is in another function")
        XCTAssertEqual(Self.lines(of: "transport.exchange(", in: SourceReader.tokens(code)).count, 1)
        let client = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/AlbedianCaptureCore/Sources/AlbedianCaptureCore/LaptopConnection.swift"),
                                encoding: .utf8)
        let clientTokens = SourceReader.tokens(try SourceReader.code(client, .swift))
        XCTAssertEqual(Self.lines(of: "NWConnection(host: NWEndpoint.Host(endpoint.host), port: port, using: parameters)", in: clientTokens).count, 1)
    }

    /// Writes `files` (name to text) into a fresh folder, scans it, and removes it.
    private func scanPlanted(_ files: [String: String], exceptions: [Exception] = [], assets: [Asset] = []) throws -> [Finding] {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-scan-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for (name, text) in files {
            let url = folder.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(text.utf8).write(to: url)
        }
        return try Self.scan([folder], exceptions: exceptions, assets: assets).findings
    }

    func testSeesEachWayOutTheLastReadersWroteAndOneNameFromManyFamilies() throws {
        // The reader's spellings (AUDIT G, mobile-0's third version) and one name from many families, each in a file of its own
        let planted: [(String, String, String)] = [
            ("V1.swift", "let c = NWConnection.init(host: \"h\", port: 1, using: .tcp)\nc.send(\n content: nil, completion: .idempotent)", "NWConnection"),
            ("V2.swift", "let c = NWConnection (host: \"h\", port: 1, using: .tcp)", "NWConnection"),
            ("V3.swift", "static let place = \"http://x\"; let s = URLSession(configuration: .ephemeral)", "URLSession"),
            ("V4.swift", "let fd = socket(AF_INET, SOCK_STREAM, 0)\n_ = connect(fd, nil, 0)", "socket"),
            ("V5.swift", "Stream.getStreamsToHost(withName: \"h\", port: 1, inputStream: nil, outputStream: nil)", "Stream"),
            ("V7.swift", "ShareLink(item: url)\nUIPasteboard.general.string = text", "ShareLink"),
            // The Safari view's names stand only in AlbedianCapture/Screens/SafariView.swift; a file anywhere else is refused
            ("V10.swift", "import SafariServices\nlet v = SFSafariViewController(url: u)", "SFSafariViewController"),
            ("V9.m", "#import <Foundation/Foundation.h>\nvoid f(void) { [[NSURLSession sharedSession] dataTaskWithURL:nil]; }", "NSURLSession"),
            ("Raw.swift", "let x = #\"a \\(b)\"# + \"\\(URLRequest(url: u))\"", "URLRequest"),
            ("RawInterp.swift", "let x = #\"\\#(WKWebView())\"#", "WKWebView"),
            ("Multi.swift", "let x = \"\"\"\n  text \"quoted\" \\(CFStreamCreatePairWithSocketToHost(nil, nil, 0, nil, nil))\n\"\"\"", "CFStreamCreatePairWithSocketToHost"),
            ("Backtick.swift", "let s = `URLSession`.self", "URLSession"),
            ("Nested.swift", "/* a /* b */ c */ let x = MCSession(peer: p)", "MCSession"),
            ("AfterBlock.m", "/* NSURLSession */ id c = [CKContainer defaultContainer];", "CKContainer"),
            ("Lookup.swift", "let k: AnyClass? = NSClassFromString(\"NSURL\" + \"Session\")", "NSClassFromString"),
            ("Dl.c", "void *f = dlsym(RTLD_DEFAULT, \"sock\" \"et\");", "dlsym"),
            ("Geo.swift", "CLGeocoder().reverseGeocodeLocation(l) { _, _ in }", "CLGeocoder"),
            ("Open.swift", "UIApplication.shared.open(url)", "UIApplication"),
            ("Mail.swift", "import MessageUI\nlet m = MFMailComposeViewController()", "MessageUI"),
            ("Net.swift", "let l = try NWListener(using: .tcp)", "NWListener"),
            ("C.c", "int f(void) { return nw_connection_create(0, 0) != 0; }", "nw_connection_create"),
            // The first rule (AUDIT L's case, and each form on its list): the address is refused where it is made, so
            // the call that then reads it does not matter
            ("Read.swift", "let d = try Data(contentsOf: URL(string: \"https://example.com/u?d=1\")!)", "URL(string"),
            ("Comps.swift", "var c = URLComponents(); c.host = h; let u = c.url", "URLComponents"),
            ("Init.swift", "let u: URL? = .init(string: s)", ".init(string"),
            ("InitURL.swift", "let u = URL.init(string: s)", "URL(string"),
            ("Bytes.swift", "let u = URL(dataRepresentation: d, relativeTo: nil)", "URL(dataRepresentation"),
            ("Bookmark.swift", "var stale = false; let u = try URL(resolvingBookmarkData: d, bookmarkDataIsStale: &stale)", "URL(resolvingBookmarkData"),
            ("Macro.swift", "let u = #URL(\"https://example.com\")", "#URL"),
            ("ObjC.m", "NSURL *u = [NSURL URLWithString:s];", "NSURL"),
            ("Decode.swift", "struct R: Decodable { let next: URL }", "a decodable type beside an address"),
            ("Literal.swift", "let base = \"https://example.com/\"", "a literal that holds a scheme"),
            ("Escaped.swift", "let base = \"h\\u{74}tp:/\" + \"/x\"", "a literal that holds a scheme"),
            ("Mail.swift", "let to = \"mailto:someone\"", "a literal that holds a scheme"),
            // Seen only by joining the pieces, and only by reading an escape as its character
            ("Split.swift", "let u = \"custom:/\" + \"/host\"", "a literal that holds a scheme"),
            ("Colon.swift", "let u = \"custom\\u{3A}//host\"", "a literal that holds a scheme"),
            ("CLiteral.c", "const char *u = \"wss://x\";", "a literal that holds a scheme"),
            // The second net, by name: the reader's eight
            ("DataRead.swift", "let d = try Data(contentsOf: file)", "Data(contentsOf:"),
            ("StringRead.swift", "let s = try String(contentsOf: file, encoding: .utf8)", "String(contentsOf:"),
            ("XML.swift", "let p = XMLParser(contentsOf: file)", "XMLParser"),
            ("Player.swift", "import AVFoundation\nlet p = AVPlayer(url: file)", "AVPlayer"),
            ("Preview.swift", "import LinkPresentation\nLPMetadataProvider().startFetchingMetadata(for: u) { _, _ in }", "LPMetadataProvider"),
            ("Library.swift", "import Photos\nPHPhotoLibrary.shared().performChanges({}, completionHandler: nil)", "PHPhotoLibrary"),
            ("NSDataRead.swift", "let d = NSData(contentsOf: file)", "NSData"),
            ("Drive.swift", "let u = FileManager.default.url(forUbiquityContainerIdentifier: nil)", "forUbiquityContainerIdentifier"),
        ]
        for (name, text, expected) in planted {
            let findings = try scanPlanted([name: text])
            XCTAssertTrue(findings.contains { $0.what == expected }, "\(name): \(findings)")
        }
        // A file's address from a path, an array's append, and the system's own folders are not addresses that leave
        let files = try scanPlanted(["Files.swift": "let a = URL(fileURLWithPath: p)\nlet b = URL(filePath: p)\nvar x: [UInt8] = []\nx.append(contentsOf: y)\n"
                                     + "let c = FileManager.default.temporaryDirectory.appendingPathComponent(\"a.json\")"])
        XCTAssertEqual(files, [])
        // Names in comments and literals are not code; code inside an interpolation is
        let quiet = try scanPlanted(["Quiet.swift": "// URLSession\n/* NWListener /* nested */ */\nlet a = \"URLSession and socket\"\nlet b = #\"\\(URLSession)\"#\n",
                                     "Quiet.m": "// NSURLSession \\n\n/* socket */ const char *s = \"connect\"; char c = '\\'';\n"])
        XCTAssertEqual(quiet, [])
        // A file the scan cannot read, and not listed by its path and bytes, is a finding, never passed over
        let unreadable = try scanPlanted(["Notes.txt": "x", "Shader.swift": "let r = /a//; let s = URLSession.shared",
                                          "Open.swift": "let s = \"never closed\nlet t = 1", "Splice.c": "int URL\\\nSession;",
                                          "Regex.swift": "let r = #/a/#", "Interp.swift": "let s = \"\\(a\""])
        XCTAssertEqual(Set(unreadable.map(\.file)), ["Notes.txt", "Shader.swift", "Open.swift", "Splice.c", "Regex.swift", "Interp.swift"])
        XCTAssertTrue(unreadable.allSatisfy { $0.what.hasPrefix("unreadable") || $0.what == "a kind of file the scan cannot read" }, "\(unreadable)")
        // The connection's calls outside the client file, and files sharing the transport basenames outside their core paths
        let outside = try scanPlanted(["Other.swift": "func f(c: NWConnection) { c.send (content: d, completion: .idempotent) }"])
        XCTAssertEqual(outside.map(\.what), ["NWConnection", ".send(content:"])
        let spoofedUpload = try scanPlanted(["AlbedianCapture/UploadClient.swift": "import Foundation\nlet u = URL(string: \"https://example.com\")\n"])
        XCTAssertEqual(spoofedUpload.map(\.what), ["URL(string", "a literal that holds a scheme"])
        let spoofedClient = try scanPlanted(["AlbedianCapture/LaptopConnection.swift": "let c = NWConnection(host: h, port: p, using: .tcp)\n"])
        XCTAssertEqual(spoofedClient.map(\.what), ["NWConnection"])
    }

    func testFindsWhatIsNeitherARegularFileNorAFolder() throws {
        // A link in a built folder is not read through, so it is a finding; a real folder is walked into
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-scan-\(UUID().uuidString)")
        let outside = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-scan-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder); try? FileManager.default.removeItem(at: outside) }
        try FileManager.default.createDirectory(at: folder.appendingPathComponent("Inner"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try Data("let x = 1\n".utf8).write(to: outside.appendingPathComponent("Real.swift"))
        try Data("let y = 2\n".utf8).write(to: folder.appendingPathComponent("Inner/Plain.swift"))
        try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("Link.swift"), withDestinationURL: outside.appendingPathComponent("Real.swift"))
        try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("Linked"), withDestinationURL: outside)
        let findings = try Self.scan([folder], exceptions: []).findings
        XCTAssertEqual(Set(findings.map(\.file)), ["Link.swift", "Linked"])
        XCTAssertEqual(Set(findings.map(\.what)), ["not a regular file or a folder"])
    }

    func testKeepsLineNumbersAndReadsEachLiteralKind() throws {
        let text = "let a = 1 // x\n/* one\ntwo */ let b = \"q\\\"uote\"\nlet c = \"\"\"\nline \\(d)\n\"\"\"\nlet e = ##\"a\"#b\"##\nlet f = \"\\u{41}\" + g / h\n"
        let tokens = SourceReader.tokens(try SourceReader.code(text, .swift))
        XCTAssertEqual(tokens.filter { $0.text.count > 0 && SourceReader.isIdentifierPart($0.text.unicodeScalars.first!) }.map { "\($0.text)@\($0.line)" },
                       ["let@1", "a@1", "1@1", "let@3", "b@3", "let@4", "c@4", "d@5", "let@7", "e@7", "let@8", "f@8", "g@8", "h@8"])
    }
}
