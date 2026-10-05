import SwiftUI

/// Resultat- and balansräkning, same sections and sums as the web's Rapporter pages.
struct ReportView: View {
    enum Kind: String {
        case incomeStatement = "resultat", balanceSheet = "balans"

        var title: String { self == .incomeStatement ? "Resultaträkning" : "Balansräkning" }
        var path: String { self == .incomeStatement ? "resultatrakning/" : "balansrakning/" }
    }

    @Environment(Session.self) private var session
    let kind: Kind
    @State private var report: Report?
    @State private var error: String?
    /// nil = the server's default (the year containing today, the whole year).
    @State private var yearId: Int?
    @State private var fromMonth: String?
    @State private var toMonth: String?

    var body: some View {
        List {
            if let report {
                if report.years.isEmpty {
                    ContentUnavailableView("Inget räkenskapsår", systemImage: "calendar.badge.exclamationmark", description: Text("Lägg upp ett räkenskapsår på datorn först."))
                } else {
                    if let months = report.monthChoices, !months.isEmpty {
                        periodSection(months, report: report)
                    }
                    ForEach(report.sections.indices, id: \.self) { index in
                        section(report.sections[index])
                    }
                    Section {
                        ReportLine(label: report.result.label, amount: report.result.amount, emphasized: true)
                            .foregroundStyle(report.result.amount.value < 0 ? .red : .green)
                    } footer: {
                        if let note = report.result.note {
                            Text(note)
                        } else if kind == .balanceSheet, let year = report.selectedYear {
                            Text("Ställning per \(ISODate.display(year.endDate)).")
                        }
                    }
                }
            } else if let error {
                ContentUnavailableView("Kunde inte hämta rapporten", systemImage: "wifi.exclamationmark", description: Text(error))
            } else {
                ProgressView()
            }
        }
        .navigationTitle(kind.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let report, !report.years.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Räkenskapsår", selection: yearBinding(report)) {
                            ForEach(report.years) { year in
                                Text(year.name).tag(year.id)
                            }
                        }
                    } label: {
                        Text(report.selectedYear?.name ?? "År")
                    }
                }
            }
        }
        .task(id: "\(session.changeCounter)|\(yearId ?? 0)|\(fromMonth ?? "")|\(toMonth ?? "")") { await load() }
        .refreshable { await load() }
    }

    private func periodSection(_ months: [MonthChoice], report: Report) -> some View {
        Section {
            Picker("Från", selection: binding($fromMonth, default: report.fromMonth)) {
                ForEach(months) { Text($0.label).tag($0.value) }
            }
            Picker("Till", selection: binding($toMonth, default: report.toMonth)) {
                ForEach(months) { Text($0.label).tag($0.value) }
            }
        } footer: {
            Text("\(ISODate.display(report.periodStart)) – \(ISODate.display(report.periodEnd))")
        }
    }

    private func section(_ section: ReportSection) -> some View {
        Section {
            ForEach(section.rows) { row in
                ReportLine(number: row.account.number, label: row.account.name, amount: row.amount)
            }
            if let label = section.totalLabel, let total = section.total {
                ReportLine(label: label, amount: total, emphasized: true)
            }
        } header: {
            if let title = section.title {
                Text(title)
            }
        }
    }

    private func yearBinding(_ report: Report) -> Binding<Int> {
        Binding(get: { yearId ?? report.selectedYear?.id ?? 0 }) { id in
            yearId = id
            fromMonth = nil
            toMonth = nil
        }
    }

    private func binding(_ state: Binding<String?>, default fallback: String?) -> Binding<String> {
        Binding(get: { state.wrappedValue ?? fallback ?? "" }, set: { state.wrappedValue = $0 })
    }

    private func load() async {
        guard let api = session.api else { return }
        var query: [String: String] = [:]
        if let yearId { query["year"] = String(yearId) }
        if let fromMonth { query["from_month"] = fromMonth }
        if let toMonth { query["to_month"] = toMonth }
        do {
            report = try await api.get(kind.path, query: query)
            error = nil
        } catch {
            self.error = session.describe(error)
        }
    }
}

/// Account number, name and amount on one line; the name wraps, the amount never does.
private struct ReportLine: View {
    var number: String?
    let label: String
    let amount: Amount
    var emphasized = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            if let number {
                Text(number).foregroundStyle(.secondary).monospacedDigit()
            }
            Text(label).lineLimit(2)
            Spacer(minLength: 12)
            Text(amount.formatted).monospacedDigit().layoutPriority(1)
        }
        .fontWeight(emphasized ? .semibold : .regular)
    }
}
