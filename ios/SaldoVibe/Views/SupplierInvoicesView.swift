import SwiftUI

struct SupplierInvoicesList: View {
    @Environment(Session.self) private var session
    @State private var invoices: [SupplierInvoice] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var showAll = false
    @State private var creating = false

    var body: some View {
        List {
            ForEach(invoices) { invoice in
                NavigationLink(value: invoice.id) {
                    PayableRow(item: invoice)
                }
            }
        }
        .navigationDestination(for: Int.self) { id in
            SupplierInvoiceDetailView(id: id)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Toggle("Visa alla", isOn: $showAll).toggleStyle(.button).controlSize(.small)
            }
            if !(session.company?.readOnly ?? false) {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Ny leverantörsfaktura", systemImage: "plus") { creating = true }
                }
            }
        }
        .overlay {
            if loaded && invoices.isEmpty && error == nil {
                ContentUnavailableView(
                    showAll ? "Inga leverantörsfakturor" : "Inga obetalda leverantörsfakturor",
                    systemImage: "doc.text",
                    description: Text(showAll ? "Fotografera en faktura med plusknappen." : "Visa alla tar med betalda fakturor.")
                )
            } else if let error, invoices.isEmpty {
                ContentUnavailableView("Kunde inte hämta fakturor", systemImage: "wifi.exclamationmark", description: Text(error))
            }
        }
        .sheet(isPresented: $creating) {
            SupplierInvoiceFormView()
        }
        .onChange(of: showAll) { Task { await load() } }
        .reloads(on: session.changeCounter) { await load() }
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            invoices = try await api.get("leverantorsfakturor/", query: showAll ? ["visa": "alla"] : [:])
            error = nil
        } catch {
            self.error = session.describe(error)
        }
        loaded = true
    }
}

struct SupplierInvoiceDetailView: View {
    let id: Int

    var body: some View {
        DocumentDetailView<SupplierInvoice, _>(path: "leverantorsfakturor/\(id)/", title: "Leverantörsfaktura", canRegister: true) { invoice in
            Section("Faktura") {
                LabeledContent("Leverantör", value: invoice.supplierName)
                if !invoice.invoiceNumber.isEmpty {
                    LabeledContent("Fakturanummer", value: invoice.invoiceNumber)
                }
                if !invoice.ocrCode.isEmpty {
                    LabeledContent("OCR", value: invoice.ocrCode)
                }
                LabeledContent("Fakturadatum", value: ISODate.display(invoice.invoiceDate))
                LabeledContent("Förfallodatum") {
                    Text(ISODate.display(invoice.dueDate)).foregroundStyle(invoice.isOverdue ? .red : .primary)
                }
                LabeledContent("Exkl. moms", value: invoice.amountExVat.formatted)
                LabeledContent("Moms", value: invoice.vatAmount.formatted)
            }
            if let lines = invoice.costLines, !lines.isEmpty {
                Section("Kostnadskonton") {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        LabeledContent(line.account?.label ?? "–", value: line.amount.formatted)
                    }
                }
            }
        }
    }
}

struct SupplierInvoiceFormView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    var prefill: Attachment?
    var onSaved: () -> Void = {}

    @State private var choices: FormChoices?
    @State private var supplier: NamedItem?
    @State private var newSupplierName = ""
    @State private var invoiceNumber = ""
    @State private var ocr = ""
    @State private var invoiceDate = Date()
    @State private var dueDate = Calendar.current.date(byAdding: .day, value: 30, to: Date()) ?? Date()
    @State private var total = ""
    @State private var vat = ""
    @State private var account: Account?
    @State private var attachments: [Attachment] = []
    @State private var error: String?
    @State private var busy = false

    private var hasSupplier: Bool { supplier != nil || !newSupplierName.trimmingCharacters(in: .whitespaces).isEmpty }

    var body: some View {
        NavigationStack {
            Form {
                Section("Leverantör") {
                    Picker("Leverantör", selection: $supplier) {
                        Text("Ny leverantör…").tag(nil as NamedItem?)
                        ForEach(choices?.suppliers ?? []) { supplier in
                            Text(supplier.name).tag(Optional(supplier))
                        }
                    }
                    if supplier == nil {
                        TextField("Namn på ny leverantör", text: $newSupplierName)
                    }
                }
                Section("Faktura") {
                    TextField("Fakturanummer", text: $invoiceNumber)
                    TextField("OCR (valfritt)", text: $ocr).keyboardType(.numberPad)
                    DatePicker("Fakturadatum", selection: $invoiceDate, displayedComponents: .date)
                    DatePicker("Förfallodatum", selection: $dueDate, in: invoiceDate..., displayedComponents: .date)
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
                    Text("Hela beloppet exklusive moms bokförs på kostnadskontot. Ska kostnaden delas på flera konton, registrera fakturan på datorn.")
                }
                .disabled(busy || !hasSupplier || Amount.parse(total) == nil || account == nil)
            }
            .navigationTitle("Ny leverantörsfaktura")
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
        if supplier == nil && newSupplierName.isEmpty, let vendor = suggestion.vendor {
            if let match = choices?.supplier(matching: vendor) {
                supplier = match
            } else {
                newSupplierName = vendor
            }
        }
        if invoiceNumber.isEmpty, let value = suggestion.invoiceNumber { invoiceNumber = value }
        if ocr.isEmpty, let value = suggestion.ocr { ocr = value }
        if total.isEmpty, let value = suggestion.total { total = Amount.apiString(value) }
        if vat.isEmpty, let value = suggestion.vat { vat = Amount.apiString(value) }
        if let parsed = ISODate.parse(suggestion.date) { invoiceDate = parsed }
        if let parsed = ISODate.parse(suggestion.dueDate), parsed >= invoiceDate { dueDate = parsed }
    }

    private func save(register: Bool) async {
        guard let api = session.api, let totalValue = Amount.parse(total) else { return }
        busy = true
        defer { busy = false }
        error = nil
        do {
            let _: SupplierInvoice = try await api.post("leverantorsfakturor/", json: jsonObject([
                "supplier": supplier?.id,
                "new_supplier_name": supplier == nil ? newSupplierName.trimmingCharacters(in: .whitespaces) : nil,
                "invoice_number": invoiceNumber,
                "ocr_code": ocr,
                "invoice_date": ISODate.string(invoiceDate),
                "due_date": ISODate.string(dueDate),
                "total_amount": Amount.apiString(totalValue),
                "vat_amount": Amount.apiString(Amount.parse(vat) ?? 0),
                "expense_account": account?.id,
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
