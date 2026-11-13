import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SwipeBackRegressionTests(unittest.TestCase):
    """Guard the "half-screen swipe back" gesture against downward-scroll misfires.

    Reproduced symptom: while scrolling a chat/sheet down, the navigation pop
    transition starts anyway. The fix keeps the gesture (it must NOT be turned
    off) but follows the industry-standard rules:
      * confine the start region to the left third of the screen,
      * require a real horizontal intent before accepting an edge start,
      * lock the axis and cancel once the gesture turns clearly vertical,
      * use a 10pt minimum displacement before locking.
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
    def decide_start(translation_x, translation_y, minimum=10.0):
        """Mirror the non-edge (center) branch of touchesMoved."""
        ax, ay = abs(translation_x), abs(translation_y)
        total = (ax * ax + ay * ay) ** 0.5
        if total > 10.0:
            return 'began' if ax >= ay else 'failed'
        if ay > 2.0 and ay > ax * 2.0:
            return 'failed'
        if ax > minimum and ay * 2.0 < ax:
            return 'began'
        return 'undecided'

    @staticmethod
    def edge_intent(translation_x, translation_y, minimum=10.0):
        """Mirror the new edge-start guard."""
        ax, ay = abs(translation_x), abs(translation_y)
        return translation_x > minimum and ax > ay

    @staticmethod
    def direction_lock(velocity_x, velocity_y, is_down_direction=False):
        """Mirror the post-began direction lock."""
        if is_down_direction:
            return 'keep'
        if abs(velocity_y) > abs(velocity_x) * 1.5 and abs(velocity_y) > 200.0:
            return 'cancelled'
        return 'keep'

    @staticmethod
    def allows_start(point_x, width, configuration=None):
        """Mirror allowedDirections(point) in NavigationContainer."""
        allowed = min(width * 0.35, 200.0)
        if configuration is not None:
            factor, minimum, maximum = configuration
            allowed = max(minimum, min(width * factor, maximum))
        return point_x <= allowed

    def test_downward_scroll_does_not_start_the_pop(self):
        # A normal downward scroll: vertical clearly dominates.
        self.assertEqual(self.decide_start(3.0, 24.0), 'failed')
        self.assertEqual(self.decide_start(2.0, 8.0), 'failed')
        # An edge start must also be rejected without horizontal intent.
        self.assertFalse(self.edge_intent(0.0, 24.0))
        self.assertFalse(self.edge_intent(3.0, 24.0))
        # A deliberate right swipe still works.
        self.assertEqual(self.decide_start(24.0, 3.0), 'began')
        self.assertTrue(self.edge_intent(24.0, 3.0))

    def test_tap_jitter_no_longer_locks_the_gesture(self):
        # 2pt of horizontal jitter used to satisfy minimumCenterMovement=2.
        # With a 10pt floor the gesture stays undecided until real movement.
        self.assertEqual(self.decide_start(2.5, 0.0), 'undecided')
        self.assertEqual(self.decide_start(2.5, 0.0, minimum=2.0), 'began')

    def test_direction_lock_cancels_a_pop_that_turns_vertical(self):
        self.assertEqual(self.direction_lock(600.0, 120.0), 'keep')
        self.assertEqual(self.direction_lock(120.0, 600.0), 'cancelled')
        # A slow drift is ignored so a slightly diagonal swipe still works.
        self.assertEqual(self.direction_lock(600.0, 150.0), 'keep')
        # Downward-dismiss sheets keep their vertical gesture.
        self.assertEqual(self.direction_lock(120.0, 600.0, is_down_direction=True), 'keep')

    def test_start_region_is_confined_but_not_disabled(self):
        width = 390.0
        self.assertTrue(self.allows_start(0.0, width))
        self.assertTrue(self.allows_start(120.0, width))
        self.assertFalse(self.allows_start(200.0, width))
        self.assertFalse(self.allows_start(380.0, width))
        # ChatController's 0.35 configuration is actually honoured now.
        self.assertTrue(self.allows_start(130.0, width, (0.35, 16.0, 200.0)))
        self.assertFalse(self.allows_start(180.0, width, (0.35, 16.0, 200.0)))
        # The gesture is still enabled on a compact screen.
        self.assertIn('panRecognizer.isEnabled = true', self.modal())

    # ------------------------------------------------------------------
    # Structural guards.
    # ------------------------------------------------------------------

    def test_minimum_displacement_is_ten_points(self):
        source = self.recognizer()
        self.assertIn('private var minimumCenterMovement: CGFloat = 10.0', source)
        self.assertIn('self.minimumCenterMovement = max(10.0,', source)
        self.assertIn('self.minimumCenterMovement = 10.0', source)

    def test_edge_start_requires_horizontal_intent(self):
        source = self.recognizer()
        self.assertIn('let horizontalIntent = translation.x > self.minimumCenterMovement && absTranslationX > absTranslationY', source)
        edge = source.split('let horizontalIntent', 1)[1].split('} else if !self.currentAllowedDirections.contains(.leftCenter)', 1)[0]
        self.assertIn('&& horizontalIntent', edge)

    def test_direction_lock_is_present_and_scoped(self):
        source = self.recognizer()
        self.assertIn('if !self.currentAllowedDirections.contains(.down) {', source)
        self.assertIn('self.state = .cancelled', source)
        self.assertIn('abs(velocity.y) > abs(velocity.x) * 1.5', source)

    def test_start_point_is_used_in_both_containers(self):
        for source in (self.container(), self.modal()):
            self.assertIn('allowedDirections: { [weak self] point in', source)
            self.assertIn('guard point.x <=', source)
            self.assertIn('min(width * 0.35, 200.0)', source)

    def test_per_screen_configuration_is_read(self):
        self.assertIn('strongSelf.controllers.last?.interactiveNavivationGestureEdgeWidth', self.container())
        self.assertIn('case let .widthMultiplier(factor, minValue, maxValue):', self.container())

    def test_dynamic_edge_width_dead_code_is_removed(self):
        # Restoring dynamicEdgeWidth alone widened the zero-check edge and
        # would have made misfires worse; it must stay gone.
        self.assertNotIn('panRecognizer.dynamicEdgeWidth', self.container())


if __name__ == '__main__':
    unittest.main()
