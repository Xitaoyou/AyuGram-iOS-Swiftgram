import Foundation
import XCTest
import SwiftSignalKit
import Postbox
@testable import TelegramCore

/// `FinalStateReplayer.applyReplay` (state-sync seam 5), the one way a final state is replayed, driven directly over
/// the harness's Postbox without a state manager. An applied replay records the messages it deleted in the
/// `MessagesRemovedContext` it was given, inside its own transaction; a replay that `ReplayPolicy.verifiesTransaction`
/// refuses writes nothing and records nothing, and the channel reset's policy replays the same state unverified.
final class FinalStateReplayerTests: StateSyncTestCase {
    private func makeReplayer(_ messagesRemovedContext: MessagesRemovedContext) -> FinalStateReplayer {
        return FinalStateReplayer(accountManager: self.fixture.accountManager, postbox: self.fixture.postbox, accountPeerId: StateSyncFixture.accountPeerId, encryptionProvider: UnusedEncryptionProvider(), auxiliaryMethods: StateSyncFixture.makeAuxiliaryMethods(), messagesRemovedContext: messagesRemovedContext)
    }

    /// A final state built over a snapshot whose account state is `snapshot`: it deletes `id` and moves the account
    /// state to the next pts.
    private func finalState(snapshot: AuthorizedAccountState.State, deleting id: MessageId) -> AccountFinalState {
        let initialState = AccountInitialState(state: snapshot, peerIds: Set(), peerIdsRequiringLocalChatState: Set(), channelStates: [:], peerChatInfos: [:], locallyGeneratedMessageTimestamps: [:], channelsToPollExplicitely: Set())
        var state = AccountMutableState(initialState: initialState, initialPeers: [:], initialReferencedReplyMessageIds: ReferencedReplyMessageIds(), initialReferencedGeneralMessageIds: Set(), initialStoredMessages: Set(), initialStoredStories: [:], initialReadInboxMaxIds: [:], storedMessagesByPeerIdAndTimestamp: [:], initialSentScheduledMessageIds: Set())
        state.deleteMessages([id])
        state.updateState(AuthorizedAccountState.State(pts: snapshot.pts + 1, qts: snapshot.qts, date: snapshot.date, seq: snapshot.seq))
        return AccountFinalState(state: state, shouldPoll: false, incomplete: false, discard: false)
    }

    func testAnAppliedReplayRecordsWhatItDeleted() {
        self.fixture.seedAccount(state: self.state(pts: 100))
        let channel = self.seedChannel(300, pts: 50, storedMessageId: 10)
        let deletedId = self.messageId(channel, 10)
        let removed = MessagesRemovedContext()
        let replayer = self.makeReplayer(removed)
        let finalState = self.finalState(snapshot: self.state(pts: 100), deleting: deletedId)

        let deletedIds = self.fixture.transaction { transaction -> [DeletedMessageId]? in
            return replayer.applyReplay(finalState, policy: .verified, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction)?.deletedMessageIds
        } ?? nil

        XCTAssertEqual(deletedIds, [DeletedMessageId.messageId(deletedId)])
        XCTAssertNil(self.fixture.message(deletedId))
        XCTAssertEqual(self.fixture.accountState()?.state?.pts, 101)
        XCTAssertEqual(removed.synchronouslyIsMessageDeletedRemotely(ids: [deletedId]), [deletedId])
        XCTAssertEqual(removed.synchronouslyIsMessageDeletedInteractively(ids: [deletedId]), [deletedId])
    }

