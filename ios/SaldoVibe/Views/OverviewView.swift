import SwiftUI

struct OverviewView: View {
    @Environment(Session.self) private var session
    /// 0 = supplier invoices, 1 = customer invoices.
    let openInvoices: (Int) -> Void
    @State private var overview: Overview?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if let overview {
                    Section("Kassa och bank") {
                        LabeledContent("Saldo") {
                            Text(overview.cashBalance.formatted).font(.title3.weight(.semibold)).monospacedDigit()
                        }
                        ForEach(overview.bankAccounts) { account in
                            LabeledContent("\(account.number) \(account.name)") {
                                Text(account.balance.formatted).monospacedDigit()
                            }
                        }
                    }
                    Section(yearTitle(overview)) {
                        LabeledContent("Intäkter", value: overview.revenue.formatted)
                        LabeledContent("Kostnader", value: overview.costs.formatted)
                        LabeledContent("Resultat") {
                            Text(overview.netResult.formatted)
                                .fontWeight(.semibold)
                                .foregroundStyle(overview.netResult.value < 0 ? .red : .green)
                        }
                    }
                    Section("Obetalda fakturor") {
                        Button { openInvoices(0) } label: {
                            LabeledContent("Att betala (\(overview.payablesOpen.count))", value: overview.payablesOpen.total.formatted)
                        }
                        Button { openInvoices(1) } label: {
                            LabeledContent("Att få in (\(overview.receivablesOpen.count))", value: overview.receivablesOpen.total.formatted)
                        }
                    }
                    .tint(.primary)
                    Section("Påminnelser") {
                        if overview.alerts.isEmpty {
                            Label("Inget att hantera just nu", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                        }
                        ForEach(overview.alerts) { alert in
                            if let target = alert.target {
                                Button {
                                    openInvoices(target == "customer_invoices" ? 1 : 0)
                                } label: {
                                    alertRow(alert)
                                }
                                .tint(.primary)
                            } else {
                                alertRow(alert)
                            }
                        }
                    }
                } else if let error {
                    ContentUnavailableView("Kunde inte hämta översikten", systemImage: "wifi.exclamationmark", description: Text(error))
                } else {
                    ProgressView()
                }
            }
            .navigationTitle(session.company?.name ?? "Översikt")
            .reloads(on: session.changeCounter) { await load() }
        }
    }

    private func alertRow(_ alert: OverviewAlert) -> some View {
        HStack {
            Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
            Text(alert.text)
            Spacer()
            Text("\(alert.count)").foregroundStyle(.secondary).monospacedDigit()
        }
    }

    private func yearTitle(_ overview: Overview) -> String {
        guard let year = overview.accountingYear else { return "Inget räkenskapsår för idag" }
        return "Räkenskapsåret \(ISODate.display(year.startDate)) – \(ISODate.display(year.endDate))"
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            overview = try await api.get("oversikt/")
            error = nil
        } catch {
            self.error = session.describe(error)
        }
    }
}
