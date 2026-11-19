import UIKit

/// D8 (UIUX-iphone-aim-QUESTION, ia-cam; decided by the lead under the owner's delegation of 19 November): "Open
/// Settings" under a refused camera opens the app's own page in the Settings app, Apple's way after a no. The one
/// address it opens is UIKit's constant for that page, never one of its own: the build check (`check-built.zsh`,
/// `settings_object`) lets only this file refer to UIApplication and its open call, and refuses it any address in text.
@MainActor
enum SettingsPage {
    static func open() {
        guard let page = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(page)
    }
}
