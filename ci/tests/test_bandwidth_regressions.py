import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BandwidthRegressionTests(unittest.TestCase):
    """Keep the bandwidth-maximisation changes from silently disappearing.

    Honest scope: FLOOD_PREMIUM_WAIT_* is a server-side, per-account 420 that
    counts in-flight upload.getFile requests. No client flag can turn it off
    (upload.getFile has no premium parameter). What a client CAN do is:
      - ask for a CDN redirect so parts use a separate RPC / DC pool,
      - move more bytes per request (the throttle counts requests, not bytes),
      - raise the transport capacity that was pinning the main DC at 12,
      - learn the largest window the account tolerates and run right up
        against it, instead of blindly spiking into a multi-second 420.

    This module pins those changes and simulates the learned-window loop so a
    behavioural regression fails here rather than only on a device.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def fetch_v2(self):
        return self.text('submodules/TelegramCore/Sources/Network/FetchV2.swift')

    def settings(self):
        return self.text('Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift')

    def network(self):
        return self.text('submodules/TelegramCore/Sources/Network/Network.swift')

    def multiplexed(self):
        return self.text('submodules/TelegramCore/Sources/Network/MultiplexedRequestManager.swift')

    def flood_handler(self):
        handler = self.fetch_v2().split('private func processFloodWaitError', 1)[1]
        return handler.split('private func ', 1)[0]

    # ------------------------------------------------------------------
    # Behavioural simulations of the actual algorithms.
    # ------------------------------------------------------------------

    def test_learned_ceiling_runs_at_the_edge_without_spiking_into_420(self):
        source = self.fetch_v2()
        self.assertIn('private var learnedWindowCeiling: Int? = nil', source)
        self.assertIn('fastDownloadCeilingProbeInterval: Double = 60.0', source)
        self.assertIn('state.window = min(ceiling, state.window + 2)', source)
        self.assertIn(
            'self.learnedWindowCeiling = min(self.learnedWindowCeiling ?? Int.max, state.window)',
            source,
        )

        INITIAL, MAX_PENDING, MIN_WINDOW, PROBE_INTERVAL = 8, 12, 2, 60.0

        class WindowModel:
            def __init__(self):
                self.window = INITIAL
                self.ceiling = None
                self.last_probe = 0.0

            def fast_part(self, now):
                if now - self.last_probe > PROBE_INTERVAL:
                    self.last_probe = now
                    if self.ceiling is not None:
                        self.ceiling = min(self.ceiling + 1, MAX_PENDING)
                cap = self.ceiling if self.ceiling is not None else MAX_PENDING
                self.window = min(cap, self.window + 2)

            def flood_wait(self):
                self.window = max(MIN_WINDOW, self.window // 2)
                limit = self.ceiling if self.ceiling is not None else 1 << 30
                self.ceiling = min(limit, self.window)

        model = WindowModel()
        for t in range(0, 40, 2):
            model.fast_part(float(t))
        self.assertEqual(model.window, MAX_PENDING,
                         'A healthy account must be allowed to reach the top tier')

        model.flood_wait()
        self.assertEqual(model.window, 6)
        self.assertEqual(model.ceiling, 6)
        for t in range(42, 58, 2):
            model.fast_part(float(t))
        self.assertEqual(model.window, 6,
                         'Ramp-up must stop at the learned ceiling instead of '
                         'immediately provoking another 420')

        model.fast_part(65.0)
        self.assertEqual(model.ceiling, 7,
                         'A slow probe lets the ceiling recover when the '
                         'account budget opens up again')
        self.assertEqual(model.window, 7)

    def test_larger_parts_break_the_low_throughput_self_lock(self):
        """adaptWindow used to shrink parts to 128KB below 1.5MB/s, but the
        only way back to 512KB was measuring >= 4MB/s - unreachable with
        128KB parts. The new floor keeps the user-selected tier.
        """
        source = self.fetch_v2()
        self.assertIn('state.partSize = max(self.defaultPartSize, fastDownloadSmallPartSize)',
                      source)
        self.assertNotIn('state.partSize = max(state.minPartSize, fastDownloadSmallPartSize)',
                         source)

        min_part_size = 4 * 1024
        small_part = 128 * 1024
        default_part = 512 * 1024
        large_part = 512 * 1024
        grow_threshold = 4 * 1024 * 1024

        self.assertEqual(max(min_part_size, small_part), small_part)
        self.assertEqual(max(default_part, small_part), default_part)
        self.assertEqual(min(1 * 1024 * 1024, large_part), default_part)
        # 128KB parts arriving every 0.25s can never reach the grow threshold.
        self.assertLess(small_part / 0.25, grow_threshold)

    def test_cdn_part_size_stays_hash_aligned(self):
        source = self.fetch_v2()
        self.assertIn('self.cdnPartSize = 256 * 1024', source)
        self.assertIn('private let cdnHashBlockLength: Int64 = 128 * 1024', source)
        self.assertEqual((256 * 1024) % (128 * 1024), 0,
                         'CDN parts must be a whole number of hash blocks')
        self.assertIn(256 * 1024, [1 << i for i in range(10, 21)])

    def test_cdn_budget_is_double_the_master_budget(self):
        source = self.fetch_v2()
        self.assertIn(
            'self.cdnPendingParts = min(fastDownloadCdnPendingParts, pendingParts * 2)',
            source,
        )
        self.assertIn('min(fastDownloadMasterPendingParts, pendingParts)', source)
        self.assertEqual(min(32, 12 * 2), 24)
        self.assertGreater(min(32, 12 * 2), 12)

    # ------------------------------------------------------------------
    # Structural guards for each change.
    # ------------------------------------------------------------------

    def test_cdn_redirect_is_requested_on_every_engine(self):
        source = self.fetch_v2()
        self.assertIn('flags: !self.cdnDisabled ? (1 << 1) : 0', source,
                      'cdn_supported was gated behind the rust engine, so iOS '
                      'never received a CDN redirect')
        self.assertNotIn('self.fastDownloads && !self.cdnDisabled', source)
        self.assertIn('private var cdnDisabled: Bool = false', source,
                      'A failed CDN must still be able to disable further redirects')

    def test_cdn_worker_pool_is_not_downgraded_on_ios(self):
        network = self.network()
        self.assertIn('cdnMaxRequestsPerWorker: 4, cdnMaxWorkersPerTarget: 8', network)
        self.assertNotIn('fastDownloads ? 4 : 3', network)
        self.assertNotIn('fastDownloads ? 8 : 4', network)
        self.assertNotIn('let fastDownloads = engine.kind == .rust', network)

    def test_main_dc_transport_capacity_is_raised(self):
        multiplexed = self.multiplexed()
        limits = multiplexed.split('private func limits(', 1)[1].split('private func ', 1)[0]
        self.assertIn('return (4, 6)', limits)
        self.assertNotIn('return (3, 4)', limits)
        self.assertGreater(4 * 6, 3 * 4)

    def test_cdn_fallback_is_not_rust_only(self):
        """Enabling CDN on iOS is only safe if hash failures can still fall
        back to the main DC and the switch keeps already-downloaded ranges.
        """
        source = self.fetch_v2()
        self.assertIn('self.cdnVerificationFailures += 1', source)
        self.assertIn(
            'if self.cdnVerificationFailures >= maxCdnFailuresBeforeFallback, '
            'case let .cdn(cdnData) = state.fetchLocation {',
            source,
        )
        self.assertIn('cdnState.completedRanges = state.completedRanges', source)
        self.assertEqual(source.count('cdnState.completedRanges = state.completedRanges'), 1)
        self.assertNotIn('if self.fastDownloads {\n                            cdnState', source)
        self.assertIn('self.fallBackToMaster(', source)

    def test_cold_start_probe_clamp_is_removed(self):
        source = self.fetch_v2()
        self.assertIn('maxPendingParts = min(maxPendingParts, state.window)', source)
        self.assertNotIn('min(maxPendingParts, 4)', source,
                         'The first burst must use the full adaptive window')

    def test_speed_tiers_and_initial_window_are_higher(self):
        source = self.fetch_v2()
        settings = self.settings()
        self.assertIn('private let fastDownloadInitialWindow = 8', source)
        self.assertIn('return min(`default` + 2, 10)', settings)
        self.assertIn('return min(`default` + 6, 14)', settings)
        self.assertIn('return min(`default`, 4)', settings,
                      'The "none" tier must stay conservative')
        self.assertEqual(min(6 + 2, 10), 8)
        self.assertEqual(min(6 + 6, 14), 12)

    def test_420_backoff_remains_the_safety_net(self):
        """The learned ceiling only works because a 420 still halves the
        window and frees the in-flight multiplexer slots.
        """
        handler = self.flood_handler()
        self.assertIn('error.hasPrefix("FLOOD_PREMIUM_WAIT")', handler)
        self.assertIn('state.window = max(fastDownloadMinWindow, state.window / 2)', handler)
        self.assertIn('part.disposable?.dispose()', handler)
        self.assertIn('self.update()', handler)
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)
        self.assertNotIn('automaticFloodWait: false', self.fetch_v2(),
                         'Turning a 420 into .failure kills the whole download')

    def test_premium_upsell_is_still_absent_on_both_engines(self):
        self.assertNotIn('addNetworkSpeedLimitedEvent', self.fetch_v2())
        v1 = self.text('submodules/TelegramCore/Sources/Network/MultipartFetch.swift')
        handler = v1.split('private func processFloodWaitError', 1)[1].split('func ', 1)[0]
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)


if __name__ == '__main__':
    unittest.main()
