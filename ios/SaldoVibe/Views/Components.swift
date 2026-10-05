import SwiftUI

struct StatusBadge: View {
    let status: String
    let label: String

    private var color: Color {
        switch status {
        case "paid": .green
        case "partial": .blue
        case "bookkept": .orange
        default: .gray
        }
    }

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

struct PayableRow<P: Payable>: View {
    let item: P

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.body.weight(.medium)).lineLimit(1)
                Text(item.subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing: 6) {
                    Text(ISODate.display(item.dateLabel))
                    if item.isOverdue {
                        Text("Förfallen").fontWeight(.semibold)
                    }
                }
                .font(.caption)
                .foregroundStyle(item.isOverdue ? .red : .secondary)
            }
            Spacer(minLength: 0)
            VStack(alignment: .trailing, spacing: 4) {
                Text(item.totalAmount.formatted).monospacedDigit()
                StatusBadge(status: item.status, label: item.statusLabel)
            }
        }
        .padding(.vertical, 2)
    }
}

struct AmountField: View {
    let title: String
    @Binding var text: String

    init(_ title: String, text: Binding<String>) {
        self.title = title
        _text = text
    }

    var body: some View {
        LabeledContent(title) {
            TextField("0,00", text: $text)
                .keyboardType(.decimalPad)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
        }
    }
}

struct ErrorText: View {
    let message: String?

    var body: some View {
        if let message {
            Text(message).foregroundStyle(.red).font(.footnote)
        }
    }
}

/// Image fetched with the session's token (AsyncImage can't send headers), cached in memory.
struct AuthenticatedImage: View {
    @Environment(Session.self) private var session
    let path: String
    @State private var image: UIImage?
    @State private var failed = false

    private static let cache = NSCache<NSString, UIImage>()

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else if failed {
                Image(systemName: "doc").font(.title2).foregroundStyle(.secondary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .task(id: path) {
            if let cached = Self.cache.object(forKey: path as NSString) {
                image = cached
                return
            }
            guard let api = session.api, let data = try? await api.data(path), let loaded = UIImage(data: data) else {
                failed = true
                return
            }
            Self.cache.setObject(loaded, forKey: path as NSString)
            image = loaded
        }
    }
}

struct AttachmentThumbnail: View {
    let attachment: Attachment
    var size: CGFloat = 56

    var body: some View {
        AuthenticatedImage(path: attachment.thumbnailPath)
            .frame(width: size, height: size)
            .background(Color(.secondarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct AttachmentRow: View {
    let attachment: Attachment

    var body: some View {
        HStack(spacing: 12) {
            AttachmentThumbnail(attachment: attachment)
            VStack(alignment: .leading, spacing: 2) {
                Text(attachment.fileName).lineLimit(1)
                Text(ISODate.display(String(attachment.uploadedAt.prefix(10))))
                    .font(.caption).foregroundStyle(.secondary)
                if let suggestion = attachment.suggestion, suggestion.vendor != nil || suggestion.total != nil {
                    Text([suggestion.vendor, suggestion.total.map { Amount($0).formatted }].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
    }
}

/// Drops nil values so the dictionary can go through JSONSerialization.
func jsonObject(_ pairs: [String: Any?]) -> [String: Any] {
    pairs.compactMapValues { $0 }
}

extension View {
    /// Pull-to-refresh plus reload whenever the session says something changed.
    func reloads(on counter: Int, _ action: @escaping () async -> Void) -> some View {
        task(id: counter) { await action() }.refreshable { await action() }
    }
}
