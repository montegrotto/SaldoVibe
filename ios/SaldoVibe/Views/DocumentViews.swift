import SwiftUI

/// Detail page for an expense, supplier invoice or customer invoice: document rows from the
/// caller, then payment state and attachments. Read-only – booking and payment happen on the web.
struct DocumentDetailView<P: Payable & Decodable, Rows: View>: View {
    @Environment(Session.self) private var session
    let path: String
    let title: String
    @ViewBuilder let rows: (P) -> Rows
    @State private var item: P?
    @State private var error: String?

    var body: some View {
        List {
            if let item {
                rows(item)
                Section("Betalning") {
                    LabeledContent("Status") { StatusBadge(status: item.status, label: item.statusLabel) }
                    LabeledContent("Totalt", value: item.totalAmount.formatted)
                    if item.paidAmount.value > 0 {
                        LabeledContent("Betalt", value: item.paidAmount.formatted)
                    }
                    if !item.isPaid {
                        LabeledContent("Kvar att betala", value: item.remainingAmount.formatted)
                    }
                    if let date = item.paymentDate {
                        LabeledContent("Betaldatum", value: ISODate.display(date))
                    }
                }
                if let attachments = item.attachments, !attachments.isEmpty {
                    Section("Bilagor") {
                        ForEach(attachments) { attachment in
                            NavigationLink {
                                AttachmentDetailView(attachment: attachment, allowRegister: false)
                            } label: {
                                HStack(spacing: 12) {
                                    AttachmentThumbnail(attachment: attachment, size: 44)
                                    Text(attachment.fileName).lineLimit(1)
                                }
                            }
                        }
                    }
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            } else if let error {
                ContentUnavailableView("Kunde inte hämta", systemImage: "wifi.exclamationmark", description: Text(error))
            } else {
                ProgressView()
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            item = try await api.get(path)
            error = nil
        } catch {
            self.error = session.describe(error)
        }
    }
}