    func testAVerifiedReplayOfAStaleStateIsRefusedAndTheChannelResetPolicyReplaysIt() {
        self.fixture.seedAccount(state: self.state(pts: 105))
        let channel = self.seedChannel(300, pts: 50, storedMessageId: 10)
        let deletedId = self.messageId(channel, 10)
        let removed = MessagesRemovedContext()
        let replayer = self.makeReplayer(removed)
        // Built over pts 100; the stored account state has moved on to 105 since.
        let finalState = self.finalState(snapshot: self.state(pts: 100), deleting: deletedId)

        let refused = self.fixture.transaction { transaction -> Bool in
            return replayer.applyReplay(finalState, policy: .verified, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction) == nil
        }
        XCTAssertEqual(refused, true)
        XCTAssertNotNil(self.fixture.message(deletedId))
        XCTAssertEqual(self.fixture.accountState()?.state?.pts, 105)
        XCTAssertEqual(removed.synchronouslyIsMessageDeletedRemotely(ids: [deletedId]), [])
        XCTAssertEqual(removed.synchronouslyIsMessageDeletedInteractively(ids: [deletedId]), [])

        let deletedIds = self.fixture.transaction { transaction -> [DeletedMessageId]? in
            return replayer.applyReplay(finalState, policy: .unverifiedChannelReset, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction)?.deletedMessageIds
        } ?? nil
        XCTAssertEqual(deletedIds, [DeletedMessageId.messageId(deletedId)])
        XCTAssertNil(self.fixture.message(deletedId))
        XCTAssertEqual(removed.synchronouslyIsMessageDeletedRemotely(ids: [deletedId]), [deletedId])
    }
    func testServerReadStateCanReduceAnInflatedUnreadCount() {
        self.fixture.seedAccount(state: self.state(pts: 100))
        let peerId = self.seedUserChat(401)
        self.fixture.seedReadState(peerId: peerId, maxIncomingReadId: 10, maxOutgoingReadId: 0, maxKnownId: 30, count: 20)
        let initial = AccountInitialState(state: self.state(pts: 100), peerIds: Set(), peerIdsRequiringLocalChatState: Set(), channelStates: [:], peerChatInfos: [:], locallyGeneratedMessageTimestamps: [:], channelsToPollExplicitely: Set())
        var state = AccountMutableState(initialState: initial, initialPeers: [:], initialReferencedReplyMessageIds: ReferencedReplyMessageIds(), initialReferencedGeneralMessageIds: Set(), initialStoredMessages: Set(), initialStoredStories: [:], initialReadInboxMaxIds: [:], storedMessagesByPeerIdAndTimestamp: [:], initialSentScheduledMessageIds: Set())
        state.operations.append(.ResetReadState(peerId: peerId, namespace: Namespaces.Message.Cloud, maxIncomingReadId: 25, maxOutgoingReadId: 0, maxKnownId: 30, count: 5, markedUnread: false))
        let final = AccountFinalState(state: state, shouldPoll: false, incomplete: false, discard: false)
        let result = self.fixture.transaction { transaction in
            self.makeReplayer(MessagesRemovedContext()).applyReplay(final, policy: .verified, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction) != nil
        }
        XCTAssertEqual(result, true)
        let count = self.fixture.transaction { $0.getCombinedPeerReadState(peerId)?.count }
        XCTAssertEqual(count ?? nil, 5)
    }

    func testLocallyAdvancedReadStateDoesNotAdoptServerUnreadCount() {
        self.fixture.seedAccount(state: self.state(pts: 100))
        let peerId = self.seedUserChat(402)
        self.fixture.seedReadState(peerId: peerId, maxIncomingReadId: 25, maxOutgoingReadId: 0, maxKnownId: 30, count: 5)
        let initial = AccountInitialState(state: self.state(pts: 100), peerIds: Set(), peerIdsRequiringLocalChatState: Set(), channelStates: [:], peerChatInfos: [:], locallyGeneratedMessageTimestamps: [:], channelsToPollExplicitely: Set())
        var state = AccountMutableState(initialState: initial, initialPeers: [:], initialReferencedReplyMessageIds: ReferencedReplyMessageIds(), initialReferencedGeneralMessageIds: Set(), initialStoredMessages: Set(), initialStoredStories: [:], initialReadInboxMaxIds: [:], storedMessagesByPeerIdAndTimestamp: [:], initialSentScheduledMessageIds: Set())
        state.operations.append(.ResetReadState(peerId: peerId, namespace: Namespaces.Message.Cloud, maxIncomingReadId: 10, maxOutgoingReadId: 0, maxKnownId: 30, count: 20, markedUnread: false))
        let final = AccountFinalState(state: state, shouldPoll: false, incomplete: false, discard: false)
        let result = self.fixture.transaction { transaction in
            self.makeReplayer(MessagesRemovedContext()).applyReplay(final, policy: .verified, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction) != nil
        }
        XCTAssertEqual(result, true)
        let count = self.fixture.transaction { $0.getCombinedPeerReadState(peerId)?.count }
        XCTAssertEqual(count ?? nil, 5)
    }

