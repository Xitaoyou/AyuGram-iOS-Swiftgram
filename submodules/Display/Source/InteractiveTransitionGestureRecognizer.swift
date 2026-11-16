import Foundation
import UIKit

private enum HorizontalGestures {
    case none
    case some
    case strict
}

private func hasHorizontalGestures(_ view: UIView, point: CGPoint?) -> HorizontalGestures {
    if let disablesInteractiveTransitionGestureRecognizerNow = view.disablesInteractiveTransitionGestureRecognizerNow, disablesInteractiveTransitionGestureRecognizerNow() {
        return .strict
    }
    
    if view.disablesInteractiveTransitionGestureRecognizer {
        return .some
    }
    
    if let point = point, let test = view.interactiveTransitionGestureRecognizerTest, test(point) {
        return .some
    }
    
    if let view = view as? ListViewBackingView {
        let transform = view.transform
        let angle: Double = Double(atan2f(Float(transform.b), Float(transform.a)))
        let term1: Double = abs(angle - Double.pi / 2.0)
        let term2: Double = abs(angle + Double.pi / 2.0)
        let term3: Double = abs(angle - Double.pi * 3.0 / 2.0)
        if term1 < 0.001 || term2 < 0.001 || term3 < 0.001 {
            return .some
        }
    }
    
    if let superview = view.superview {
        return hasHorizontalGestures(superview, point: point != nil ? view.convert(point!, to: superview) : nil)
    } else {
        return .none
    }
}

/// A view whose own tap tolerates some drift (the chat title, a navigation bar's buttons): a navigation swipe that starts
/// on it validates only past `interactiveTransitionMinimumMovement` points, not after the usual 2pt, so a tap that drifts
/// a little sideways stays a tap, and a real swipe from it still navigates back.
public protocol InteractiveTransitionGestureRecognizerTapTarget: AnyObject {
    var interactiveTransitionMinimumMovement: CGFloat { get }
}

private func interactiveTransitionMinimumMovement(_ view: UIView) -> CGFloat? {
    var current: UIView? = view
    while let view = current {
        if let target = view as? InteractiveTransitionGestureRecognizerTapTarget {
            return target.interactiveTransitionMinimumMovement
        }
        current = view.superview
    }
    return nil
}

public struct InteractiveTransitionGestureRecognizerDirections: OptionSet {
    public var rawValue: Int
    
    public init(rawValue: Int) {
        self.rawValue = rawValue
    }
    
    public static let leftEdge = InteractiveTransitionGestureRecognizerDirections(rawValue: 1 << 2)
    public static let rightEdge = InteractiveTransitionGestureRecognizerDirections(rawValue: 1 << 3)
    public static let leftCenter = InteractiveTransitionGestureRecognizerDirections(rawValue: 1 << 0)
    public static let rightCenter = InteractiveTransitionGestureRecognizerDirections(rawValue: 1 << 1)
    public static let down = InteractiveTransitionGestureRecognizerDirections(rawValue: 1 << 4)
    
    public static let left: InteractiveTransitionGestureRecognizerDirections = [.leftEdge, .leftCenter]
    public static let right: InteractiveTransitionGestureRecognizerDirections = [.rightEdge, .rightCenter]
}

public enum InteractiveTransitionGestureRecognizerEdgeWidth {
    case constant(CGFloat)
    case widthMultiplier(factor: CGFloat, min: CGFloat, max: CGFloat)
}

public class InteractiveTransitionGestureRecognizer: UIPanGestureRecognizer {
    private let edgeWidth: InteractiveTransitionGestureRecognizerEdgeWidth
    private let allowedDirections: (CGPoint) -> InteractiveTransitionGestureRecognizerDirections
    
    private var validatedGesture = false
    // AYG: 2pt locked the gesture on a finger's horizontal jitter while
    // tapping. 10pt proved too stiff to start a pop on a half-screen sheet;
    // 8pt is the usual lower bound for an intentional pan.
    private var minimumCenterMovement: CGFloat = 8.0
    private var firstLocation: CGPoint = CGPoint()
    public private(set) var currentAllowedDirections: InteractiveTransitionGestureRecognizerDirections = []
    /// The view whose restrictions (horizontal gestures, `disablesInteractiveTransitionGestureRecognizer…`) apply at a
    /// point, when it is not the one `view.hitTest` returns (a container that takes every touch during a transition).
    public var gestureRestrictionsHitTest: ((CGPoint, UIEvent?) -> UIView?)?
    /// Permit slight initial movement in the opposite direction before deciding.
    public var initialDirectionTolerance: CGFloat = 0.0
    public var initialVerticalRejectionDistance: CGFloat = 2.0
    
    public init(target: Any?, action: Selector?, allowedDirections: @escaping (CGPoint) -> InteractiveTransitionGestureRecognizerDirections, edgeWidth: InteractiveTransitionGestureRecognizerEdgeWidth = .constant(16.0)) {
        self.allowedDirections = allowedDirections
        self.edgeWidth = edgeWidth
        
        super.init(target: target, action: action)
        
        self.maximumNumberOfTouches = 1
        self.delaysTouchesBegan = false
    }
    
    override public func reset() {
        super.reset()
        
        self.validatedGesture = false
        self.minimumCenterMovement = 8.0
        self.currentAllowedDirections = []
    }

    public func cancel() {
        self.state = .cancelled
    }
    
    override public func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        let touch = touches.first!
        let point = touch.location(in: self.view)
        
        var allowedDirections = self.allowedDirections(point)
        if allowedDirections.isEmpty {
            self.state = .failed
            return
        }
        
        super.touchesBegan(touches, with: event)
        
        self.firstLocation = point
        
