# iOS SwiftUI 集成示例

`GroupChatDemo.swift` 演示如何在 iOS App 里连一个 group-chat-oss 后端:拉历史、显示消息、发送、下拉刷新。

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

## 说明

- 这是最小参考示例,不含实时推送;生产里可加 `/group/poll` 长轮询,或接你自己的推送通道。
- `GroupMessage` 的字段按你后端 `/group/history` 的实际返回微调即可。
- 鉴权统一走请求头 `X-Auth-Token`。
