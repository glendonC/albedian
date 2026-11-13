#!/bin/zsh
# The check of what was built (mobile-0, fourth version): it lists the system symbols and classes the built app and
# core refer to, and fails when one of its named sending families is among them, or when the connection's creating
# call is referred to from an object file other than LaptopConnection's. It reads Mach-O files: give it the built .app,
# object files, or folders holding them (each searched for Mach-O files).
#
#   xcodebuild -project apps/ios/AlbedianCapture.xcodeproj -scheme AlbedianCapture \
#       -destination 'generic/platform=iOS Simulator' -derivedDataPath <DerivedData> ENABLE_DEBUG_DYLIB=NO build
#   zsh apps/ios/scripts/check-built.zsh <DerivedData>/Build/Products/Debug-iphonesimulator/AlbedianCapture.app \
#       <DerivedData>/Build/Intermediates.noindex
#
# ENABLE_DEBUG_DYLIB=NO: a debug build otherwise wraps the app in Xcode's own launcher, which loads the app's code with
# dlopen and dlsym; with it off, the executable is the app's own linked code.
#
# What it reads, in each file: the undefined (imported) symbols, demangled (`nm -u`, `swift-demangle`), and the
# file's strings (`strings -a`), so a class or function looked up by its name while the app runs shows as text. Given
# the .app, it also reads four closed lists from it, each allowing what is listed and refusing the rest: the system
# libraries and frameworks its executables link (`otool -L`, `allowed_links`), the libraries they embed and link by
# @rpath/ or @executable_path/ (`allowed_embedded`, empty today), the entitlements it carries (`allowed_entitlements`:
# the app's identifier, its team and get-task-allow; no iCloud container, shared group, keychain sharing or push), and
# the keys of its Info.plist (`allowed_plist`: no address scheme, background mode or document type; and transport
# security only as local networking). It exits 1 with one line per distinct finding, 2 when it was given nothing to
# read, and 0 with the list of what the build refers to.
# The named families, and the address-making and address-reading calls refused in any object but the transport's,
# are lists: a call of a linked framework that makes or reads an address and is not named here is not seen by name.
# Of the four closed lists, every line `otool -L` gives for each Mach-O file in the .app that has its execute bit set
# (but a line naming the file itself, a universal file's architecture heading) is held against the system list or the
# embedded one, and every entitlement and Info.plist key against its own, so one that is not listed is a finding
# whatever it is called.
# What it cannot see: a name assembled from pieces at run time with no lookup function among the symbols (the lookup
# functions themselves are a family), and what Apple's own frameworks do inside.
set -u
setopt null_glob

