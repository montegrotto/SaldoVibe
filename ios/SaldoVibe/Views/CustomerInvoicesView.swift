import SwiftUI

struct CustomerInvoicesList: View {
    @Environment(Session.self) private var session
    @State private var invoices: [CustomerInvoice] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var showAll = false

    var body: some View {
        List {
            ForEach(invoices) { invoice in
                NavigationLink(value: invoice.id) {
                    PayableRow(item: invoice)
                }
            }
        }
        .navigationDestination(for: Int.self) { id in
            CustomerInvoiceDetailView(id: id)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Toggle("Visa alla", isOn: $showAll).toggleStyle(.button).controlSize(.small)
            }
        }
        .overlay {
            if loaded && invoices.isEmpty && error == nil {
                ContentUnavailableView(
                    showAll ? "Inga kundfakturor" : "Inga obetalda kundfakturor",
                    systemImage: "doc.text",
                    description: Text("Kundfakturor skapas, skickas och betalas in på datorn.")
                )
            } else if let error, invoices.isEmpty {
                ContentUnavailableView("Kunde inte hämta fakturor", systemImage: "wifi.exclamationmark", description: Text(error))
            }
        }
        .onChange(of: showAll) { Task { await load() } }
        .reloads(on: session.changeCounter) { await load() }
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            invoices = try await api.get("kundfakturor/", query: showAll ? ["visa": "alla"] : [:])
            error = nil
        } catch {
            self.error = session.describe(error)
        }
        loaded = true
    }
}

struct CustomerInvoiceDetailView: View {
    let id: Int

    var body: some View {
        DocumentDetailView<CustomerInvoice, _>(path: "kundfakturor/\(id)/", title: "Kundfaktura", actions: false) { invoice in
            Section("Faktura") {
                LabeledContent("Kund", value: invoice.customerName)
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
                if invoice.isCreditInvoice {
                    Label("Kreditfaktura", systemImage: "arrow.uturn.backward").foregroundStyle(.secondary)
                }
            }
            if let lines = invoice.lines, !lines.isEmpty {
                Section("Rader") {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(line.description)
                            Text("\(line.quantity) \(line.unit) × \(line.unitPrice.formatted) · moms \(line.vatRate) %")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .badge(Text(line.totalExVat.formatted))
                    }
                }
            }
        }
    }
}
