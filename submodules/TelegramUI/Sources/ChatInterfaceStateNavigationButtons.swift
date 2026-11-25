import Foundation
import UIKit
import AsyncDisplayKit
import Display
import TelegramCore
import TelegramPresentationData
import AccountContext
import ChatPresentationInterfaceState
import ChatNavigationButton

func leftNavigationButtonForChatInterfaceState(_ presentationInterfaceState: ChatPresentationInterfaceState, subject: ChatControllerSubject?, strings: PresentationStrings, currentButton: ChatNavigationButton?, target: Any?, selector: Selector?) -> ChatNavigationButton? {
    if let _ = presentationInterfaceState.interfaceState.selectionState {
        if case .messageOptions = presentationInterfaceState.subject {
            return nil
        }
        if let _ = presentationInterfaceState.reportReason {
            return ChatNavigationButton(action: .spacer, buttonItem: UIBarButtonItem(title: " ", style: .plain, target: nil, action: nil))
        }
        let hasSelection = !(presentationInterfaceState.interfaceState.selectionState?.selectedIds.isEmpty ?? true)
        let button: ChatNavigationButton
        if let currentButton, currentButton.action == .copySelectedMessages {
            button = currentButton
        } else {
            let item = UIBarButtonItem(title: strings.Conversation_ContextMenuCopy, style: .plain, target: target, action: selector)
            item.accessibilityLabel = strings.Conversation_ContextMenuCopy
            button = ChatNavigationButton(action: .copySelectedMessages, buttonItem: item)
        }
        button.buttonItem.isEnabled = hasSelection && !presentationInterfaceState.copyProtectionEnabled
        return button
    }
    
    if case let .customChatContents(customChatContents) = presentationInterfaceState.subject {
        switch customChatContents.kind {
        case .hashTagSearch:
            break
        case .quickReplyMessageInput, .businessLinkSetup, .welcomeMessages:
            if let currentButton = currentButton, currentButton.action == .dismiss {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(title: "___close", style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Common_Close
                return ChatNavigationButton(action: .dismiss, buttonItem: buttonItem)
            }
        }
    }
    
    return nil
}

func rightNavigationButtonForChatInterfaceState(context: AccountContext, presentationInterfaceState: ChatPresentationInterfaceState, strings: PresentationStrings, currentButton: ChatNavigationButton?, target: Any?, selector: Selector?, chatInfoNavigationButton: ChatNavigationButton?, moreInfoNavigationButton: ChatNavigationButton?, sideListKind: ChatSideListKind?) -> ChatNavigationButton? {
    if case .standard(.previewing) = presentationInterfaceState.mode {
        return nil
    }
    var hasMessages = false
    if let chatHistoryState = presentationInterfaceState.chatHistoryState {
        if case .loaded(false, _) = chatHistoryState {
            hasMessages = true
        }
    }
    
    if let _ = presentationInterfaceState.interfaceState.selectionState {
        if case .messageOptions = presentationInterfaceState.subject {
            return nil
        }
        if let currentButton = currentButton, currentButton.action == .cancelMessageSelection {
            return currentButton
        } else {
            let buttonItem = UIBarButtonItem(title: strings.Common_Cancel, style: .plain, target: target, action: selector)
            buttonItem.accessibilityLabel = strings.Common_Cancel
            return ChatNavigationButton(action: .cancelMessageSelection, buttonItem: buttonItem)
        }
    }
    
    if sideListKind != nil {
        if let currentButton, currentButton.action == .toggleSideList {
            return currentButton
        } else {
            let buttonItem = UIBarButtonItem(image: generateTintedImage(image: UIImage(bundleImageName: "Chat/Title Panels/SideListIcon"), color: presentationInterfaceState.theme.rootController.navigationBar.primaryTextColor), style: .plain, target: target, action: selector)
            buttonItem.accessibilityLabel = strings.Chat_SideList_Open
            return ChatNavigationButton(action: .toggleSideList, buttonItem: buttonItem)
        }
    }
    
    if case let .replyThread(message) = presentationInterfaceState.chatLocation, message.peerId == context.account.peerId {
        let isTags = presentationInterfaceState.hasSearchTags
        
        if case .search(isTags) = currentButton?.action {
            return currentButton
        } else {
            let buttonItem = UIBarButtonItem(image: isTags ? PresentationResourcesRootController.navigationCompactTagsSearchIcon(presentationInterfaceState.theme) : PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
            buttonItem.accessibilityLabel = strings.Conversation_Search
            return ChatNavigationButton(action: .search(hasTags: isTags), buttonItem: buttonItem)
        }
    }
    
    if let channel = presentationInterfaceState.renderedPeer?.peer as? TelegramChannel, channel.isMonoForum, case .peer = presentationInterfaceState.chatLocation {
        let displaySearch = hasMessages
        
        if displaySearch {
            if case .search(false) = currentButton?.action {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(image: PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Conversation_Search
                return ChatNavigationButton(action: .search(hasTags: false), buttonItem: buttonItem)
            }
        } else {
            return nil
        }
    }
    
    if let user = presentationInterfaceState.renderedPeer?.peer as? TelegramUser, user.isForum, case .peer = presentationInterfaceState.chatLocation {
        let displaySearch = hasMessages
        
        if displaySearch {
            if case .search(false) = currentButton?.action {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(image: PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Conversation_Search
                return ChatNavigationButton(action: .search(hasTags: false), buttonItem: buttonItem)
            }
        } else {
            return nil
        }
    }
    
    if let channel = presentationInterfaceState.renderedPeer?.peer as? TelegramChannel, channel.isForumOrMonoForum, let moreInfoNavigationButton = moreInfoNavigationButton {
        if case .replyThread = presentationInterfaceState.chatLocation {
        } else {
            if case .pinnedMessages = presentationInterfaceState.subject {
            } else {
                return moreInfoNavigationButton
            }
        }
    }
    
    if let user = presentationInterfaceState.renderedPeer?.peer as? TelegramUser, let botInfo = user.botInfo, botInfo.flags.contains(.hasForum), let moreInfoNavigationButton = moreInfoNavigationButton {
        if case .pinnedMessages = presentationInterfaceState.subject {
        } else {
            return moreInfoNavigationButton
        }
    }
    
    if case .messageOptions = presentationInterfaceState.subject {
        return nil
    }
    
    if case .pinnedMessages = presentationInterfaceState.subject {
        return nil
    }
    
    if case let .customChatContents(customChatContents) = presentationInterfaceState.subject {
        switch customChatContents.kind {
        case .hashTagSearch:
            return nil
        case let .quickReplyMessageInput(_, shortcutType):
            switch shortcutType {
            case .generic:
                if let currentButton = currentButton, currentButton.action == .edit {
                    return currentButton
                } else {
                    let buttonItem = UIBarButtonItem(title: strings.Common_Edit, style: .plain, target: target, action: selector)
                    buttonItem.accessibilityLabel = strings.Common_Done
                    return ChatNavigationButton(action: .edit, buttonItem: buttonItem)
                }
            case .greeting, .away:
                return nil
            }
        case .businessLinkSetup:
            if let currentButton = currentButton, currentButton.action == .edit {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(title: strings.Common_Edit, style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Common_Done
                return ChatNavigationButton(action: .edit, buttonItem: buttonItem)
            }
        case .welcomeMessages:
            return nil
        }
    }
    
    if case .replyThread = presentationInterfaceState.chatLocation {
        if let channel = presentationInterfaceState.renderedPeer?.peer as? TelegramChannel, channel.isForumOrMonoForum {
        } else if hasMessages {
            if case .search = currentButton?.action {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(image: PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Conversation_Search
                return ChatNavigationButton(action: .search(hasTags: false), buttonItem: buttonItem)
            }
        } else {
            if case .spacer = currentButton?.action {
                return currentButton
            } else {
                return ChatNavigationButton(action: .spacer, buttonItem: UIBarButtonItem(title: "", style: .plain, target: target, action: selector))
            }
        }
    }
    if case let .peer(peerId) = presentationInterfaceState.chatLocation {
        if peerId.isRepliesOrVerificationCodes {
            if hasMessages {
                if case .search = currentButton?.action {
                    return currentButton
                } else {
                    let buttonItem = UIBarButtonItem(image: PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                    buttonItem.accessibilityLabel = strings.Conversation_Search
                    return ChatNavigationButton(action: .search(hasTags: false), buttonItem: buttonItem)
                }
            } else {
                if case .spacer = currentButton?.action {
                    return currentButton
                } else {
                    return ChatNavigationButton(action: .spacer, buttonItem: UIBarButtonItem(title: "", style: .plain, target: target, action: selector))
                }
            }
        }
    }
    
    if case .scheduledMessages = presentationInterfaceState.subject {
        return chatInfoNavigationButton
    }
    
    if case .standard(.previewing) = presentationInterfaceState.mode {
        return chatInfoNavigationButton
    } else if let peerId = presentationInterfaceState.chatLocation.peerId {
        if presentationInterfaceState.accountPeerId == peerId {
            var displaySearchButton = false
            
            if case .replyThread = presentationInterfaceState.chatLocation {
                displaySearchButton = true
            }
            
            if case .scheduledMessages = presentationInterfaceState.subject {
                return chatInfoNavigationButton
            } else {
                displaySearchButton = true
            }
            
            if displaySearchButton {
                let isTags = presentationInterfaceState.hasSearchTags
                
                if case .search(isTags) = currentButton?.action {
                    return currentButton
                } else {
                    let buttonItem = UIBarButtonItem(image: isTags ? PresentationResourcesRootController.navigationCompactTagsSearchIcon(presentationInterfaceState.theme) : PresentationResourcesRootController.navigationCompactSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                    buttonItem.accessibilityLabel = strings.Conversation_Search
                    return ChatNavigationButton(action: .search(hasTags: isTags), buttonItem: buttonItem)
                }
            }
        }
    }

    return chatInfoNavigationButton
}

func secondaryRightNavigationButtonForChatInterfaceState(context: AccountContext, presentationInterfaceState: ChatPresentationInterfaceState, strings: PresentationStrings, currentButton: ChatNavigationButton?, target: Any?, selector: Selector?, chatInfoNavigationButton: ChatNavigationButton?, moreInfoNavigationButton: ChatNavigationButton?, sideListKind: ChatSideListKind?) -> ChatNavigationButton? {
    if presentationInterfaceState.interfaceState.selectionState != nil {
        return nil
    }
    if let sideListKind {
        // Saved Messages keeps its search beside the sidebar button.
        if case .savedMessages = sideListKind {
            let isTags = presentationInterfaceState.hasSearchTags
            if case .search(isTags) = currentButton?.action {
                return currentButton
            } else {
                let buttonItem = UIBarButtonItem(image: isTags ? PresentationResourcesRootController.navigationSearchTagIcon(presentationInterfaceState.theme) : PresentationResourcesRootController.navigationSearchIcon(presentationInterfaceState.theme), style: .plain, target: target, action: selector)
                buttonItem.accessibilityLabel = strings.Conversation_Search
                return ChatNavigationButton(action: .search(hasTags: isTags), buttonItem: buttonItem)
            }
        }
        return nil
    }
    if case .standard(.default) = presentationInterfaceState.mode {
        if case .peer(context.account.peerId) = presentationInterfaceState.chatLocation, presentationInterfaceState.subject != .scheduledMessages, presentationInterfaceState.hasSavedChats {
            return moreInfoNavigationButton
        }
    }
    
    return nil
}
