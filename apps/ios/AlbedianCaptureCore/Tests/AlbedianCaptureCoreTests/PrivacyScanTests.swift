import Foundation
import XCTest

/// REQ-IOS-6.1, the design's "the companion never sends the device's name", and mobile-0's one connection, checked on
/// the code. The scan reads every regular file in the app's folder and in the core's sources (the app's folder is a
/// synchronized group, so Xcode builds any file put there), walks into their folders, and refuses anything else there
/// (a link, say) and a file of a kind it cannot read; in each file it
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

    /// The transport's own files: the only ones where the first rule lets an address that is not a file's be made (the
    /// paired laptop's, from the text a person types, and the request's Host line).
    static let transportFiles: Set<String> = ["LaptopConnection.swift", "UploadClient.swift"]

    /// Where the app's own code needs what the rules refuse: file, line, the code on it, and why. Empty in this unit.
    static let exceptions: [Exception] = []

    /// The first labels of `URL(...)` the first rule lets through outside the transport: a file's address from a path.
    static let fileAddressLabels: Set<String> = ["fileURLWithPath", "filePath"]

    /// Names refused by their start: the C interfaces of CFNetwork, the Network framework, DNS service discovery and
    /// reachability, and the URL loading system's other types.
    static let sendingPrefixes = ["CFHTTP", "CFStream", "CFReadStream", "CFWriteStream", "CFSocket", "CFNetService", "CFHost", "nw_",
                                  "DNSService", "SCNetworkReachability", "NSURLSession", "URLSession"]

    /// The one connection type, and the calls that open, write and read it: each only in LaptopConnection.swift.
    static let clientFile = "LaptopConnection.swift"
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
    static func scan(_ roots: [URL], exceptions: [Exception] = exceptions) throws -> (findings: [Finding], inClient: [String: Int]) {
        var findings: [Finding] = []
        var inClient: [String: Int] = [:]
        for root in roots {
            guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey]) else {
                findings.append(Finding(file: root.lastPathComponent, line: 0, what: "a folder the scan cannot open")); continue
            }
            for case let url as URL in walker {
                let name = url.lastPathComponent
                // A folder is walked into; anything else that is not a regular file (a link, say) is not read through
                let kind = try? url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey])
                if kind?.isDirectory == true { continue }
                guard kind?.isRegularFile == true else {
                    findings.append(Finding(file: name, line: 0, what: "not a regular file or a folder")); continue
                }
                guard let language = SourceReader.language(of: url) else {
                    findings.append(Finding(file: name, line: 0, what: "a kind of file the scan cannot read")); continue
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
                    findings.append(Finding(file: name, line: token.line, what: token.text))
                }
                // An exception counts only while its line holds the very code it lists
                let sourceLines = text.components(separatedBy: "\n")
                let exempted: (Int) -> Bool = { line in
                    exempt(name, line, line >= 1 && line <= sourceLines.count ? sourceLines[line - 1] : "", in: exceptions)
                }
                findings += addressMaking(tokens, file: name, exempted: exempted)
                if let literals = try? SourceReader.split(text, language).literals {
                    findings += schemes(in: literals, file: name, exempted: exempted)
                }
                for pattern in addressReads {
                    for line in lines(of: pattern, in: tokens) where !exempted(line) { findings.append(Finding(file: name, line: line, what: pattern)) }
                }
                if name == clientFile {
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
    static func addressMaking(_ tokens: [SourceReader.Token], file: String, exempted: (Int) -> Bool) -> [Finding] {
        guard !transportFiles.contains(file) else { return [] }
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
    static func schemes(in literals: String, file: String, exempted: (Int) -> Bool) -> [Finding] {
        guard !transportFiles.contains(file) else { return [] }
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
    private func scanPlanted(_ files: [String: String], exceptions: [Exception] = []) throws -> [Finding] {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-scan-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for (name, text) in files {
            let url = folder.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(text.utf8).write(to: url)
        }
        return try Self.scan([folder], exceptions: exceptions).findings
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
        // A file the scan cannot read is a finding, never passed over
        let unreadable = try scanPlanted(["Notes.txt": "x", "Shader.swift": "let r = /a//; let s = URLSession.shared",
                                          "Open.swift": "let s = \"never closed\nlet t = 1", "Splice.c": "int URL\\\nSession;",
                                          "Regex.swift": "let r = #/a/#", "Interp.swift": "let s = \"\\(a\""])
        XCTAssertEqual(Set(unreadable.map(\.file)), ["Notes.txt", "Shader.swift", "Open.swift", "Splice.c", "Regex.swift", "Interp.swift"])
        XCTAssertTrue(unreadable.allSatisfy { $0.what.hasPrefix("unreadable") || $0.what == "a kind of file the scan cannot read" }, "\(unreadable)")
        // The connection's calls outside the client file
        let outside = try scanPlanted(["Other.swift": "func f(c: NWConnection) { c.send (content: d, completion: .idempotent) }"])
        XCTAssertEqual(outside.map(\.what), ["NWConnection", ".send(content:"])
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
