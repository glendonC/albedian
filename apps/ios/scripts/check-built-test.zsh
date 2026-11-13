#!/bin/zsh
# The check of what was built, tried on what it must refuse: one call from each of nineteen of its named families and
# the last reader's eight address reads, each planted in a scratch object file of its own (compiled for the iPhone
# simulator, as the app is), a class named in text and one in Objective-C, the connection created outside its file and
# from two files, and an app off each closed list; and on what must pass: the transport making the paired address, an
# app on the lists, an embedded library on the list, the connection in its own file and a clean file. Exits 0 when
# every planted case is refused and each passing one passes.
#   zsh apps/ios/scripts/check-built-test.zsh
set -u
here=${0:A:h}
check=$here/check-built.zsh
scratch=$(mktemp -d "${TMPDIR:-/tmp}/albedian-check-built.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
sdk=$(xcrun --sdk iphonesimulator --show-sdk-path)
target=arm64-apple-ios26.0-simulator
failures=0

compile_swift() { # name, source
  print -r -- "$2" > "$scratch/$1.swift"
  xcrun --sdk iphonesimulator swiftc -parse-as-library -target $target -sdk "$sdk" -c "$scratch/$1.swift" -o "$scratch/$1.o" 2> "$scratch/$1.err" \
    || { print "COULD NOT BUILD $1: $(head -3 "$scratch/$1.err")"; failures=$((failures + 1)); return 1 }
}
compile_objc() { # name, source
  print -r -- "$2" > "$scratch/$1.m"
  xcrun --sdk iphonesimulator clang -fobjc-arc -target $target -isysroot "$sdk" -c "$scratch/$1.m" -o "$scratch/$1.o" 2> "$scratch/$1.err" \
    || { print "COULD NOT BUILD $1: $(head -3 "$scratch/$1.err")"; failures=$((failures + 1)); return 1 }
}
expect() { # refused|passes, name, word the finding must hold, files...
  local want=$1 name=$2 word=$3; shift 3
  out=$(zsh "$check" "$@" 2>&1); code=$?
  if [[ $want == refused ]]; then
    if (( code == 1 )) && print -r -- "$out" | grep -q -- "$word"; then print "refused  $name ($word)"
    else print "MISSED   $name: exit $code, $(print -r -- "$out" | tail -2 | tr '\n' ' ')"; failures=$((failures + 1)); fi
  else
    if (( code == 0 )); then print "passes   $name"
    else print "WRONGLY REFUSED $name: $(print -r -- "$out" | tail -3 | tr '\n' ' ')"; failures=$((failures + 1)); fi
  fi
}

typeset -A swift_planted
swift_planted=(
  URLSession      'import Foundation
public func f() { _ = URLSession.shared }'
  URLRequest      'import Foundation
public func f(_ u: URL) -> URLRequest { URLRequest(url: u) }'
  NWListener      'import Network
public func f() throws { _ = try NWListener(using: .tcp) }'
  socket          'import Darwin
public func f() -> Int32 { socket(AF_INET, SOCK_STREAM, 0) }'
  getaddrinfo     'import Darwin
public func f() { var r: UnsafeMutablePointer<addrinfo>?; _ = getaddrinfo("h", nil, nil, &r) }'
  NSStream        'import Foundation
public func f() { var i: InputStream?; var o: OutputStream?; Stream.getStreamsToHost(withName: "h", port: 1, inputStream: &i, outputStream: &o) }'
  CFStream        'import Foundation
public func f() { var r: Unmanaged<CFReadStream>?; var w: Unmanaged<CFWriteStream>?; CFStreamCreatePairWithSocketToHost(nil, "h" as CFString, 1, &r, &w) }'
  WKWebView       'import WebKit
@MainActor public func f() { _ = WKWebView() }'
  SFSafariViewController 'import SafariServices
@MainActor public func f(_ u: URL) { _ = SFSafariViewController(url: u) }'
  UIActivityViewController 'import UIKit
@MainActor public func f() { _ = UIActivityViewController(activityItems: [], applicationActivities: nil) }'
  UIPasteboard    'import UIKit
@MainActor public func f() { UIPasteboard.general.string = "x" }'
  openURL         'import UIKit
@MainActor public func f(_ u: URL) { UIApplication.shared.open(u) }'
  ShareLink       'import SwiftUI
public struct V: View { public var body: some View { ShareLink(item: "x") } }'
  MFMailComposeViewController 'import MessageUI
@MainActor public func f() { _ = MFMailComposeViewController() }'
  MCSession       'import MultipeerConnectivity
public func f() { _ = MCSession(peer: MCPeerID(displayName: "a")) }'
  CKContainer     'import CloudKit
public func f() { _ = CKContainer.default() }'
  CLGeocoder      'import CoreLocation
public func f() { _ = CLGeocoder() }'
  dlsym           'import Darwin
public func f() { _ = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "sock" + "et") }'
  NSClassFromString 'import Foundation
public func f() { _ = NSClassFromString("NSURL" + "Session") }'
)
for name in ${(k)swift_planted}; do
  compile_swift "planted_$name" "${swift_planted[$name]}" && expect refused "$name" "$name" "$scratch/planted_$name.o"
