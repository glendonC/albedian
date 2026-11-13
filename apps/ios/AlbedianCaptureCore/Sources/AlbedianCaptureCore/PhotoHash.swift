import CryptoKit
import Foundation

/// The photo's SHA-256 over the exact bytes the camera wrote and the app sends (design component 3), with CryptoKit
/// (`cryptokit/sha256`), as 64 lower-case hex characters: the key the laptop's ingest store names the file by.
public enum PhotoHash {
    public static func sha256Hex(_ bytes: Data) -> String {
        let hex = Array("0123456789abcdef".utf8)
        var out: [UInt8] = []
        out.reserveCapacity(64)
        for byte in SHA256.hash(data: bytes) {
            out.append(hex[Int(byte >> 4)])
            out.append(hex[Int(byte & 0x0F)])
        }
        return String(decoding: out, as: UTF8.self)
    }

    /// Whether text is a SHA-256 as the contract takes it: 64 lower-case hex characters, not all zeros.
    public static func isContractHash(_ text: String) -> Bool {
        let units = Array(text.utf16)
        guard units.count == 64 else { return false }
        guard units.allSatisfy({ (0x30...0x39).contains($0) || (0x61...0x66).contains($0) }) else { return false }
        return units.contains { $0 != 0x30 }
    }
}
