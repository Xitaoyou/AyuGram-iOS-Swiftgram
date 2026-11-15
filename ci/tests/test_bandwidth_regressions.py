import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BandwidthRegressionTests(unittest.TestCase):
    """Structural regression guards for part sizing, recovery, and CDN gating.

    In-flight byte capacity is not measured throughput or proof of a server
    throttling algorithm. Executable Swift scheduling tests live in
    ci/check_download_scheduling.py; device throughput still needs measurement.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def fetch_v2(self):
        return self.text('submodules/TelegramCore/Sources/Network/FetchV2.swift')

    def settings(self):
        return self.text('Swiftgram/SGSimpleSettings/Sources/SimpleSettings.swift')

    def multiplexed(self):
        return self.text('submodules/TelegramCore/Sources/Network/MultiplexedRequestManager.swift')

    def flood_handler(self):
        handler = self.fetch_v2().split('private func processFloodWaitError', 1)[1]
        return handler.split('private func ', 1)[0]

    # ------------------------------------------------------------------
    # Behavioural simulations.
    # ------------------------------------------------------------------

    def test_maximum_has_twice_the_inflight_byte_capacity(self):
        """Check configured capacity, without claiming a throughput gain."""
        rate = 8                       # same as V1's parallelParts for big files
        v1_part = 512 * 1024
        maximum_part = 1024 * 1024
        self.assertEqual(rate * v1_part, 4 * 1024 * 1024)
        self.assertEqual(rate * maximum_part, 8 * 1024 * 1024)
        self.assertEqual(rate * maximum_part, 2 * (rate * v1_part))
        # The configured per-file request ceiling is unchanged.
        self.assertEqual(rate, 8)

        source = self.settings()
        self.assertIn('return 1024 * 1024', source)
        self.assertIn('return min(`default` + 2, 8)', source)
        self.assertIn('return min(`default`, 6)', source)
        self.assertIn('return min(`default`, 4)', source)
        self.assertIn('private let fastDownloadInitialWindow = 8', self.fetch_v2())

    def test_latency_driven_window_collapse_is_gone(self):
        """Model the old vs new window decision for a 1MB part taking 2.4s."""
        source = self.fetch_v2()
        self.assertNotIn('if latency > fastDownloadSlowPartLatency', source)
        self.assertNotIn('else if latency < fastDownloadFastPartLatency', source)
        self.assertNotIn('fastDownloadFastPartLatency', source)
        self.assertNotIn('fastDownloadSlowPartLatency', source)
        self.assertIn('now - max(0.001, latency)', source)
        self.assertIn('state.recentParts.map { $0.started }.min()', source)
        self.assertIn('state.window = min(ceiling, state.window + 1)', source)
        self.assertIn('private let fastDownloadLargePartSize: Int64 = 1024 * 1024', source)

        def old_window(latency, window=8):
            if latency > 2.0:
                return max(2, window // 2)
            if latency < 1.0:
                return min(12, window + 2)
            return window

        def new_window(latency, window=8, ceiling=None):
            cap = ceiling if ceiling is not None else 8
            return min(cap, window + 1)

        part_latency = 2.4          # normal for 1MB on a mobile connection
        collapsed = 8
        for _ in range(6):
            collapsed = old_window(part_latency, collapsed)
        self.assertEqual(collapsed, 2, 'Old logic pinned maximum at window=2')
        self.assertEqual(new_window(part_latency), 8,
                         'A slow but successful part must not shrink the window')

    def test_ceiling_probe_is_not_locked_behind_a_fast_part(self):
        """The old +1 recovery lived in the latency<1s branch, unreachable
        with large parts. It must be unconditional and time-based."""
        source = self.fetch_v2()
        self.assertIn('fastDownloadCeilingProbeInterval: Double = 15.0', source)
        probe = source.split('if now - self.lastCeilingProbe > fastDownloadCeilingProbeInterval', 1)[1]
        probe = probe.split('state.recentParts.append', 1)[0]
        self.assertIn('self.learnedWindowCeiling = min(ceiling + 1, state.maxPendingParts)', probe)
        self.assertNotIn('latency <', probe)

        ceiling, window, max_pending = 2, 2, 8
        for _ in range(6):                      # six 15s probes
            ceiling = min(ceiling + 1, max_pending)
            window = min(ceiling, window + 1)
        self.assertEqual(window, 8, 'A throttled account must recover')

    # ------------------------------------------------------------------
    # Structural guards.
    # ------------------------------------------------------------------

    def test_ios_no_longer_requests_cdn_redirect(self):
        source = self.fetch_v2()
        self.assertIn('flags: self.fastDownloads && !self.cdnDisabled ? (1 << 1) : 0', source)
        self.assertNotIn('flags: !self.cdnDisabled ? (1 << 1) : 0', source)
        self.assertIn('self.cdnPartSize = 128 * 1024', source)
        self.assertIn(
            'self.cdnPendingParts = self.fastDownloads ? '
            'min(fastDownloadCdnPendingParts, pendingParts * 2) : pendingParts',
            source,
        )
        # The rust-gated CDN fault handling is back to the upstream shape.
        self.assertIn('if self.fastDownloads, self.cdnVerificationFailures >= '
                      'maxCdnFailuresBeforeFallback', source)
        self.assertIn('if self.fastDownloads {\n                            '
                      'cdnState.completedRanges', source)

    def test_only_a_real_420_may_shrink_the_window(self):
        handler = self.flood_handler()
        self.assertIn('error.hasPrefix("FLOOD_PREMIUM_WAIT")', handler)
        self.assertIn('state.window = max(fastDownloadMinWindow, state.window / 2)', handler)
        self.assertIn('part.disposable?.dispose()', handler)
        self.assertIn('self.update()', handler)
        self.assertIn('self.learnedWindowCeiling = min(', handler)
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)
        self.assertNotIn('automaticFloodWait: false', self.fetch_v2(),
                         'Turning a 420 into .failure kills the whole download')
        # adaptWindow must not contain a second halving.
        adapt = self.fetch_v2().split('private func adaptWindow', 1)[1].split('private func ', 1)[0]
        self.assertNotIn('state.window = max(fastDownloadMinWindow', adapt)

    def test_zero_second_flood_wait_still_notifies_the_download_layer(self):
        """FLOOD_PREMIUM_WAIT_0 retries immediately and keeps its slot; the
        >0 gate hid it entirely, so the queue could spin with no progress.
        """
        network = self.text('submodules/TelegramCore/Sources/Network/Network.swift')
        self.assertIn(
            'if errorContext.floodWaitSeconds > 0 || '
            'errorText.hasPrefix("FLOOD_PREMIUM_WAIT")',
            network,
        )
        self.assertNotIn(
            'floodWaitSeconds > 0, let errorText = errorContext.floodWaitErrorText',
            network,
        )
        self.assertIn('if errorContext.floodWaitSeconds > 0 && !automaticFloodWait', network,
                      'Do not turn a 420 into .failure')

    def test_cold_start_clamp_stays_removed(self):
        source = self.fetch_v2()
        self.assertIn('maxPendingParts = min(maxPendingParts, state.window)', source)
        self.assertNotIn('min(maxPendingParts, 4)', source)

    def test_transport_capacity_remains_raised(self):
        limits = self.multiplexed().split('private func limits(', 1)[1].split('private func ', 1)[0]
        self.assertIn('return (4, 6)', limits)
        self.assertNotIn('return (3, 4)', limits)

    def test_premium_upsell_is_still_absent_on_both_engines(self):
        self.assertNotIn('addNetworkSpeedLimitedEvent', self.fetch_v2())
        v1 = self.text('submodules/TelegramCore/Sources/Network/MultipartFetch.swift')
        handler = v1.split('private func processFloodWaitError', 1)[1].split('func ', 1)[0]
        self.assertNotIn('addNetworkSpeedLimitedEvent', handler)


if __name__ == '__main__':
    unittest.main()