families_demangled='(^|[^A-Za-z0-9_])(NSURLSession|NSURLSessionConfiguration|NSURLConnection|NSURLRequest|NSMutableURLRequest|NSURLDownload|NSStream|NSInputStream|NSOutputStream|NSNetService|NSNetServiceBrowser|WKWebView|WKWebsiteDataStore|SFSafariViewController|ASWebAuthenticationSession|UIActivityViewController|MFMailComposeViewController|MFMessageComposeViewController|UIDocumentInteractionController|UIPasteboard|NSPasteboard|MCSession|MCPeerID|MCNearbyServiceAdvertiser|MCNearbyServiceBrowser|MCAdvertiserAssistant|CKContainer|CKDatabase|NSUbiquitousKeyValueStore|CLGeocoder|MKMapView|MKLocalSearch|GKMatch|CBPeripheralManager|CBCentralManager|NSUserActivity|SKPaymentQueue|NWListener|NWBrowser|NWConnectionGroup|NWMulticastGroup|NWEthernetChannel|URLSession|URLRequest|ShareLink|PasteButton|OpenURLAction|AsyncImage|WebView|WebPage|Link|UIApplication|NSWorkspace|NSXMLParser|XMLParser|AVPlayer|AVQueuePlayer|AVURLAsset|LPMetadataProvider|LPLinkView|PHPhotoLibrary|PHAssetCreationRequest)([^A-Za-z0-9_]|$)'
families_c='^_(socket|connect|sendto|sendmsg|getaddrinfo|gethostbyname|gethostbyname2|res_query|dlsym|dlopen|NSClassFromString|NSSelectorFromString|objc_getClass|objc_lookUpClass|objc_getRequiredClass|CFHTTP.*|CFStream.*|CFReadStream.*|CFWriteStream.*|CFSocket.*|CFNetService.*|CFHost.*|nw_connection.*|nw_listener.*|nw_browser.*|DNSService.*|SCNetworkReachability.*|objc_msgSend\$openURL.*|objc_msgSend\$getStreamsToHost.*)$'
families_strings='(^|[^A-Za-z0-9_])(NSURLSession|NSURLConnection|NSURLRequest|NSURLDownload|NSInputStream|NSOutputStream|NSNetService|WKWebView|SFSafariViewController|ASWebAuthenticationSession|UIActivityViewController|MFMailComposeViewController|MFMessageComposeViewController|UIPasteboard|MCSession|CKContainer|CLGeocoder|getStreamsToHost|openURL:options:completionHandler:|CFStreamCreatePairWithSocketToHost|nw_connection_create|dataWithContentsOfURL|initWithContentsOfURL|stringWithContentsOfURL|NSXMLParser|LPMetadataProvider|PHPhotoLibrary)([^A-Za-z0-9_]|$)'
creating='(^|[^A-Za-z0-9_])NWConnection\.(__allocating_init|init)\('
client_object='LaptopConnection'
# Rule 1 in the built code: an address made from text, and the standard types' readers of an address, only in the
# transport's objects (or where `exceptions` lists the object and the call, with its reason in the README)
address_making='(^|[^A-Za-z0-9_])(URL\.init\(string:|URL\.init\(dataRepresentation:|URL\.init\(resolvingBookmarkData:|URLComponents|NSURL[^A-Za-z]|objc class NSURL$)'
address_reading='(^|[^A-Za-z0-9_])(Data\.init\(contentsOf:|String\.init\(contentsOf:|NSData|NSString\.init\(contentsOf)'
transport_objects=(LaptopConnection.o UploadClient.o)
exceptions=()
# The four closed lists, read from the .app
allowed_links=(
  /System/Library/Frameworks/Foundation.framework/Foundation /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation
  /System/Library/Frameworks/CryptoKit.framework/CryptoKit /System/Library/Frameworks/Network.framework/Network
  /System/Library/Frameworks/SwiftUI.framework/SwiftUI /System/Library/Frameworks/UIKit.framework/UIKit
  /System/Library/Frameworks/CoreGraphics.framework/CoreGraphics
  /System/Library/Frameworks/AVFoundation.framework/AVFoundation /System/Library/Frameworks/CoreMedia.framework/CoreMedia
  /System/Library/Frameworks/CoreMotion.framework/CoreMotion /System/Library/Frameworks/CoreLocation.framework/CoreLocation
  /System/Library/Frameworks/_LocationEssentials.framework/_LocationEssentials
  /usr/lib/libSystem.B.dylib /usr/lib/libobjc.A.dylib
  /usr/lib/swift/libswiftCore.dylib /usr/lib/swift/libswiftCoreFoundation.dylib /usr/lib/swift/libswiftCoreImage.dylib
  /usr/lib/swift/libswiftDispatch.dylib /usr/lib/swift/libswiftMetal.dylib /usr/lib/swift/libswiftOSLog.dylib
  /usr/lib/swift/libswiftObjectiveC.dylib /usr/lib/swift/libswiftObservation.dylib /usr/lib/swift/libswiftQuartzCore.dylib
  /usr/lib/swift/libswiftSpatial.dylib /usr/lib/swift/libswiftUniformTypeIdentifiers.dylib /usr/lib/swift/libswiftXPC.dylib
  /usr/lib/swift/libswift_Builtin_float.dylib /usr/lib/swift/libswift_Concurrency.dylib /usr/lib/swift/libswift_DarwinFoundation1.dylib
  /usr/lib/swift/libswiftos.dylib /usr/lib/swift/libswiftsimd.dylib
  /usr/lib/swift/libswiftAVFoundation.dylib /usr/lib/swift/libswiftCoreAudio.dylib /usr/lib/swift/libswiftCoreLocation.dylib
  /usr/lib/swift/libswiftCoreMIDI.dylib /usr/lib/swift/libswiftCoreMedia.dylib
)
# What the app embeds and links by @rpath/ or @executable_path/: empty, since the core package is linked into the
# executable and the Swift runtime comes from the system's /usr/lib/swift. Each entry is an install name with its reason.
allowed_embedded=()
allowed_entitlements=(application-identifier com.apple.developer.team-identifier get-task-allow)
allowed_plist=(
  BuildMachineOSBuild CFBundleDevelopmentRegion CFBundleDisplayName CFBundleExecutable CFBundleIdentifier
  CFBundleInfoDictionaryVersion CFBundleName CFBundlePackageType CFBundleShortVersionString CFBundleSupportedPlatforms
  CFBundleVersion DTCompiler DTPlatformBuild DTPlatformName DTPlatformVersion DTSDKBuild DTSDKName DTXcode DTXcodeBuild
  LSRequiresIPhoneOS MinimumOSVersion NSAppTransportSecurity UIApplicationSceneManifest UIApplicationSupportsIndirectInputEvents
  UIDeviceFamily UILaunchScreen UISupportedInterfaceOrientations UIRequiredDeviceCapabilities
  NSLocalNetworkUsageDescription NSCameraUsageDescription NSMotionUsageDescription NSLocationWhenInUseUsageDescription
)
allowed_ats='{"NSAllowsLocalNetworking":true}'

