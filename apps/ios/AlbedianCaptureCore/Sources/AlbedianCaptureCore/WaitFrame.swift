import Foundation

/// largertext-wait-m (UIUX-iphone-largertext-sweep-READY item 1, DECIDED-BY-LEAD 24 Nov 19:56): at accessibility sizes the
/// wait's photo and its rings are drawn whole in a smaller 3:4 frame, its top kept and centred across, as tall as what is
/// left above the step row and its line, so the line ("Placed from where your iPhone pointed: predicted until matched.")
/// stands whole at every size. The frame is sized once for the text size, from the most the column can show, never from
/// what it shows.
public enum WaitFrame {
    /// The photo's top on every screen (arrive iPhone 2).
    public static let top = 118.0
    /// Between the photo's foot and the step row: 668 on the record's 390 pt screen, where the photo ends at 638.
    public static let gap = 30.0
    /// The room left at the screen's foot, under the line's last line (the record's picture: 34 pt).
    public static let foot = 34.0

    /// The photo's frame: at default sizes (`columnRoom` nil) the screen's width and 4:3 of it; at accessibility sizes,
    /// given the room the row and its line need, the tallest 3:4 frame whose foot leaves that room, the gap and the foot,
    /// never wider than the screen and never less than nothing.
    public static func size(screenWidth: Double, screenHeight: Double, columnRoom: Double?) -> (width: Double, height: Double) {
        guard let columnRoom else { return (screenWidth, screenWidth * 4 / 3) }
        let height = min(screenWidth * 4 / 3, max(0, screenHeight - foot - columnRoom - gap - top))
        return (height * 3 / 4, height)
    }

    /// The most the column can show, which the frame keeps room for, measured hidden and never shown: the longest step
    /// name, a count of five digits a side under it (more than a phone photo's steps count [inference]), and the early
    /// line's longest sentence.
    public static let mostName = (SolveJob.Stage.allCases.map(WaitRow.word) + [QueuedRow.word]).max { $0.count < $1.count } ?? ""
    public static let mostCount = WaitRow.Count.of(done: 88_888, total: 88_888).words(secondsPassed: 0) ?? ""
    public static let mostSentence = [PredictedEarlySky.Source.pointing, .moon, .sun].map(PredictedEarlySky.sentence(for:))
        .max { $0.count < $1.count } ?? ""
}
