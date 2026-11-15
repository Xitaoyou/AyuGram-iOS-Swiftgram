import XCTest
@testable import TelegramCore

final class DownloadRequestBudgetTests: XCTestCase {
    func testPrefetchLeavesRoomForPlayback() {
        let budget = DownloadRequestBudget(capacity: 24)
        XCTAssertFalse(budget.allows(active: 22, priority: 0, now: 100))
        XCTAssertFalse(budget.allows(active: 22, priority: 1, now: 100))
        XCTAssertTrue(budget.allows(active: 22, priority: 2, now: 100))
        XCTAssertFalse(budget.allows(active: 24, priority: 2, now: 100))
    }

    func testFloodWaitPausesAllFilesAndCoalescesTheBurst() {
        var budget = DownloadRequestBudget(capacity: 24)
        XCTAssertTrue(budget.recordFloodWait("FLOOD_PREMIUM_WAIT_5", active: 16, now: 100))
        XCTAssertEqual(budget.limit, 8)
        XCTAssertTrue(budget.recordFloodWait("FLOOD_PREMIUM_WAIT_5", active: 16, now: 100.1))
        XCTAssertEqual(budget.limit, 8)
        XCTAssertFalse(budget.allows(active: 0, priority: 2, now: 105))
        XCTAssertTrue(budget.allows(active: 0, priority: 2, now: 105.1))
        budget.recordSuccess(now: 105.1)
        XCTAssertEqual(budget.limit, 8)
        budget.recordSuccess(now: 120.1)
        XCTAssertEqual(budget.limit, 9)
    }

    func testZeroWaitHasABoundedCooldownAndInvalidErrorsDoNotChangeBudget() {
        var budget = DownloadRequestBudget(capacity: 8)
        XCTAssertFalse(budget.recordFloodWait("FLOOD_WAIT_10", active: 8, now: 100))
        XCTAssertFalse(budget.recordFloodWait("FLOOD_PREMIUM_WAIT_nan", active: 8, now: 100))
        XCTAssertTrue(budget.recordFloodWait("FLOOD_PREMIUM_WAIT_0", active: 8, now: 100))
        XCTAssertFalse(budget.allows(active: 0, priority: 2, now: 100.9))
        XCTAssertTrue(budget.allows(active: 0, priority: 2, now: 101))
    }
}