done
# Rule 1 in the built code, and the second net: the last reader's eight (AUDIT L, the fourth version)
typeset -A reads
reads=(
  DataRead      'import Foundation
public func f() throws -> Data { try Data(contentsOf: URL(string: "https://example.com/u?d=1")!) }'
  StringRead    'import Foundation
public func f(_ u: URL) throws -> String { try String(contentsOf: u, encoding: .utf8) }'
  NSDataRead    'import Foundation
public func f(_ u: URL) -> NSData? { NSData(contentsOf: u) }'
  XMLRead       'import Foundation
public func f(_ u: URL) -> XMLParser? { XMLParser(contentsOf: u) }'
  PlayerRead    'import AVFoundation
public func f(_ u: URL) -> AVPlayer { AVPlayer(url: u) }'
  PreviewRead   'import LinkPresentation
public func f(_ u: URL) { LPMetadataProvider().startFetchingMetadata(for: u) { _, _ in } }'
  LibraryChange 'import Photos
public func f() { PHPhotoLibrary.shared().performChanges({}, completionHandler: nil) }'
  Components    'import Foundation
public func f(_ h: String) -> URL? { var c = URLComponents(); c.scheme = "https"; c.host = h; return c.url }'
)
typeset -A read_word
read_word=(DataRead "makes an address from text" StringRead "reads an address" NSDataRead NSData XMLRead XMLParser PlayerRead AVPlayer
           PreviewRead LPMetadataProvider LibraryChange PHPhotoLibrary Components "makes an address from text")
for name in ${(k)reads}; do
  compile_swift "planted_$name" "${reads[$name]}" && expect refused "$name" "${read_word[$name]}" "$scratch/planted_$name.o"
done
# The transport's own object may make an address from text
compile_swift UploadClient 'import Foundation
public func f(_ t: String) -> URLComponents? { URLComponents(string: t) }' && expect passes "the transport making the paired address" - "$scratch/UploadClient.o"

# The closed lists, read from an app: a scratch .app of one executable and an Info.plist
make_app() { # name, swift source, extra linker flags, Info.plist body (keys inside <dict>)
  local app=$scratch/$1.app
  mkdir -p "$app"
  print -r -- "$2" > "$scratch/$1-main.swift"
  xcrun --sdk iphonesimulator swiftc -target $target -sdk "$sdk" "$scratch/$1-main.swift" -o "$app/$1" ${(z)3} 2> "$scratch/$1.err" \
    || { print "COULD NOT BUILD $1: $(head -3 "$scratch/$1.err")"; failures=$((failures + 1)); return 1 }
  print -r -- "<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"><plist version=\"1.0\"><dict><key>CFBundleExecutable</key><string>$1</string>$4</dict></plist>" > "$app/Info.plist"
}
plain_main='import Foundation
print(Date())'
make_app CleanApp "$plain_main" "" "<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>" \
  && expect passes "an app on the lists" - "$scratch/CleanApp.app"
make_app LinkApp 'import LinkPresentation
let p = LPMetadataProvider()' "" "" && expect refused "an app linking a framework off the list" "links /System/Library/Frameworks/LinkPresentation" "$scratch/LinkApp.app"
make_app SchemeApp "$plain_main" "" "<key>CFBundleURLTypes</key><array/>" && expect refused "an address scheme in Info.plist" "CFBundleURLTypes" "$scratch/SchemeApp.app"
make_app BackgroundApp "$plain_main" "" "<key>UIBackgroundModes</key><array><string>fetch</string></array>" && expect refused "a background mode" "UIBackgroundModes" "$scratch/BackgroundApp.app"
make_app ArbitraryApp "$plain_main" "" "<key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/></dict>" \
  && expect refused "a transport exception" "transport security" "$scratch/ArbitraryApp.app"
print -r -- '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>application-identifier</key><string>X.example</string><key>com.apple.developer.icloud-container-identifiers</key><array/></dict></plist>' > "$scratch/cloud.xcent"
make_app CloudApp "$plain_main" "-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __entitlements -Xlinker $scratch/cloud.xcent" "" \
  && expect refused "an iCloud entitlement" "com.apple.developer.icloud-container-identifiers" "$scratch/CloudApp.app"
