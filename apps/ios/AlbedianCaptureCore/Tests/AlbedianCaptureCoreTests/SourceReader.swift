import Foundation

/// A reading of source text for the privacy scan: comments and the contents of string and character literals are
/// taken out by reading the syntax (not by cutting lines), every line break is kept so line numbers hold, and what is
/// left is split into tokens. Swift: line comments, nested block comments, single-line, multi-line and raw strings,
/// with interpolated code kept as code. The C family (C, C++, Objective-C, Metal): line and block comments, string,
/// @-string and character literals. Whatever it cannot read for certain (a regex literal, a C++ raw string, a line
/// spliced with a backslash, a trigraph, a comment or literal left open) it refuses, naming the line.
enum SourceReader {
    enum Language: Equatable { case swift, cFamily }

    struct Unreadable: Error, Equatable {
        let line: Int
        let why: String
    }

    struct Token: Equatable {
        let text: String
        let line: Int
    }

    /// The language of a file the scan can read, by its extension; nil for any other kind of file.
    static func language(of url: URL) -> Language? {
        switch url.pathExtension.lowercased() {
        case "swift": .swift
        case "c", "h", "m", "mm", "cc", "cpp", "cxx", "hpp", "hh", "metal": .cFamily
        default: nil
        }
    }

    /// The code: comments and literal contents replaced by spaces, line breaks kept.
    static func code(_ text: String, _ language: Language) throws(Unreadable) -> String { try split(text, language).code }

    /// The code, and beside it the contents of the string and character literals (everything else replaced by spaces,
    /// line breaks kept), so a literal can be read without being taken for code.
    static func split(_ text: String, _ language: Language) throws(Unreadable) -> (code: String, literals: String) {
        let scalars = Array(text.unicodeScalars)
        switch language {
        case .swift: return try swiftCode(scalars)
        case .cFamily: return try cCode(scalars)
        }
    }

    /// The tokens of code: identifiers (letters, digits, `_` and `$`, not starting with a digit), numbers as one token
    /// each, and every other character that is not white space as a token of its own. A backtick is a token, so a
    /// backticked name reads as the name.
    static func tokens(_ code: String) -> [Token] {
        var tokens: [Token] = []
        var line = 1
        var current = ""
        var currentLine = 1
        func flush() {
            if !current.isEmpty { tokens.append(Token(text: current, line: currentLine)); current = "" }
        }
        for scalar in code.unicodeScalars {
            if isIdentifierPart(scalar) {
                if current.isEmpty { currentLine = line }
                current.unicodeScalars.append(scalar)
                continue
            }
            flush()
            if scalar == "\n" { line += 1; continue }
            if CharacterSet.whitespaces.contains(scalar) || scalar == "\r" || scalar == "\t" { continue }
            tokens.append(Token(text: String(scalar), line: line))
        }
        flush()
        return tokens
    }

    static func isIdentifierPart(_ scalar: Unicode.Scalar) -> Bool {
        scalar == "_" || scalar == "$" || CharacterSet.alphanumerics.contains(scalar)
    }

    // MARK: Swift

    private struct StringKind {
        let hashes: Int
        let multiline: Bool
    }

    private enum Context {
        /// Code; `interpolation` is the string it sits in, with the parentheses opened inside it.
        case code(interpolation: StringKind?, depth: Int)
        case string(StringKind)
        case blockComment(depth: Int)
    }

