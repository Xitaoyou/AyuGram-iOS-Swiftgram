import Foundation

@main
struct UpdateManifestTests {
    static func expect(_ value: @autoclosure () -> Bool, _ text: String) { precondition(value(), text) }
    static func data(_ json: String) -> Data { json.data(using: .utf8)! }

    static func main() {
        let newer = AyuGramUpdates.parse(data(#"{"build": 200301, "version": "12.6", "url": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-200301"}"#), currentBuild: 200201)
        expect(newer == AyuGramUpdateInfo(build: 200301, version: "12.6", url: "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-200301"), "A newer build is offered")
        expect(AyuGramUpdates.parse(data(#"{"build": 200201, "url": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-300000"}"#), currentBuild: 200201) == nil, "The same build is not offered")
        expect(AyuGramUpdates.parse(data(#"{"build": 100, "url": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-300000"}"#), currentBuild: 200201) == nil, "An older build is not offered")
        expect(AyuGramUpdates.parse(data(#"{"build": 300000, "url": "http://t.me/x"}"#), currentBuild: 1) == nil, "Only https links are accepted")
        expect(AyuGramUpdates.parse(data(#"{"build": "300000", "url": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-300000"}"#), currentBuild: 1)?.version == "300000", "A string build works and names the version")
        expect(AyuGramUpdates.parse(data("not json"), currentBuild: 1) == nil, "A broken manifest is ignored")
        let installable = AyuGramUpdates.parse(data(#"{"build": 300000, "url": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/build-300000", "ipa": {"standard": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/download/build-300000/a.ipa", "custom": "http://insecure/b.ipa"}}"#), currentBuild: 1)
        expect(installable?.ipa == ["standard": "https://github.com/Xitaoyou/AyuGram-iOS-Swiftgram/releases/download/build-300000/a.ipa"], "IPA links are read per variant, https only")
        expect(AyuGramUpdates.parse(data(#"{"build":300000,"url":"https://github.com/mapix3/AyuGram-iOS/releases/tag/build-300000"}"#), currentBuild: 1) == nil, "Upstream releases are rejected")
        expect(AyuGramUpdates.parse(data(#"{"build":300000,"url":"https://github.com.evil.example/Xitaoyou/AyuGram-iOS-Swiftgram/releases/tag/x"}"#), currentBuild: 1) == nil, "Lookalike hosts are rejected")
        print("Update manifest: newer builds only, https links only")
    }
}