    func testRepeatedForumMessagesAreCountedOnlyOnce() {
        self.fixture.seedAccount(state: self.state(pts: 100))
        let peerId = StateSyncFixture.channelPeerId(403)
        let channel = StateSyncFixture.makeChannel(id: peerId, accessHash: 1, title: "Forum", isBroadcast: false).withUpdatedFlags([.isForum])
        self.fixture.seedChannel(channel, state: ChannelState(pts: 50, invalidatedPts: nil, synchronizedUntilMessageId: nil))
        self.fixture.seedChat(peerId: peerId)
        self.fixture.seedReadState(peerId: peerId, maxIncomingReadId: 19, maxOutgoingReadId: 0, maxKnownId: 20, count: 1)
        func message(_ id: Int32) -> StoreMessage {
            return StoreMessage(id: MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: id), customStableId: nil, globallyUniqueId: nil, groupingKey: nil, threadId: 5, timestamp: self.baseDate, flags: [.Incoming], tags: [], globalTags: [], localTags: [], forwardInfo: nil, authorId: nil, text: "message", attributes: [], media: [])
        }
        self.fixture.transaction { transaction -> Void in
            let _ = transaction.addMessages([message(20)], location: .UpperHistoryBlock)
            let data = MessageHistoryThreadData(creationDate: self.baseDate, isOwnedByMe: false, author: peerId, info: EngineMessageHistoryThread.Info(title: "Topic", icon: nil, iconColor: 0x6FB9F0), incomingUnreadCount: 1, isMarkedUnread: false, maxIncomingReadId: 19, maxKnownMessageId: 20, maxOutgoingReadId: 0, isClosed: false, isHidden: false, notificationSettings: TelegramPeerNotificationSettings.defaultSettings, isMessageFeeRemoved: false)
            transaction.setMessageHistoryThreadInfo(peerId: peerId, threadId: 5, info: StoredMessageHistoryThreadInfo(data))
        }
        let initial = AccountInitialState(state: self.state(pts: 100), peerIds: Set(), peerIdsRequiringLocalChatState: Set(), channelStates: [:], peerChatInfos: [:], locallyGeneratedMessageTimestamps: [:], channelsToPollExplicitely: Set())
        var state = AccountMutableState(initialState: initial, initialPeers: [:], initialReferencedReplyMessageIds: ReferencedReplyMessageIds(), initialReferencedGeneralMessageIds: Set(), initialStoredMessages: Set(), initialStoredStories: [:], initialReadInboxMaxIds: [:], storedMessagesByPeerIdAndTimestamp: [:], initialSentScheduledMessageIds: Set())
        state.operations.append(.AddMessages([message(20), message(21), message(21)], .UpperHistoryBlock))
        let final = AccountFinalState(state: state, shouldPoll: false, incomplete: false, discard: false)
        let result = self.fixture.transaction { transaction in
            self.makeReplayer(MessagesRemovedContext()).applyReplay(final, policy: .verified, removePossiblyDeliveredMessagesUniqueIds: [:], transaction: transaction) != nil
        }
        XCTAssertEqual(result, true)
        let count = self.fixture.transaction { transaction in
            transaction.getMessageHistoryThreadInfo(peerId: peerId, threadId: 5)?.data.get(MessageHistoryThreadData.self)?.incomingUnreadCount
        }
        XCTAssertEqual(count ?? nil, 2)
    }

}