    private static func swiftCode(_ s: [Unicode.Scalar]) throws(Unreadable) -> (code: String, literals: String) {
        var out = String.UnicodeScalarView()
        var lit = String.UnicodeScalarView()
        var stack: [Context] = [.code(interpolation: nil, depth: 0)]
        var line = 1
        var i = 0
        func at(_ k: Int) -> Unicode.Scalar? { k < s.count ? s[k] : nil }
        func starts(_ text: String, _ k: Int) -> Bool {
            var j = k
            for c in text.unicodeScalars { guard at(j) == c else { return false }; j += 1 }
            return true
        }
        func blank(_ count: Int, literal: Bool = false) {
            for k in i..<min(i + count, s.count) {
                if s[k] == "\n" { out.append("\n"); lit.append("\n"); line += 1 } else { out.append(" "); lit.append(literal ? s[k] : " ") }
            }
            i += count
        }
        func emit(_ c: Unicode.Scalar) {
            out.append(c); lit.append(c == "\n" ? "\n" : " "); i += 1
        }
        func hashes(_ k: Int) -> Int { var n = 0; while at(k + n) == "#" { n += 1 }; return n }
        /// Whether a slash here may begin a regex literal: after an operator, an opening bracket, a comma, a colon, a
        /// keyword that takes an expression, or at a line's start; not after a name, a number or a closing bracket.
        func slashMayBeginRegex() -> Bool {
            let view = Array(out)
            var k = view.count - 1
            while k >= 0, view[k] == " " || view[k] == "\t" || view[k] == "\r" { k -= 1 }
            guard k >= 0, view[k] != "\n" else { return true }
            if "([{,:;=!&|?+-*%<>^~".unicodeScalars.contains(view[k]) { return true }
            guard isIdentifierPart(view[k]) else { return false }
            var word = String.UnicodeScalarView()
            while k >= 0, isIdentifierPart(view[k]) { word.insert(view[k], at: word.startIndex); k -= 1 }
            return ["return", "case", "in", "where", "if", "guard", "while", "throw", "try", "await", "else", "switch", "is", "as",
                    "repeat", "yield", "then"].contains(String(word))
        }
        while i < s.count {
            switch stack[stack.count - 1] {
            case .blockComment(let depth):
                if starts("/*", i) { stack[stack.count - 1] = .blockComment(depth: depth + 1); blank(2) }
                else if starts("*/", i) {
                    if depth == 1 { stack.removeLast() } else { stack[stack.count - 1] = .blockComment(depth: depth - 1) }
                    blank(2)
                } else { blank(1) }
            case .string(let kind):
                let c = s[i]
                if c == "\\" && hashes(i + 1) == kind.hashes {
                    let next = i + 1 + kind.hashes
                    if at(next) == "(" {
                        blank(kind.hashes + 2)
                        stack.append(.code(interpolation: kind, depth: 0))
                    } else {
                        // An escape: the backslash, its hashes and the character after them. In the literals' view it
                        // stands as the one character it means, so a scheme spelled with escapes still reads as one
                        let mark = lit.count
                        blank(kind.hashes + 1)
                        var meant: Unicode.Scalar = " "
                        if at(i) == "u", at(i + 1) == "{" {
                            let start = i
                            while i < s.count, s[i] != "}" { if s[i] == "\n" { throw Unreadable(line: line, why: "an escape left open") }; blank(1) }
                            blank(1)
                            let digits = String(String.UnicodeScalarView(s[(start + 2)..<max(start + 2, i - 1)]))
                            if let value = UInt32(digits, radix: 16), let scalar = Unicode.Scalar(value), scalar != "\n" { meant = scalar }
                        } else if i < s.count {
                            let escaped = s[i]
                            if escaped != "\n" && !"ntr0".unicodeScalars.contains(escaped) { meant = escaped }
                            blank(1)
                        }
                        // Keep any line break the escape spanned, so line numbers hold
                        let breaks = lit[lit.index(lit.startIndex, offsetBy: mark)...].filter { $0 == "\n" }.count
                        lit.removeSubrange(lit.index(lit.startIndex, offsetBy: mark)...)
                        lit.append(meant)
                        for _ in 0..<breaks { lit.append("\n") }
                    }
                } else if kind.multiline && starts("\"\"\"", i) && hashes(i + 3) >= kind.hashes {
                    stack.removeLast(); blank(3 + kind.hashes)
                } else if !kind.multiline && c == "\"" && hashes(i + 1) >= kind.hashes {
                    stack.removeLast(); blank(1 + kind.hashes)
                } else if !kind.multiline && c == "\n" {
                    throw Unreadable(line: line, why: "a string not closed on its line")
                } else { blank(1, literal: true) }
            case .code(let interpolation, let depth):
                let c = s[i]
                if starts("//", i) {
                    while i < s.count, s[i] != "\n" { blank(1) }
                } else if starts("/*", i) {
                    stack.append(.blockComment(depth: 1)); blank(2)
                } else if c == "#" || c == "\"" {
                    let n = c == "#" ? hashes(i) : 0
                    if at(i + n) == "\"" {
                        let multiline = starts("\"\"\"", i + n)
                        stack.append(.string(StringKind(hashes: n, multiline: multiline)))
                        blank(n + (multiline ? 3 : 1))
                    } else if c == "#" && at(i + n) == "/" {
                        throw Unreadable(line: line, why: "a regex literal")
                    } else {
                        emit(c)
                    }
                } else if c == "/" && !starts("/=", i) {
                    // A slash after an operator, an opening bracket, a comma, a colon or at a line's start begins a
                    // regex literal (Swift 6 reads bare slashes as one); one after a name, a number or a closing
                    // bracket is division
                    if slashMayBeginRegex() && at(i + 1) != " " && at(i + 1) != nil { throw Unreadable(line: line, why: "a regex literal") }
                    emit(c)
                } else if c == "(" {
                    if interpolation != nil { stack[stack.count - 1] = .code(interpolation: interpolation, depth: depth + 1) }
                    emit(c)
                } else if c == ")" && interpolation != nil {
                    if depth == 0 { stack.removeLast(); blank(1) }
                    else { stack[stack.count - 1] = .code(interpolation: interpolation, depth: depth - 1); emit(c) }
                } else {
                    if c == "\n" { line += 1 }
                    emit(c)
                }
            }
        }
        guard stack.count == 1 else { throw Unreadable(line: line, why: "the file ends inside a comment, a string or an interpolation") }
        return (String(out), String(lit))
    }

