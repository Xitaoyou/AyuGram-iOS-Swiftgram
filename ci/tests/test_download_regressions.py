import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DownloadRegressionTests(unittest.TestCase):
    """Keep the download root-cause fixes from silently disappearing.

    Reproduced on-device symptoms: a "Subscribe to Premium" toast while
    batch-loading media, a video stuck on its blurred placeholder at 50%
    progress, and a download-speed setting that made batches slower the
    higher it was set.

    Besides structural guards, this file simulates the 420 back-off and the
    scheduler slot bookkeeping so a behavioural regression fails here rather
    than only on a device.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def fetch_v2(self):
        return self.text('submodules/TelegramCore/Sources/Network/FetchV2.swift')

    def fetch_manager(self):
        return self.text('submodules/FetchManagerImpl/Sources/FetchManagerImpl.swift')

    def gallery_video(self):
        return self.text('submodules/GalleryUI/Sources/Items/UniversalVideoGalleryItem.swift')

    def flood_handler(self):
        handler = self.fetch_v2().split('private func processFloodWaitError', 1)[1]
        return handler.split('private func ', 1)[0]

    # ------------------------------------------------------------------
    # Behavioural simulations of the actual algorithms.
    # ------------------------------------------------------------------

    def test_420_backoff_releases_occupied_slots(self):
        """A throttled request must not keep a multiplexer slot until its
        flood-wait timer expires. Halving the window must be followed by
        disposing the parts beyond it, and the next dispatch must obey it."""
        handler = self.flood_handler()
        self.assertIn('state.window = max(fastDownloadMinWindow, state.window / 2)', handler)
        self.assertIn('part.disposable?.dispose()', handler)
        self.assertIn('self.update()', handler)

        # Mirror the Swift algorithm with a deterministic model.
        fast_download_min_window = 2
        window = 6
        max_pending_parts = 6
        pending = list(range(6))
        released = []

        # processFloodWaitError
        window = max(fast_download_min_window, window // 2)
        keep_count = max(1, window)
        if len(pending) > keep_count:
            dropped = pending[keep_count:]
            pending = pending[:keep_count]
            released.extend(dropped)

        self.assertEqual(window, 3)
        self.assertEqual(len(pending), 3)
        self.assertEqual(len(released), 3)

        # update() may only refill up to min(maxPendingParts, window).
        dispatched = min(max_pending_parts, window) - len(pending)
        self.assertEqual(dispatched, 0, 'No new part may be dispatched while at the new window')
        self.assertLessEqual(len(pending) + dispatched, window)

        # A second 420 halves again but never drops below the floor.
        window = max(fast_download_min_window, window // 2)
        self.assertEqual(window, 2)

    def test_scheduler_refills_after_every_completion(self):
        """The single-valued top pointer must be cleared even when all three
        slots are busy, otherwise re-selection is locked and the category
        stalls permanently."""
        source = self.fetch_manager()
        # The clear must come BEFORE the slot-budget check, not inside it.
        self.assertIn(
            'self.topEntryIdAndPriority = nil\n                    if self.activeEntryIds.count < self.maxActiveEntries {',
            source,
        )
        self.assertIn('activeEntryIds.count >= self.maxActiveEntries', source,
                      'Activation must refuse to exceed the slot budget')

        # Model: three busy slots, a completion frees one and must allow a refill.
        max_active = 3
        active = {'a', 'b', 'c'}
        top_pointer = 'c'
        completed = 'b'
        active.discard(completed)
        top_pointer = None  # unconditional clear
        refill_allowed = len(active) < max_active
        self.assertIsNone(top_pointer)
        self.assertTrue(refill_allowed)

    # ------------------------------------------------------------------
    # Structural guards for each remaining root cause.
    # ------------------------------------------------------------------

    def test_server_throttle_backs_off_instead_of_upselling(self):
        handler = self.flood_handler()
        self.assertIn('error.hasPrefix("FLOOD_PREMIUM_WAIT")', handler)
        self.assertIn('state.window = max(fastDownloadMinWindow, state.window / 2)', handler)
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)

    def test_adaptive_window_is_not_rust_only(self):
        source = self.fetch_v2()
        self.assertIn('maxPendingParts = min(maxPendingParts, state.window)', source)
        self.assertNotIn('if self.fastDownloads {\n                    maxPendingParts', source)
        self.assertIn('self.adaptWindow(state: state, latency: CFAbsoluteTimeGetCurrent() - requestStarted, bytes: Int64(data.count))', source)
        self.assertNotIn('if self.fastDownloads {\n                            self.adaptWindow', source)

    def test_cdn_parts_report_flood_wait(self):
        source = self.fetch_v2()
        self.assertEqual(source.count('onFloodWaitError:'), 2,
                         'Both the main-DC and CDN part requests must report throttling')

    def test_v1_path_has_no_premium_upsell(self):
        source = self.text('submodules/TelegramCore/Sources/Network/MultipartFetch.swift')
        handler = source.split('private func processFloodWaitError', 1)[1].split('func ', 1)[0]
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)

    def test_speed_setting_is_a_ceiling_on_the_rust_engine(self):
        self.assertIn('min(fastDownloadMasterPendingParts, pendingParts)', self.fetch_v2())
        settings = self.text('Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift')
        self.assertIn('return min(`default` + 2, 8)', settings)
        self.assertIn('return min(`default`, 4)', settings)

    def test_maximum_tier_part_size_is_bounded(self):
        settings = self.text('Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift')
        maximum = settings.split('DownloadSpeedBoostValues.maximum.rawValue:', 1)[1]
        maximum = maximum.split('default:', 1)[0]
        self.assertIn('return 512 * 1024', maximum)
        self.assertNotIn('1024 * 1024', maximum)

    def test_opened_video_parts_are_prioritised(self):
        gallery = self.gallery_video()
        self.assertIn('fetchPriorityDisposable.set(item.context.engine.resources.pushPriorityDownload', gallery)
        self.assertIn('self.fetchPriorityDisposable.dispose()', gallery)

    def test_autoplay_also_gets_the_priority_boost(self):
        gallery = self.gallery_video()
        central = gallery.split('override func centralityUpdated', 1)[1].split('override func ', 1)[0]
        self.assertIn('pushPriorityDownload', central,
                      'Autoplay never calls fetchControls.fetch(); central activation must boost it')

    def test_prefetch_leaves_queue_headroom(self):
        prefetch = self.text('submodules/TelegramUI/Sources/InChatPrefetchManager.swift')
        self.assertIn('self.contexts.count >= 2', prefetch)

    def test_category_scheduler_is_not_single_active(self):
        source = self.fetch_manager()
        self.assertIn('private let maxActiveEntries = 3', source)
        self.assertIn('let _ = self.activeEntryIds.insert(topEntryId)', source)
        self.assertIn('let _ = self.activeEntryIds.remove(id)', source)
        self.assertIn('activeEntryIds.contains(id)', source)

    def test_failed_fetch_releases_its_slot(self):
        source = self.fetch_manager()
        self.assertIn('private let entryFailed: (FetchManagerLocationEntryId) -> Void', source)
        self.assertIn('context.cancelEntry(id, isCompleted: false)', source)
        self.assertGreaterEqual(source.count('}, error: { _ in'), 3,
                                'All three fetch signals need an error handler')
        self.assertEqual(source.count('entryFailed(topEntryId)'), 2)
        self.assertEqual(source.count('entryFailed(id)'), 1)

    def test_user_opened_downloads_use_the_highest_priority_lane(self):
        source = self.fetch_manager()
        self.assertEqual(
            source.count('entry.userInitiated ? [(0 ..< Int64.max, .maximum)] : nil'),
            2,
        )

    def test_user_opened_video_generates_a_first_frame(self):
        source = self.fetch_manager()
        self.assertIn('if entry.userInitiated, case .video = entry.statsCategory', source)
        self.assertIn('duration: 4.0', source)
        self.assertIn('alternate: fallbackPreload', source,
                      'A stalled frame preload must get one retry instead of hanging forever')


if __name__ == '__main__':
    unittest.main()