        if let target = self.gestureRestrictionsHitTest?(self.firstLocation, event) ?? self.view?.hitTest(self.firstLocation, with: event) {
            self.minimumCenterMovement = max(8.0, interactiveTransitionMinimumMovement(target) ?? 8.0)
            let horizontalGestures = hasHorizontalGestures(target, point: self.view?.convert(self.firstLocation, to: target))
            switch horizontalGestures {
            case .some, .strict:
                if allowedDirections.contains(.down) {
                } else {
                    if case .strict = horizontalGestures {
                        allowedDirections = []
                    } else if allowedDirections.contains(.leftEdge) || allowedDirections.contains(.rightEdge) {
                        allowedDirections.remove(.leftCenter)
                        allowedDirections.remove(.rightCenter)
                    }
                }
            case .none:
                break
            }
        }
        
        if allowedDirections.isEmpty {
            self.state = .failed
        } else {
            self.currentAllowedDirections = allowedDirections
        }
    }
    
    override public func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        let location = touches.first!.location(in: self.view)
        let translation = CGPoint(x: location.x - self.firstLocation.x, y: location.y - self.firstLocation.y)
        
        let absTranslationX: CGFloat = abs(translation.x)
        let absTranslationY: CGFloat = abs(translation.y)
        
        let size = self.view?.bounds.size ?? CGSize()
        
        //print("moved: \(CFAbsoluteTimeGetCurrent()) absTranslationX: \(absTranslationX) absTranslationY: \(absTranslationY)")
        
        var fireBegan = false
        
        if self.currentAllowedDirections.contains(.down) {
            if !self.validatedGesture {
                let totalMovement = sqrt(absTranslationX * absTranslationX + absTranslationY * absTranslationY)
                if totalMovement > 10.0 {
                    // Force dominant direction after 10pt movement
                    if absTranslationY >= absTranslationX {
                        self.validatedGesture = true
                    } else {
                        self.state = .failed
                    }
                } else if absTranslationX > 2.0 && absTranslationX > absTranslationY * 2.0 {
                    self.state = .failed
                } else if absTranslationY > 2.0 && absTranslationX * 2.0 < absTranslationY {
                    self.validatedGesture = true
                }
            }
        } else {
            let edgeWidth: CGFloat
            switch self.edgeWidth {
            case let .constant(value):
                edgeWidth = value
            case let .widthMultiplier(factor, minValue, maxValue):
                edgeWidth = max(minValue, min(size.width * factor, maxValue))
            }
            
            if !self.validatedGesture {
                if self.firstLocation.x < edgeWidth && !self.currentAllowedDirections.contains(.rightEdge) && !self.currentAllowedDirections.contains(.leftCenter) {
                    self.state = .failed
                    return
                }
                if self.firstLocation.x > size.width - edgeWidth && !self.currentAllowedDirections.contains(.leftEdge) && !self.currentAllowedDirections.contains(.rightCenter) {
                    self.state = .failed
                    return
                }
                
                // AYG: require a horizontal MAJORITY, not a strictly
                // horizontal vector. A natural thumb swipe on a half-screen
                // sheet is diagonal; absX > absY rejected anything past 45deg.
                // A clearly vertical flick is still caught by the axis lock
                // below, so this only needs to reject a vertical majority.
                let horizontalIntent = translation.x > self.minimumCenterMovement && absTranslationX > absTranslationY * 0.7
                let leftwardIntent = translation.x < -self.minimumCenterMovement && absTranslationX > absTranslationY * 0.7
                if self.currentAllowedDirections.contains(.rightEdge) && self.firstLocation.x < edgeWidth && horizontalIntent {
                    self.validatedGesture = true
                } else if self.currentAllowedDirections.contains(.leftEdge) && self.firstLocation.x > size.width - edgeWidth && leftwardIntent {
                    self.validatedGesture = true
                } else if !self.currentAllowedDirections.contains(.leftCenter) && translation.x < -self.initialDirectionTolerance {
                    self.state = .failed
                } else if !self.currentAllowedDirections.contains(.rightCenter) && translation.x > self.initialDirectionTolerance {
                    self.state = .failed
                } else {
                    let totalMovement = sqrt(absTranslationX * absTranslationX + absTranslationY * absTranslationY)
                    if totalMovement > 8.0 {
                        // AYG: same relaxed majority as the edge path. A
                        // diagonal swipe must still commit to the pop.
                        if absTranslationX >= absTranslationY * 0.7 {
                            self.validatedGesture = true
                            fireBegan = true
                        } else {
                            self.state = .failed
                        }
                    } else if absTranslationY > self.initialVerticalRejectionDistance && absTranslationY > absTranslationX * 2.0 {
                        self.state = .failed
                    } else if absTranslationX > self.minimumCenterMovement && absTranslationY * 1.4 < absTranslationX {
                        self.validatedGesture = true
                        fireBegan = true
                    }
                }
            }
        }
        
        if self.validatedGesture {
            // AYG: axis lock only while the gesture is still being decided.
            // After 40pt the pop is committed and a diagonal continuation
            // must NOT cancel it - the previous lock ran on every move and
            // killed almost any slightly angled swipe. Thresholds are loose
            // on purpose: only a fast, clearly vertical flick fails (the
            // original "scrolling down starts a pop" symptom).
            if !self.currentAllowedDirections.contains(.down) {
                let totalMovement = sqrt(absTranslationX * absTranslationX + absTranslationY * absTranslationY)
                if totalMovement < 40.0 {
                    let velocity = self.velocity(in: self.view)
                    if abs(velocity.y) > abs(velocity.x) * 2.2, abs(velocity.y) > 450.0 {
                        self.state = .cancelled
                        return
                    }
                }
            }
            super.touchesMoved(touches, with: event)
            if fireBegan {
                if self.state == .possible {
                    self.state = .began
                }
            }
        }
    }
}
