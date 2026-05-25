// GroupChatDemo.swift
//
// group-chat-oss 的 iOS 集成示例(SwiftUI)。
// 演示如何连一个 group-chat-oss 后端:拉取历史、显示消息、发送消息。
// 这是参考示例,不是完整 App——把它丢进你自己的 Xcode 工程即可用。
//
// 后端默认接口(见 README):
//   GET  /group/history   -> 历史消息
//   POST /group/send      -> 发送消息
// 鉴权: 请求头 X-Auth-Token: <你的 token>

import SwiftUI

// MARK: - 配置

struct GroupChatConfig {
    /// 例如 URL(string: "http://127.0.0.1:8895")!
    var baseURL: URL
    /// 与后端 config 里的 token 一致
    var authToken: String
    /// 本客户端在群里的身份 id
    var senderID: String = "ios-user"
}

// MARK: - 数据模型

/// 跟后端 /group/history 返回的消息字段对齐;字段名按你的后端实际返回微调。
struct GroupMessage: Identifiable, Decodable {
    let id: Int
    let senderID: String
    let text: String
    let ts: String?

    enum CodingKeys: String, CodingKey {
        case id
        case senderID = "sender_id"
        case text
        case ts
    }
}

/// /group/history 可能返回 {"messages": [...]} 或直接数组,这里两种都兼容。
private struct HistoryResponse: Decodable {
    let messages: [GroupMessage]?
}

// MARK: - ViewModel

@MainActor
final class GroupChatViewModel: ObservableObject {
    @Published var messages: [GroupMessage] = []
    @Published var draft: String = ""
    @Published var errorText: String?

    private let config: GroupChatConfig

    init(config: GroupChatConfig) {
        self.config = config
    }

    private func makeRequest(_ path: String, method: String = "GET", json: [String: Any]? = nil) -> URLRequest {
        var req = URLRequest(url: config.baseURL.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue(config.authToken, forHTTPHeaderField: "X-Auth-Token")
        if let json {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: json)
        }
        return req
    }

    func loadHistory() async {
        do {
            let (data, _) = try await URLSession.shared.data(for: makeRequest("group/history"))
            if let wrapped = try? JSONDecoder().decode(HistoryResponse.self, from: data),
               let list = wrapped.messages {
                messages = list
            } else if let list = try? JSONDecoder().decode([GroupMessage].self, from: data) {
                messages = list
            }
            errorText = nil
        } catch {
            errorText = "加载失败: \(error.localizedDescription)"
        }
    }

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        do {
            _ = try await URLSession.shared.data(
                for: makeRequest("group/send", method: "POST",
                                 json: ["sender_id": config.senderID, "text": text])
            )
            await loadHistory()
        } catch {
            errorText = "发送失败: \(error.localizedDescription)"
        }
    }
}

// MARK: - View

struct GroupChatDemoView: View {
    @StateObject private var vm: GroupChatViewModel

    init(config: GroupChatConfig) {
        _vm = StateObject(wrappedValue: GroupChatViewModel(config: config))
    }

    var body: some View {
        VStack(spacing: 0) {
            if let err = vm.errorText {
                Text(err).font(.caption).foregroundStyle(.red).padding(.top, 4)
            }
            List(vm.messages) { msg in
                VStack(alignment: .leading, spacing: 2) {
                    Text(msg.senderID).font(.caption).foregroundStyle(.secondary)
                    Text(msg.text)
                }
                .padding(.vertical, 2)
            }
            .listStyle(.plain)
            .refreshable { await vm.loadHistory() }

            HStack(spacing: 8) {
                TextField("说点什么…", text: $vm.draft, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                Button("发送") { Task { await vm.send() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.draft.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding()
        }
        .task { await vm.loadHistory() }
    }
}

// MARK: - 用法示例
//
// struct DemoApp: App {
//     var body: some Scene {
//         WindowGroup {
//             GroupChatDemoView(config: GroupChatConfig(
//                 baseURL: URL(string: "http://127.0.0.1:8895")!,
//                 authToken: "test-token",
//                 senderID: "ios-user"
//             ))
//         }
//     }
// }
