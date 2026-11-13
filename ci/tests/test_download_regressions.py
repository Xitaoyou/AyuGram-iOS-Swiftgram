import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DownloadRegressionTests(unittest.TestCase):
    """Keep the download root-cause fixes from silently disappearing.

    Every assertion below corresponds to a symptom that was reproduced on a
    device: a "Subscribe to Premium" toast while batch-loading media, a video
    stuck on its blurred placeholder at 50% progress, and a download speed
    setting that made batches slower the higher it was set.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def fetch_v2(self):
        return self.text('submodules/TelegramCore/Sources/Network/FetchV2.swift')

    def fetch_manager(self):
        return self.text('submodules/FetchManagerImpl/Sources/FetchManagerImpl.swift')

    def test_server_throttle_backs_off_instead_of_upselling(self):
        source = self.fetch_v2()
        handler = source.split('private func processFloodWaitError', 1)[1]
        handler = handler.split('private func ', 1)[0]
        self.assertIn('error.hasPrefix("FLOOD_PREMIUM_WAIT")', handler)
        self.assertIn('state.window = max(fastDownloadMinWindow, state.window / 2)', handler)
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)

    def test_speed_setting_is_a_ceiling_on_the_rust_engine(self):
        # The stock code bypassed getSGMaxPendingParts entirely when
        # fastDownloads was on, so the setting never capped concurrency.
        self.assertIn('min(fastDownloadMasterPendingParts, pendingParts)', self.fetch_v2())
        settings = self.text('Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift')
        self.assertIn('return min(`default` + 2, 8)', settings)
        self.assertIn('return min(`default`, 4)', settings)

    def test_opened_video_parts_are_prioritised(self):
        gallery = self.text('submodules/GalleryUI/Sources/Items/UniversalVideoGalleryItem.swift')
        self.assertIn('fetchPriorityDisposable.set(item.context.engine.resources.pushPriorityDownload', gallery)
        self.assertIn('self.fetchPriorityDisposable.dispose()', gallery)

    def test_prefetch_leaves_queue_headroom(self):
        prefetch = self.text('submodules/TelegramUI/Sources/InChatPrefetchManager.swift')
        self.assertIn('self.contexts.count >= 2', prefetch)

    def test_category_scheduler_is_not_single_active(self):
        source = self.fetch_manager()
        self.assertIn('private let maxActiveEntries = 3', source)
        self.assertIn('let _ = self.activeEntryIds.insert(topEntryId)', source)
        self.assertIn('let _ = self.activeEntryIds.remove(id)', source)
        self.assertIn('activeEntryIds.contains(id)', source)

    def test_user_opened_downloads_use_the_highest_priority_lane(self):
        # Both activation paths must keep this; missing one reintroduces the
        # "50% progress but still blurred" behaviour.
        source = self.fetch_manager()
        self.assertEqual(
            source.count('entry.userInitiated ? [(0 ..< Int64.max, .maximum)] : nil'),
            2,
        )
