# iOS SwiftUI 集成示例

`GroupChatDemo.swift` 演示如何在 iOS App 里连一个 group-chat-oss 后端:拉历史、显示消息、选择消息类型、发送、下拉刷新。

## 用法

1. 把 `GroupChatDemo.swift` 拖进你自己的 Xcode 工程。
2. 在 App 入口展示 `GroupChatDemoView`:

   ```swift
   GroupChatDemoView(config: GroupChatConfig(
       baseURL: URL(string: "http://你的后端地址:8895")!,
       authToken: "你的 token",
       senderID: "ios-user"
   ))
   ```

3. 运行:它会拉 `/group/history` 显示消息,输入框走 `/group/send` 发送,列表下拉刷新。

## 消息类型

示例输入栏暴露 5 类:

- `Chat`: 闲聊。适合 casual room,不会自动变成任务。
- `Task`: 工作任务。适合 work room,会进入任务板。
- `Review`: 复审请求。适合 code/work room,需要 reviewer ACK、给 P0/P1/P2 comments,最后 `ALL_CLEAR`。
- `Question`: 问题。适合需要回答但不一定立项的内容。
- `Broadcast`: 公告。适合通知全群,不要用它暗中派活。

如果你只做闲聊群,默认停在 `Chat` 即可。如果你做工作群,建议把 `Task`
和 `Review` 放在最容易点到的位置,并在任务详情页显示 ACK 状态、P0/P1/P2
和 `ALL_CLEAR`。

## 说明

- 这是最小参考示例,不含实时推送;生产里可加 `/group/poll` 长轮询,或接你自己的推送通道。
- `GroupMessage` 的字段按你后端 `/group/history` 的实际返回微调即可。
- 鉴权统一走请求头 `X-Auth-Token`。