# The fourth closed list: a library the app embeds and links by @rpath/ or @executable_path/
make_dylib() { # app name, module name, install name
  mkdir -p "$scratch/$1.app/Frameworks"
  print -r -- 'public func planted() {}' > "$scratch/$2.swift"
  xcrun --sdk iphonesimulator swiftc -target $target -sdk "$sdk" -emit-library -module-name $2 "$scratch/$2.swift" \
    -o "$scratch/$1.app/Frameworks/lib$2.dylib" -Xlinker -install_name -Xlinker "$3" 2> "$scratch/$2.err" \
    || { print "COULD NOT BUILD $2: $(head -3 "$scratch/$2.err")"; failures=$((failures + 1)); return 1 }
}
links_it() { # app name, install name: the app's executable really links it, so a pass is not for want of a link
  otool -L "$scratch/$1.app/$1" | awk '{print $1}' | grep -qx -- "$2" && return 0
  print "COULD NOT LINK $2 into $1"; failures=$((failures + 1)); return 1
}
make_dylib RpathApp Rpathed @rpath/libRpathed.dylib \
  && make_app RpathApp "$plain_main" "$scratch/RpathApp.app/Frameworks/libRpathed.dylib -Xlinker -rpath -Xlinker @executable_path/Frameworks" "" \
  && links_it RpathApp @rpath/libRpathed.dylib \
  && expect refused "an embedded library off the list, by @rpath/" "embeds and links @rpath/libRpathed.dylib" "$scratch/RpathApp.app"
make_dylib ExecApp Execed @executable_path/Frameworks/libExeced.dylib \
  && make_app ExecApp "$plain_main" "$scratch/ExecApp.app/Frameworks/libExeced.dylib" "" \
  && links_it ExecApp @executable_path/Frameworks/libExeced.dylib \
  && expect refused "an embedded library off the list, by @executable_path/" "embeds and links @executable_path/Frameworks/libExeced.dylib" "$scratch/ExecApp.app"
# Listed: the same kind of app passes a copy of the script whose list names its library, and only that line differs
sed 's#^allowed_embedded=()$#allowed_embedded=(@rpath/libListed.dylib)#' "$check" > "$scratch/check-listed.zsh"
if [[ $(diff "$check" "$scratch/check-listed.zsh" | grep -c '^[<>]') != 2 ]]; then
  print "COULD NOT LIST libListed in a copy of the script"; failures=$((failures + 1))
else
  make_dylib ListedApp Listed @rpath/libListed.dylib \
    && make_app ListedApp "$plain_main" "$scratch/ListedApp.app/Frameworks/libListed.dylib -Xlinker -rpath -Xlinker @executable_path/Frameworks" "" \
    && links_it ListedApp @rpath/libListed.dylib \
    && { saved=$check; check=$scratch/check-listed.zsh; expect passes "an embedded library on the list" - "$scratch/ListedApp.app"; check=$saved; }
fi

# A class named by its text alone, with the lookup hidden behind a symbol the list does not hold: the text is seen
compile_swift planted_text 'import Foundation
public let name = "NSURLSession"' && expect refused "a class named in text" NSURLSession "$scratch/planted_text.o"
compile_objc planted_objc '#import <Foundation/Foundation.h>
void f(void) { [[NSURLSession sharedSession] dataTaskWithURL:[NSURL URLWithString:@"x"]]; }' && expect refused "Objective-C" NSURLSession "$scratch/planted_objc.o"
# The connection created outside its file, and from two files
connection='import Network
public func open() -> NWConnection { NWConnection(host: "127.0.0.1", port: 8000, using: .tcp) }'
compile_swift LaptopConnection "$connection" && expect passes "the connection in its own file" - "$scratch/LaptopConnection.o"
compile_swift Other "$connection" && expect refused "the connection created elsewhere" "outside LaptopConnection" "$scratch/Other.o"
mkdir -p "$scratch/two" && cp "$scratch/LaptopConnection.o" "$scratch/two/LaptopConnection.o" && cp "$scratch/LaptopConnection.o" "$scratch/two/LaptopConnection2.o"
expect refused "the connection created from two files" "2 object files" "$scratch/two"
# Clean: what the app does use
compile_swift clean 'import Foundation
import CryptoKit
public func f(_ d: Data, folder: URL) throws -> String {
  try d.write(to: folder.appendingPathComponent("a.bin"))
  return SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() + URL(fileURLWithPath: "/tmp").path
}' \
  && expect passes "a clean file" - "$scratch/clean.o"
expect_given_nothing=$(zsh "$check" "$scratch/nothing-here" 2>&1); (( $? == 2 )) && print "refused  nothing to read (exit 2)" || { print "MISSED   nothing to read"; failures=$((failures + 1)); }
print "check-built-test: $failures failures"
exit $(( failures > 0 ))
