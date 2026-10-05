import SwiftUI

struct ExpensesView: View {
    @Environment(Session.self) private var session
    @State private var expenses: [Expense] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var showAll = false
    @State private var creating = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(expenses) { expense in
                    NavigationLink(value: expense.id) {
                        PayableRow(item: expense)
                    }
                }
            }
            .navigationDestination(for: Int.self) { id in
                ExpenseDetailView(id: id)
            }
            .navigationTitle("Utlägg")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Toggle("Visa alla", isOn: $showAll).toggleStyle(.button).controlSize(.small)
                }
                if !(session.company?.readOnly ?? false) {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Nytt utlägg", systemImage: "plus") { creating = true }
                    }
                }
            }
            .overlay {
                if loaded && expenses.isEmpty && error == nil {
                    ContentUnavailableView(
                        showAll ? "Inga utlägg" : "Inga obetalda utlägg",
                        systemImage: "creditcard",
                        description: Text(showAll ? "Registrera det första med plusknappen." : "Visa alla tar med utbetalda utlägg.")
                    )
                } else if let error, expenses.isEmpty {
                    ContentUnavailableView("Kunde inte hämta utlägg", systemImage: "wifi.exclamationmark", description: Text(error))
                }
            }
            .sheet(isPresented: $creating) {
                ExpenseFormView()
            }
            .onChange(of: showAll) { Task { await load() } }
            .reloads(on: session.changeCounter) { await load() }
        }
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            expenses = try await api.get("utlagg/", query: showAll ? ["visa": "alla"] : [:])
            error = nil
        } catch {
            self.error = session.describe(error)
        }
        loaded = true
    }
}

struct ExpenseDetailView: View {
    let id: Int

    var body: some View {
        DocumentDetailView<Expense, _>(path: "utlagg/\(id)/", title: "Utlägg", canRegister: true) { expense in
            Section("Utlägg") {
                LabeledContent("Beskrivning", value: expense.description)
                LabeledContent("Datum", value: ISODate.display(expense.expenseDate))
                LabeledContent("Person", value: expense.person)
                if let account = expense.expenseAccount {
                    LabeledContent("Kostnadskonto", value: account.label)
                }
                LabeledContent("Exkl. moms", value: expense.amountExVat.formatted)
                LabeledContent("Moms", value: expense.vatAmount.formatted)
            }
        }
    }
}

struct ExpenseFormView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    var prefill: Attachment?
    var onSaved: () -> Void = {}

    @State private var choices: FormChoices?
    @State private var description = ""
    @State private var date = Date()
    @State private var total = ""
    @State private var vat = ""
    @State private var account: Account?
    @State private var employee: NamedItem?
    @State private var personName = ""
    @State private var attachments: [Attachment] = []
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Utlägg") {
                    TextField("Beskrivning", text: $description)
                    DatePicker("Datum", selection: $date, displayedComponents: .date)
                    AmountField("Totalbelopp", text: $total)
                    if choices?.vatRegistered ?? true {
                        AmountField("Varav moms", text: $vat)
                    }
                    NavigationLink {
                        AccountPickerView(accounts: choices?.accounts ?? [], selection: $account)
                    } label: {
                        AccountRow(title: "Kostnadskonto", account: account)
                    }
                }
                Section("Vem gjorde utlägget?") {
                    Picker("Anställd", selection: $employee) {
                        Text("Annan person").tag(nil as NamedItem?)
                        ForEach(choices?.employees ?? []) { employee in
                            Text(employee.name).tag(Optional(employee))
                        }
                    }
                    if employee == nil {
                        TextField("Namn", text: $personName)
                    }
                }
                AttachmentsSection(attachments: $attachments, onSuggestion: apply)
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
                Section {
                    Button {
                        Task { await save(register: true) }
                    } label: {
                        Label("Bokför", systemImage: "checkmark.seal")
                    }
                    Button {
                        Task { await save(register: false) }
                    } label: {
                        Label("Spara som utkast", systemImage: "tray")
                    }
                } footer: {
                    Text("Bokför registrerar utlägget direkt med samma konton och kontroller som på webben.")
                }
                .disabled(busy || description.isEmpty || Amount.parse(total) == nil || account == nil)
            }
            .navigationTitle("Nytt utlägg")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Avbryt") { dismiss() } }
            }
            .task {
                do {
                    choices = try await session.loadChoices()
                } catch {
                    self.error = session.describe(error)
                }
                if let prefill, attachments.isEmpty {
                    attachments = [prefill]
                    if let suggestion = prefill.suggestion {
                        apply(suggestion)
                    }
                }
            }
        }
    }

    private func apply(_ suggestion: Suggestion) {
        if description.isEmpty, let vendor = suggestion.vendor { description = vendor }
        if total.isEmpty, let value = suggestion.total { total = Amount.apiString(value) }
        if vat.isEmpty, let value = suggestion.vat { vat = Amount.apiString(value) }
        if let parsed = ISODate.parse(suggestion.date) { date = parsed }
    }

    private func save(register: Bool) async {
        guard let api = session.api, let totalValue = Amount.parse(total) else { return }
        busy = true
        defer { busy = false }
        error = nil
        do {
            let _: Expense = try await api.post("utlagg/", json: jsonObject([
                "description": description,
                "expense_date": ISODate.string(date),
                "total_amount": Amount.apiString(totalValue),
                "vat_amount": Amount.apiString(Amount.parse(vat) ?? 0),
                "expense_account": account?.id,
                "employee": employee?.id,
                "person_name": employee == nil ? personName : "",
                "attachment_ids": attachments.map(\.id),
                "register": register,
            ]))
            session.didChange()
            onSaved()
            dismiss()
        } catch {
            self.error = session.describe(error)
        }
    }
}
