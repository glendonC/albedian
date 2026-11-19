import Foundation

/// D13, INTERIM for the owner's review (UIUX-iphone-kept-QUESTION): the words for photos kept and never sent.
public enum KeptWords {
    /// The home's pill, top left, only while photos are kept that were never sent: "3 not sent".
    public static func pill(_ n: Int) -> String { "\(WaitRow.Count.grouped(n)) not sent" }
    /// The line under the photo: "3 photos not sent".
    public static func line(_ n: Int) -> String { "\(WaitRow.Count.grouped(n)) \(n == 1 ? "photo" : "photos") not sent" }
    /// The sending row's word, in the wait row's place and type.
    public static let sending = "Sending"
    /// Photos sent of photos kept: "1 of 3".
    public static func count(sent: Int, of total: Int) -> String { "\(WaitRow.Count.grouped(sent)) of \(WaitRow.Count.grouped(total))" }
    /// How much of the line is filled: photos sent over photos kept.
    public static func fraction(sent: Int, of total: Int) -> Double {
        guard total > 0 else { return 0 }
        return min(max(Double(sent) / Double(total), 0), 1)
    }
}