    // MARK: C family

    private static func cCode(_ s: [Unicode.Scalar]) throws(Unreadable) -> (code: String, literals: String) {
        var out = String.UnicodeScalarView()
        var lit = String.UnicodeScalarView()
        var line = 1
        var i = 0
        func at(_ k: Int) -> Unicode.Scalar? { k < s.count ? s[k] : nil }
        func blank(literal: Bool = false) {
            if s[i] == "\n" { out.append("\n"); lit.append("\n"); line += 1 } else { out.append(" "); lit.append(literal ? s[i] : " ") }
            i += 1
        }
        func emit(_ c: Unicode.Scalar) {
            out.append(c); lit.append(c == "\n" ? "\n" : " "); i += 1
        }
        // A backslash at a line's end splices two lines into one, anywhere: refused, so no name is hidden across lines
        for (index, c) in s.enumerated() where c == "\\" {
            var k = index + 1
            while k < s.count, s[k] == " " || s[k] == "\t" || s[k] == "\r" { k += 1 }
            if k == s.count || s[k] == "\n" {
                throw Unreadable(line: s[..<index].filter { $0 == "\n" }.count + 1, why: "a line spliced with a backslash")
            }
        }
        while i < s.count {
            let c = s[i]
            if c == "?" && at(i + 1) == "?" { throw Unreadable(line: line, why: "a trigraph") }
            if c == "/" && at(i + 1) == "/" {
                while i < s.count, s[i] != "\n" { blank() }
            } else if c == "/" && at(i + 1) == "*" {
                blank(); blank()
                while true {
                    guard i < s.count else { throw Unreadable(line: line, why: "a comment left open") }
                    if s[i] == "*" && at(i + 1) == "/" { blank(); blank(); break }
                    blank()
                }
            } else if c == "R" && at(i + 1) == "\"" && (i == 0 || !isIdentifierPart(s[i - 1])) {
                throw Unreadable(line: line, why: "a C++ raw string")
            } else if c == "\"" || c == "'" {
                let quote = c
                blank()
                while true {
                    guard i < s.count, s[i] != "\n" else { throw Unreadable(line: line, why: "a literal not closed on its line") }
                    if s[i] == "\\" { blank(); if i < s.count { blank() }; continue }
                    if s[i] == quote { blank(); break }
                    blank(literal: true)
                }
            } else {
                if c == "\n" { line += 1 }
                emit(c)
            }
        }
        return (String(out), String(lit))
    }
}
