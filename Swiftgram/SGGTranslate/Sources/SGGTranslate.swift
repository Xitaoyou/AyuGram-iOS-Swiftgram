import Foundation
import SwiftSignalKit
import SwiftSoup

public enum TranslateFetchError {
    case network
}

private let sgTranslateSessionConfiguration: URLSessionConfiguration = .ephemeral
private let sgTranslateSession: URLSession = URLSession(configuration: sgTranslateSessionConfiguration)

public func getTranslateUrl(_ message: String, _ toLang: String) -> String {
    // AYG: translate.google.com/m no longer serves the `div.result-container`
    // this module used to scrape, so every fallback returned an empty string.
    // Use the JSON endpoint shipped by Swiftgram upstream instead.
    // Percent-encode everything except unreserved characters: .urlQueryAllowed
    // still lets &, =, + and ? through and corrupts ordinary chat text.
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
    let encoded = message.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
    return "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=\(toLang)&dt=t&ie=UTF-8&oe=UTF-8&q=\(encoded)"
}

private func prepareResultString(_ str: String) -> String {
    return str
}

public func parseTranslateResponse(_ data: String) -> String {
    guard let raw = data.data(using: .utf8),
          let root = (try? JSONSerialization.jsonObject(with: raw)) as? [Any],
          let blocks = root.first as? [Any] else {
        return ""
    }
    var result = ""
    for block in blocks {
        if let parts = block as? [Any], let part = parts.first as? String, !part.isEmpty {
            result += part
        }
    }
    return prepareResultString(result)
}

public func getGTranslateLang(_ userLang: String) -> String {
    var lang: String = userLang
    let rawSuffix: String = "-raw"
    if lang.hasSuffix(rawSuffix) {
        lang = String(lang.dropLast(rawSuffix.count))
    }
    lang = lang.lowercased()

    switch lang {
    case "zh-hans", "zh":
        return "zh-CN"
    case "zh-hant":
        return "zh-TW"
    case "he":
        return "iw"
    default:
        break
    }

    lang = lang.components(separatedBy: "-")[0].components(separatedBy: "_")[0]

    return lang
}

public func requestTranslateUrl(url: URL) -> Signal<String, TranslateFetchError> {
    return Signal { subscriber in
        let completed: Atomic<Bool> = Atomic(value: false)
        var request: URLRequest = URLRequest(url: url, timeoutInterval: 8.0)
        request.httpMethod = "GET"
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", forHTTPHeaderField: "User-Agent")
        let downloadTask: URLSessionDataTask = sgTranslateSession.dataTask(with: request, completionHandler: { data, response, _ in
            let _ = completed.swap(true)
            if let response: HTTPURLResponse = response as? HTTPURLResponse {
                if response.statusCode == 200 {
                    if let data: Data = data {
                        if let result: String = String(data: data, encoding: .utf8) {
                            subscriber.putNext(result)
                            subscriber.putCompletion()
                        } else {
                            subscriber.putError(.network)
                        }
                    } else {
                        subscriber.putError(.network)
                    }
                } else {
                    subscriber.putError(.network)
                }
            } else {
                subscriber.putError(.network)
            }
        })
        downloadTask.resume()

        return ActionDisposable {
            if !completed.with({ $0 }) {
                downloadTask.cancel()
            }
        }
    }
}

public func gtranslate(_ text: String, _ toLang: String) -> Signal<String, TranslateFetchError> {
    let lines: [String] = text.components(separatedBy: "\n")

    let translationSignals: [Signal<String, TranslateFetchError>] = lines.map { rawLine in
        let leadingWhitespace: Substring = rawLine.prefix { $0.isWhitespace }
        let core: String = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)

        if core.isEmpty {
            return .single(rawLine)
        }

        return gtranslateSentence(core, toLang)
        |> map { translatedCore in
            return String(leadingWhitespace) + translatedCore
        }
    }

    return combineLatest(translationSignals)
    |> map { results in
        let joined: String = results.joined(separator: "\n")
        return joined.isEmpty ? text : joined
    }
}

public func gtranslateSentence(_ text: String, _ toLang: String) -> Signal<String, TranslateFetchError> {
    return Signal { subscriber in
        let urlString: String = getTranslateUrl(text, getGTranslateLang(toLang))
        let url: URL = URL(string: urlString)!
        let translateSignal: Signal<String, TranslateFetchError> = requestTranslateUrl(url: url)
        var translateDisposable: Disposable?

        translateDisposable = translateSignal.start(next: { translatedHtml in
            let result: String = parseTranslateResponse(translatedHtml)
            if result.isEmpty {
                subscriber.putError(.network)
            } else {
                subscriber.putNext(result)
                subscriber.putCompletion()
            }
        }, error: { _ in
            subscriber.putError(.network)
        })

        return ActionDisposable {
            translateDisposable?.dispose()
        }
    }
}

public func gtranslateSplitTextBySentences(_ text: String, maxChunkLength: Int = 1500) -> [String] {
    if text.count <= maxChunkLength {
        return [text]
    }
    var chunks: [String] = []
    var currentChunk: String = ""

    text.enumerateSubstrings(in: text.startIndex ..< text.endIndex, options: .bySentences) { substring, _, _, _ in
        guard let sentence: String = substring else {
            return
        }

        if currentChunk.count + sentence.count + 1 < maxChunkLength {
            currentChunk += sentence + " "
        } else {
            if !currentChunk.isEmpty {
                chunks.append(currentChunk.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            currentChunk = sentence + " "
        }
    }

    if !currentChunk.isEmpty {
        chunks.append(currentChunk.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    return chunks
}
