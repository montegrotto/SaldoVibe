import SwiftUI

struct SupplierInvoicesList: View {
    @Environment(Session.self) private var session
    @State private var invoices: [SupplierInvoice] = []
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
            SupplierInvoiceDetailView(id: id)
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Toggle("Visa alla", isOn: $showAll).toggleStyle(.button).controlSize(.small)
            }
        }
        .overlay {
            if loaded && invoices.isEmpty && error == nil {
                ContentUnavailableView(
                    showAll ? "Inga leverantörsfakturor" : "Inga obetalda leverantörsfakturor",
                    systemImage: "doc.text",
                    description: Text(showAll ? "Leverantörsfakturor registreras på datorn." : "Visa alla tar med betalda fakturor.")
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
        DocumentDetailView<SupplierInvoice, _>(path: "leverantorsfakturor/\(id)/", title: "Leverantörsfaktura") { invoice in
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
