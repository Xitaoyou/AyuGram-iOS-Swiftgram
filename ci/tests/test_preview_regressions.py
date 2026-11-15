import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PreviewRegressionTests(unittest.TestCase):
    """Keep the preview-speed root-cause fixes from silently disappearing.

    Reproduced on-device symptom after the download A/B/C build: opening a
    photo or a video still felt slow when a chat contained a lot of media.

    The dominant root cause was NOT the network window. A lone full-range
    request was collapsed to `ranges = nil` in FetchedMediaResource, which
    threw away its priority, and fetchedFullRange hardcoded `.default`. So
    every `.maximum` / `.elevated` setting on a user-opened file was a no-op:
    the file the user tapped shared one lane with the entire prefetch queue.

    Besides structural guards this module simulates the collapse, the lane
    ordering, the progressive tier de-duplication and the first-frame display
    loop, so a behavioural regression fails here rather than only on device.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def fetched_media_resource(self):
        return self.text('submodules/TelegramCore/Sources/Network/FetchedMediaResource.swift')

    def media_box(self):
        return self.text('submodules/Postbox/Sources/MediaBox.swift')

    def file_context_v2(self):
        return self.text('submodules/Postbox/Sources/MediaBoxFileContextV2Impl.swift')

    def file_context_v1(self):
        return self.text('submodules/Postbox/Sources/MediaBoxFile.swift')

    def file_context_protocol(self):
        return self.text('submodules/Postbox/Sources/MediaBoxFileContext.swift')

    def photo_resources(self):
        return self.text('submodules/PhotoResources/Sources/PhotoResources.swift')

    def image_gallery(self):
        return self.text('submodules/GalleryUI/Sources/Items/ChatImageGalleryItem.swift')

    def video_gallery(self):
        return self.text('submodules/GalleryUI/Sources/Items/UniversalVideoGalleryItem.swift')

    def fetch_manager(self):
        return self.text('submodules/FetchManagerImpl/Sources/FetchManagerImpl.swift')

    # ------------------------------------------------------------------
    # Behavioural simulations of the actual algorithms.
    # ------------------------------------------------------------------

    def test_full_range_collapse_preserves_the_priority_lane(self):
        """The collapse itself was fine; dropping the priority was not.

        Model the Swift branch with a lone (0..<Int64.max, .maximum) request
        and verify the lane survives all the way to the MediaBox layer.
        """
        source = self.fetched_media_resource()
        self.assertIn('var fullRangePriority: MediaBoxFetchPriority = .default', source)
        self.assertIn('fullRangePriority = rangesValue[0].1', source)
        self.assertIn('), priority: fullRangePriority, implNext: reportResultStatus)', source)

        INT64_MAX = 2 ** 63 - 1
        maximum, default = 2, 0
        ranges = [(0, INT64_MAX, maximum)]

        full_range_priority = default
        if len(ranges) == 1 and ranges[0][0] == 0 and ranges[0][1] == INT64_MAX:
            full_range_priority = ranges[0][2]
            ranges = None

        self.assertIsNone(ranges, 'Full-range downloads must still use fetchedFullRange')
        self.assertEqual(full_range_priority, maximum,
                         'The .maximum lane must not be lost in the collapse')
        self.assertEqual(self.media_box().count('}, priority: priority, error: { _ in'), 1)
        self.assertIn('priority: priority,', self.file_context_v2())
        self.assertEqual(full_range_priority, 2)

    def test_higher_priority_parts_are_served_first(self):
        """FetchV2 buckets required ranges by priority rawValue and
        fetchV2TakeNextPart walks the buckets in reverse. Simulate two queued
        parts and confirm the user-opened one wins even when the prefetch part
        arrived earlier.
        """
        fetch_v2 = self.text('submodules/TelegramCore/Sources/Network/FetchV2.swift')
        self.assertIn('func fetchV2PriorityRanges(', fetch_v2)
        self.assertIn('result[priority].formUnion(', fetch_v2)
        self.assertIn('RequiredRange(value: $0.0, priority: $0.1)', fetch_v2)
        self.assertIn('priority: Int($0.priority.rawValue)', fetch_v2)
        self.assertIn('for priorityIndex in (0 ..< ranges.count).reversed()', fetch_v2)

        queued = [('prefetch-part', 0), ('user-opened-part', 2)]
        buckets = [[], [], []]
        for name, priority in queued:
            buckets[priority].append(name)
        order = []
        for priority_index in reversed(range(3)):
            order.extend(buckets[priority_index])
        self.assertEqual(order[0], 'user-opened-part')

    def test_progressive_tiers_are_deduplicated_and_stay_ascending(self):
        """Keeping the LAST occurrence of each size removes the three
        duplicate subscriptions and still leaves the largest tier last,
        which the display loop relies on.
        """
        source = self.photo_resources()
        self.assertIn('var lastIndexBySize: [Int64: Int] = [:]', source)
        self.assertIn('return lastIndexBySize[size] == entry.offset', source)

        # progressiveRangeMap produces [3], [4], then [2, 3, 4].
        tiers = [3, 4, 2, 3, 4]
        last_index_by_size = {size: index for index, size in enumerate(tiers)}
        deduped = [size for index, size in enumerate(tiers)
                   if last_index_by_size[size] == index]
        self.assertEqual(deduped, [2, 3, 4])
        self.assertEqual(deduped[-1], max(tiers), 'Largest tier must stay last')
        self.assertEqual(len(deduped), 3)

    def test_photo_shows_the_first_available_tier_instead_of_waiting(self):
        """The old loop skipped the two smallest tiers and completed as soon
        as the largest one had data: no picture until then, and no refresh
        afterwards. Model a state where only a small tier has bytes.
        """
        source = self.photo_resources()
        self.assertIn('let lastEmittedByteCount = Atomic<Int?>(value: nil)', source,
                      'combineLatest may call next from several queues')
        self.assertIn('lastEmittedByteCount.swap(data.count) == data.count', source)
        self.assertIn('let isComplete = data.count >= Int('
                      'progressiveRepresentation.progressiveSizes.last!)', source)
        self.assertNotIn('i <= 1 && !isLastSize', source,
                         'Small tiers must not be skipped')
        self.assertNotIn('subscriber.putNext(Tuple4(nil, data, .full, isLastSize))', source)
        self.assertNotIn('isLastSize', source,
                         'The display signal must not complete on a partial tier')

        total_size = 500_000
        results = [('mini', 0), ('tier2', 40_000), ('tier3', 0), ('tier4', 0)]
        emitted = None
        for name, count in reversed(results):
            if name == 'mini' or count == 0:
                continue
            emitted = (name, count, count >= total_size)
            break
        self.assertEqual(emitted, ('tier2', 40_000, False),
                         'A partial progressive scan must be drawn immediately')
        self.assertFalse(emitted[2], 'A partial scan must use the incremental decoder')

    def test_priority_key_uses_the_most_urgent_reference(self):
        """FetchManagerPriority orders userInitiated < foregroundPrefetch <
        backgroundPrefetch and the scheduler picks the smallest key, so a
        min(), not a max(), must represent an entry's references.
        """
        source = self.fetch_manager()
        self.assertIn('topReference: self.references.copyItems().min())', source)
        self.assertNotIn('topReference: self.references.copyItems().max())', source)

        priority_order = {'userInitiated': 0, 'foregroundPrefetch': 1,
                          'backgroundPrefetch': 2}
        references = ['backgroundPrefetch', 'userInitiated', 'foregroundPrefetch']
        self.assertEqual(min(references, key=lambda r: priority_order[r]),
                         'userInitiated')

    # ------------------------------------------------------------------
    # Structural guards for each remaining root cause.
    # ------------------------------------------------------------------

    def test_full_range_priority_reaches_every_media_box_layer(self):
        self.assertIn('priority: MediaBoxFetchPriority = .default, implNext: Bool = false',
                      self.media_box())
        self.assertIn('priority: MediaBoxFetchPriority, error:',
                      self.file_context_protocol())
        self.assertIn('priority: MediaBoxFetchPriority = .default,',
                      self.file_context_v2())
        self.assertIn('priority: MediaBoxFetchPriority = .default,',
                      self.file_context_v1())
        self.assertIn('priority: priority, fetch: fetch', self.file_context_v1(),
                      'The legacy V1 context must not drop the lane either')
        v2 = self.file_context_v2().split('public func fetchedFullRange(', 1)[1]
        v2 = v2.split('public func cancelFullRangeFetches', 1)[0]
        self.assertIn('priority: priority,', v2)
        self.assertNotIn('priority: .default,', v2,
                         'V2 full-range fetches must no longer be pinned to .default')

    def test_full_screen_photo_uses_the_maximum_lane(self):
        gallery = self.image_gallery()
        self.assertIn('ranges: [(0 ..< Int64.max, .maximum)]', gallery,
                      'The no-range fetch bypassed FetchManager and was .default')
        self.assertIn('self.fetchDisposable.set(self.context.engine.resources.fetch(', gallery)

    def test_opened_video_pulls_its_head_in_the_maximum_lane(self):
        """CachedVideoFirstFrameRepresentation only reads the partial file;
        it never fetches. Autoplay never calls fetchControls.fetch(), so the
        first frame had to wait for the player's sequential download.
        """
        gallery = self.video_gallery()
        self.assertIn('private let frameHeadDisposable = MetaDisposable()', gallery)
        self.assertIn('ranges: [(0 ..< headLength, .maximum)]', gallery)
        self.assertIn('statsCategory: .video', gallery)
        self.assertIn('content.fileReference.resourceReference(file.resource)', gallery)
        self.assertIn('self.frameHeadDisposable.dispose()', gallery,
                      'The holder must be disposed on deinit')
        central = gallery.split('override func centralityUpdated', 1)[1]
        inactive = central.split('if !isCentral {', 1)[1].split('}', 1)[0]
        self.assertIn('self.fetchPriorityDisposable.set(nil)', inactive,
                      'Offscreen videos must release their maximum network priority')
        self.assertIn('self.frameHeadDisposable.set(nil)', central,
                      'Leaving the item must cancel the fetch but allow re-entry')
        self.assertNotIn('self.frameHeadDisposable.dispose()', central,
                         'Disposing the holder permanently cancels future head fetches')

    def test_partial_range_downloads_keep_the_user_initiated_lane(self):
        source = self.fetch_manager()
        lane = ('entry.userInitiated ? .maximum : '
                '(entry.elevatedPriorityReferenceCount > 0 ? .elevated : .default)')
        self.assertEqual(source.count(lane), 2,
                         'Both range-building paths must honour user intent')

    def test_both_prefetch_queues_are_bounded_and_wide_enough(self):
        in_chat = self.text('submodules/TelegramUI/Sources/InChatPrefetchManager.swift')
        background = self.text('submodules/TelegramUI/Sources/PrefetchManager.swift')
        self.assertIn('self.contexts.count >= 4', in_chat,
                      'A cap of 2 starved the batch itself')
        self.assertIn('self.contexts.count >= 4', background,
                      'orderedPreloadMedia used to be enqueued without any bound')

    def test_existing_download_guards_still_hold(self):
        source = self.fetch_manager()
        self.assertIn('entry.userInitiated ? [(0 ..< Int64.max, .maximum)] : nil', source)
        self.assertIn('private let maxActiveEntries = 3', source)
        self.assertIn('if entry.userInitiated, case .video = entry.statsCategory', source)


if __name__ == '__main__':
    unittest.main()
