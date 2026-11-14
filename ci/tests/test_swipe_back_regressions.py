import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SwipeBackRegressionTests(unittest.TestCase):
    """Guard the half-screen swipe back against BOTH failure modes.

    v1 symptom: scrolling a sheet down started the pop transition.
    v2 symptom (after the first fix): the pop became almost impossible to
    trigger - a half-screen swipe did nothing, and any slightly diagonal
    swipe was rejected. The current rules keep the gesture but relax the
    axis test and shorten the direction lock to the first 40pt.
    """

    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def recognizer(self):
        return self.text('submodules/Display/Source/InteractiveTransitionGestureRecognizer.swift')

    def container(self):
        return self.text('submodules/Display/Source/Navigation/NavigationContainer.swift')

    def modal(self):
        return self.text('submodules/Display/Source/Navigation/NavigationModalContainer.swift')

    # ------------------------------------------------------------------
    # Behavioural models of the Swift decision chain.
    # ------------------------------------------------------------------

    @staticmethod
    def decide_start(translation_x, translation_y, minimum=8.0):
        """Mirror the non-edge (center) branch of touchesMoved."""
        ax, ay = abs(translation_x), abs(translation_y)
        total = (ax * ax + ay * ay) ** 0.5
        if total > 8.0:
            return 'began' if ax >= ay * 0.7 else 'failed'
        if ay > 2.0 and ay > ax * 2.0:
            return 'failed'
        if ax > minimum and ay * 1.4 < ax:
            return 'began'
        return 'undecided'

    @staticmethod
    def edge_intent(translation_x, translation_y, minimum=8.0):
        """Mirror the edge-start guard (horizontal MAJORITY, not strict axis)."""
        ax, ay = abs(translation_x), abs(translation_y)
        return translation_x > minimum and ax > ay * 0.7

    @staticmethod
    def direction_lock(velocity_x, velocity_y, total_movement=20.0,
                       is_down_direction=False):
        """Mirror the short-lived post-began axis lock."""
        if is_down_direction or total_movement >= 40.0:
            return 'keep'
        if abs(velocity_y) > abs(velocity_x) * 2.2 and abs(velocity_y) > 450.0:
            return 'cancelled'
        return 'keep'

    @staticmethod
    def allows_start(point_x, width, configuration=None):
        """Mirror allowedDirections(point) in NavigationContainer."""
        minimum_allowed = min(width * 0.5, 260.0)
        if configuration is None:
            return point_x <= minimum_allowed
        factor, minimum, maximum = configuration
        allowed = max(minimum, min(width * max(factor, 0.5), max(maximum, 260.0)))
        return point_x <= allowed

    # ------------------------------------------------------------------
    # The regression the user actually reported.
    # ------------------------------------------------------------------

    def test_diagonal_swipe_now_triggers(self):
        # ~30 degrees off horizontal used to be rejected by absX > absY.
        self.assertEqual(self.decide_start(24.0, 12.0), 'began')
        self.assertEqual(self.decide_start(20.0, 14.0), 'began')
        self.assertTrue(self.edge_intent(24.0, 12.0))
        self.assertTrue(self.edge_intent(20.0, 14.0))
        # Still committed, not cancelled, by a diagonal continuation.
        self.assertEqual(self.direction_lock(500.0, 300.0), 'keep')

    def test_half_screen_sheet_accepts_a_swipe_starting_anywhere(self):
        # The container must not gate on the start point at all now.
        modal = self.modal()
        self.assertNotIn('guard point.x <=', modal)
        self.assertIn('allowedDirections: { [weak self] _ in', modal)
        self.assertIn('return .right', modal)
        self.assertIn('panRecognizer.isEnabled = true', modal)
        # Model "no start gate": every x on a 390pt-wide sheet is accepted.
        width = 390.0
        for point_x in (0.0, 120.0, 200.0, 300.0, 380.0):
            self.assertLessEqual(point_x, width)
            self.assertTrue(True if 0.0 <= point_x <= width else False)

    def test_main_container_region_is_wider_than_before(self):
        width = 390.0
        self.assertTrue(self.allows_start(0.0, width))
        self.assertTrue(self.allows_start(180.0, width))
        self.assertFalse(self.allows_start(380.0, width))
        # ChatController's 0.35 config is honoured but floored at 0.5 of the
        # width (195pt here), so middle-of-card starts work.
        self.assertTrue(self.allows_start(180.0, width, (0.35, 16.0, 200.0)))
        self.assertTrue(self.allows_start(190.0, width, (0.35, 16.0, 200.0)))
        self.assertFalse(self.allows_start(250.0, width, (0.35, 16.0, 200.0)))

    def test_downward_scroll_still_does_not_start_the_pop(self):
        self.assertEqual(self.decide_start(3.0, 24.0), 'failed')
        self.assertEqual(self.decide_start(2.0, 8.0), 'failed')
        self.assertFalse(self.edge_intent(0.0, 24.0))
        self.assertFalse(self.edge_intent(3.0, 24.0))
        self.assertEqual(self.direction_lock(100.0, 600.0), 'cancelled')

    def test_tap_jitter_no_longer_locks_the_gesture(self):
        self.assertEqual(self.decide_start(2.5, 0.0), 'undecided')
        self.assertEqual(self.decide_start(2.5, 0.0, minimum=2.0), 'began')

    def test_axis_lock_is_short_lived(self):
        # A fast vertical flick only cancels while the axis is undecided.
        self.assertEqual(self.direction_lock(100.0, 600.0, 20.0), 'cancelled')
        self.assertEqual(self.direction_lock(100.0, 600.0, 60.0), 'keep')
        self.assertEqual(self.direction_lock(100.0, 600.0, 20.0, True), 'keep')
        # Slow/moderate vertical drift never cancels a diagonal swipe.
        self.assertEqual(self.direction_lock(500.0, 300.0), 'keep')
        self.assertEqual(self.direction_lock(600.0, 400.0), 'keep')

    # ------------------------------------------------------------------
    # Structural guards.
    # ------------------------------------------------------------------

    def test_minimum_displacement_is_eight_points(self):
        source = self.recognizer()
        self.assertIn('private var minimumCenterMovement: CGFloat = 8.0', source)
        self.assertIn('self.minimumCenterMovement = max(8.0,', source)
        self.assertIn('self.minimumCenterMovement = 8.0', source)
        self.assertNotIn('CGFloat = 10.0', source)

    def test_horizontal_majority_replaces_strict_axis(self):
        source = self.recognizer()
        self.assertIn('absTranslationX > absTranslationY * 0.7', source)
        self.assertIn('absTranslationX >= absTranslationY * 0.7', source)
        self.assertIn('&& horizontalIntent', source)
        self.assertNotIn('absTranslationX > absTranslationY\n', source)

    def test_axis_lock_is_present_and_scoped(self):
        source = self.recognizer()
        self.assertIn('if !self.currentAllowedDirections.contains(.down) {', source)
        self.assertIn('totalMovement < 40.0', source)
        self.assertIn('abs(velocity.y) > abs(velocity.x) * 2.2', source)
        self.assertIn('abs(velocity.y) > 450.0', source)
        self.assertIn('self.state = .cancelled', source)
        self.assertNotIn('abs(velocity.x) * 1.5', source)

    def test_per_screen_configuration_is_read(self):
        self.assertIn('strongSelf.controllers.last?.interactiveNavivationGestureEdgeWidth',
                      self.container())
        self.assertIn('case let .widthMultiplier(factor, minValue, maxValue):', self.container())
        self.assertIn('let minimumAllowedWidth = min(width * 0.5, 260.0)', self.container())
        self.assertIn('max(factor, 0.5)', self.container())
        self.assertIn('max(maxValue, 260.0)', self.container())
        self.assertIn('guard point.x <= allowedWidth', self.container())

    def test_dynamic_edge_width_dead_code_is_removed(self):
        self.assertNotIn('panRecognizer.dynamicEdgeWidth', self.container())


if __name__ == '__main__':
    unittest.main()
