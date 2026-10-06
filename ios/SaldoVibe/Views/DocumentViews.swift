import SwiftUI

struct AccountPickerView: View {
    let accounts: [Account]
    @Binding var selection: Account?
    /// Show only cost accounts (class 4–8) until the user searches.
    var preferCost = true
    @State private var search = ""
    @Environment(\.dismiss) private var dismiss

    private var shown: [Account] {
        let query = search.trimmingCharacters(in: .whitespaces)
        if query.isEmpty {
            return preferCost ? accounts.filter(\.isCostAccount) : accounts
        }
        return accounts.filter { $0.number.hasPrefix(query) || $0.name.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        List {
            Section {
                ForEach(shown) { account in
                    Button {
                        selection = account
                        dismiss()
                    } label: {
                        HStack {
                            Text(account.number).monospacedDigit().foregroundStyle(.secondary)
                            Text(account.name)
                            Spacer()
                            if account.id == selection?.id {
                                Image(systemName: "checkmark").foregroundStyle(.tint)
                            }
                        }
                    }
                    .tint(.primary)
                }
            } footer: {
                if preferCost && search.isEmpty {
                    Text("Kostnadskonton (klass 4–8). Sök på nummer eller namn för övriga konton.")
                }
            }
        }
        .searchable(text: $search, prompt: "Kontonummer eller namn")
        .overlay {
            if shown.isEmpty {
                ContentUnavailableView.search(text: search)
            }
        }
        .navigationTitle("Konto")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct AccountRow: View {
    let title: String
    let account: Account?

    var body: some View {
        LabeledContent(title) {
            Text(account?.label ?? "Välj…").foregroundStyle(account == nil ? .secondary : .primary)
        }
    }
}

/// Detail page for an expense, supplier invoice or customer invoice: document rows from the
/// caller, then payment state, attachments and the actions the role allows.
struct DocumentDetailView<P: Payable & Decodable, Rows: View>: View {
    @Environment(Session.self) private var session
    let path: String
    let title: String
    let canRegister: Bool
    /// Drafts (not yet bookkept) can be deleted.
    var canDelete = false
    @ViewBuilder let rows: (P) -> Rows
    @State private var item: P?
    @State private var error: String?
    @State private var paying = false
    @State private var busy = false
    @State private var confirmingDelete = false
    @Environment(\.dismiss) private var dismiss

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
                if !(session.company?.readOnly ?? false) && (!item.isBookkept || !item.isPaid) {
                    Section {
                        if canRegister && !item.isBookkept {
                            Button {
                                Task { await register() }
                            } label: {
                                Label("Bokför", systemImage: "checkmark.seal")
                            }
                            .disabled(busy)
                        }
                        if item.isBookkept && !item.isPaid {
                            Button {
                                paying = true
                            } label: {
                                Label("Registrera betalning", systemImage: "banknote")
                            }
                        }
                        if canDelete && !item.isBookkept {
                            Button(role: .destructive) {
                                confirmingDelete = true
                            } label: {
                                Label("Ta bort utkast", systemImage: "trash")
                            }
                            .disabled(busy)
                        }
                    }
                }
            } else if let error {
                ContentUnavailableView("Kunde inte hämta", systemImage: "wifi.exclamationmark", description: Text(error))
            } else {
                ProgressView()
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $paying) {
            if let item {
                PaymentSheet(payable: item, path: path + "betalning/") { updated in
                    self.item = updated
                    session.didChange()
                }
            }
        }
        .confirmationDialog("Ta bort utkastet?", isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Ta bort", role: .destructive) { Task { await delete() } }
        }
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

    private func delete() async {
        guard let api = session.api else { return }
        busy = true
        defer { busy = false }
        do {
            try await api.delete(path)
            session.didChange()
            dismiss()
        } catch {
            self.error = session.describe(error)
        }
    }

    private func register() async {
        guard let api = session.api else { return }
        busy = true
        defer { busy = false }
        do {
            item = try await api.post(path + "bokfor/")
            error = nil
            session.didChange()
        } catch {
            self.error = session.describe(error)
        }
    }
}

struct PaymentSheet<P: Payable & Decodable>: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    let payable: P
    let path: String
    let onDone: (P) -> Void
    @State private var date = Date()
    @State private var amount = ""
    @State private var account: Account?
    @State private var choices: FormChoices?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    DatePicker("Betalningsdatum", selection: $date, displayedComponents: .date)
                    AmountField("Belopp", text: $amount)
                    NavigationLink {
                        AccountPickerView(accounts: choices?.paymentAccounts ?? [], selection: $account, preferCost: false)
                    } label: {
                        AccountRow(title: "Betalkonto", account: account)
                    }
                } footer: {
                    Text("Kvar att betala: \(payable.remainingAmount.formatted). En avvikelse på högst 1 kr skrivs av som öresavrundning; ett mindre belopp lämnar posten delbetald.")
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Registrera betalning")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Avbryt") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Registrera") { Task { await submit() } }
                        .disabled(busy || account == nil || Amount.parse(amount) == nil)
                }
            }
            .task {
                amount = payable.remainingAmount.value.formatted(.number.precision(.fractionLength(2)).grouping(.never).locale(Locale(identifier: "sv_SE")))
                do {
                    choices = try await session.loadChoices()
                    account = choices?.defaultPaymentAccount ?? choices?.paymentAccounts.first
                } catch {
                    self.error = session.describe(error)
                }
            }
        }
    }

    private func submit() async {
        guard let api = session.api, let value = Amount.parse(amount) else { return }
        busy = true
        defer { busy = false }
        do {
            let updated: P = try await api.post(path, json: [
                "payment_date": ISODate.string(date),
                "amount": Amount.apiString(value),
                "payment_account": account?.id ?? 0,
            ])
            onDone(updated)
            dismiss()
        } catch {
            self.error = session.describe(error)
        }
    }
}