(( $# > 0 )) || { print -u2 "check-built: give it the built .app, object files or folders"; exit 2 }
files=()
for given in "$@"; do
  if [[ -d $given ]]; then
    while IFS= read -r f; do files+=("$f"); done < <(find "$given" -type f \( -name '*.o' -o -perm -u+x -o -name '*.dylib' \) 2>/dev/null)
  elif [[ -f $given ]]; then
    files+=("$given")
  else
    print -u2 "check-built: no such file or folder: $given"; exit 2
  fi
done
macho=()
for f in $files; do file -b "$f" | grep -q 'Mach-O' && macho+=("$f"); done
(( ${#macho} > 0 )) || { print -u2 "check-built: no Mach-O file among what was given"; exit 2 }

# One source file's object: an object file that is not a module's combined object (Xcode puts those under Binary/
# and in Build/Products); the app, a dylib and a combined object hold every file
single_object() { [[ $1 == *.o && $1 != */Binary/* && $1 != */Build/Products/* ]] }
findings=()
creators=()
all_symbols=()
notes=()
# The objects of single source files, read first: what the app's own code imports
objects_importing_dlsym=0
objects_read=0
for f in $macho; do
  if single_object "$f"; then
    objects_read=$((objects_read + 1))
    nm -u -j "$f" 2>/dev/null | grep -qx '_dlsym' && objects_importing_dlsym=$((objects_importing_dlsym + 1))
  fi
done
for f in $macho; do
  raw=(${(f)"$(nm -u -j "$f" 2>/dev/null)"})
  # In a linked binary, dlsym comes from the compiler's own runtime when the binary holds its OS version check
  # (clang_rt's os_version_check, reached from Swift's #available), which looks up the system's version function
  # by name; it is taken only when single-file objects were read too and none of them imports dlsym
  if ! single_object "$f" && (( objects_read > 0 && objects_importing_dlsym == 0 )) \
      && nm -U -j "$f" 2>/dev/null | grep -qx '___isPlatformVersionAtLeast' && print -l -- $raw | grep -qx '_dlsym'; then
    raw=(${raw:#_dlsym})
    notes+=("${f:t}: dlsym from the compiler runtime's OS version check; no object file of the app imports it")
  fi
  # An Objective-C class reference reads as "objc class <name>", so the name stands as a word
  demangled=(${(f)"$(print -l -- $raw | xcrun swift-demangle --simplified 2>/dev/null | sed -E 's/_OBJC_(META)?CLASS_\$_/objc class /')"})
  all_symbols+=($demangled)
  for s in $raw; do
    [[ $s =~ $families_c ]] && findings+=("${f:t}: imports $s")
  done
  for s in ${(f)"$(print -l -- $demangled | grep -E "$families_demangled")"}; do findings+=("${f:t}: refers to $s"); done
  # Only a single file's object counts for where the connection is created, and for rule 1
  if single_object "$f" && (( ${transport_objects[(Ie)${f:t}]} == 0 )); then
    for s in ${(f)"$(print -l -- $demangled | grep -E "$address_making")"}; do findings+=("${f:t}: makes an address from text: $s"); done
    for s in ${(f)"$(print -l -- $demangled | grep -E "$address_reading")"}; do
      call=${s%%\(*}
      (( ${exceptions[(Ie)${f:t}:$call]} )) || findings+=("${f:t}: reads an address's contents: $s")
    done
  fi
  if single_object "$f"; then
    print -l -- $demangled | grep -Eq "$creating" && creators+=("${f:t}")
    for s in $raw; do [[ $s == _nw_connection_create* ]] && creators+=("${f:t}"); done
  fi
  for word in ${(f)"$(strings -a "$f" 2>/dev/null | grep -Eo "$families_strings" | tr -cd 'A-Za-z0-9_:\n' | sort -u)"}; do
    findings+=("${f:t}: holds the text $word")
  done
done
creators=(${(u)creators})
for c in $creators; do
  [[ $c == ${client_object}* ]] || findings+=("$c: creates a connection outside $client_object")
done
objects_creating=(${(M)creators:#*.o})
(( ${#objects_creating} > 1 )) && findings+=("the connection is created from ${#objects_creating} object files: ${objects_creating[*]}")

# The four closed lists, from each .app given
for given in "$@"; do
  [[ -d $given && $given == *.app ]] || continue
  for f in ${(f)"$(find "$given" -type f -perm -u+x 2>/dev/null)"}; do
    file -b "$f" | grep -q 'Mach-O' || continue
    for lib in ${(f)"$(otool -L "$f" 2>/dev/null | tail -n +2 | awk '{print $1}')"}; do
      # A line naming the file itself is a universal file's architecture heading, not a link
      [[ $lib == "$f" ]] && continue
      if [[ $lib == @rpath/* || $lib == @executable_path/* ]]; then
        (( ${allowed_embedded[(Ie)$lib]} )) || findings+=("${given:t}: embeds and links $lib, not on the list")
      else
        (( ${allowed_links[(Ie)$lib]} )) || findings+=("${given:t}: links $lib, not on the list")
      fi
    done
  done
  executable=$given/$(plutil -extract CFBundleExecutable raw -o - "$given/Info.plist" 2>/dev/null)
  scratch=$(mktemp -d "${TMPDIR:-/tmp}/check-built.XXXXXX")
  arch=$(lipo -archs "$executable" 2>/dev/null | tr ' ' '\n' | grep -m1 arm64 || lipo -archs "$executable" 2>/dev/null | awk '{print $1}')
  otool -arch "$arch" -s __TEXT __entitlements "$executable" 2>/dev/null | perl -ne 'next unless s/^[0-9a-f]+\t//; print pack("V", hex($_)) for split' | tr -d '\000' > "$scratch/sim.plist"
  codesign -d --entitlements :- "$given" 2>/dev/null > "$scratch/signed.plist"
  for plist in "$scratch/sim.plist" "$scratch/signed.plist"; do
    [[ -s $plist ]] || continue
    for key in ${(f)"$(plutil -p "$plist" 2>/dev/null | sed -nE 's/^  "([^"]+)" =>.*/\1/p')"}; do
      (( ${allowed_entitlements[(Ie)$key]} )) || findings+=("${given:t}: carries the entitlement $key, not on the list")
    done
  done
  rm -rf "$scratch"
  for key in ${(f)"$(plutil -p "$given/Info.plist" 2>/dev/null | sed -nE 's/^  "([^"]+)" =>.*/\1/p')"}; do
    (( ${allowed_plist[(Ie)$key]} )) || findings+=("${given:t}: Info.plist has $key, not on the list")
  done
  ats=$(plutil -extract NSAppTransportSecurity json -o - "$given/Info.plist" 2>/dev/null)
  [[ -z $ats || $ats == "$allowed_ats" ]] || findings+=("${given:t}: Info.plist's transport security is $ats, not local networking alone")
done

print "check-built: read ${#macho} Mach-O files"
if (( ${#findings} > 0 )); then
  print -l -- "FOUND ${#findings}:" "${(@u)findings}"
  exit 1
fi
print "no sending family; the connection is created only in ${creators[*]:-no file}"
(( ${#notes} > 0 )) && print -l -- "${(@u)notes}"
print "what the build refers to from outside itself (imports, deduplicated across files):"
print "  Objective-C classes: $(print -l -- $all_symbols | sed -nE 's/^objc class ([A-Za-z0-9_]+)$/\1/p' | sort -u | tr '\n' ' ')"
print "  C functions: $(print -l -- $all_symbols | grep -E '^_[A-Za-z_][A-Za-z0-9_]*$' | sed 's/^_//' | sort -u | tr '\n' ' ')"
print "  Swift types: $(print -l -- $all_symbols | sed -nE 's/^(type metadata accessor for|type metadata for|nominal type descriptor for|protocol descriptor for|class metadata base offset for) ([A-Za-z0-9_.<>, ]+)$/\2/p' | sed -E 's/<.*//' | sort -u | tr '\n' ' ')"
exit 0
