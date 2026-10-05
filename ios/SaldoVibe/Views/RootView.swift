import SwiftUI

enum AppTab: String {
    case overview, receipts, expenses, invoices, more
}

struct RootView: View {
    @Environment(Session.self) private var session

    var body: some View {
        if session.isRestoring {
            ProgressView("Startar…")
        } else if !session.isLoggedIn {
            LoginView()
        } else if session.company == nil {
            CompanyPickerView()
        } else {
            MainTabView()
        }
    }
}

struct MainTabView: View {
    @State private var selection = MainTabView.launchArgument("-tab").flatMap(AppTab.init(rawValue:)) ?? .overview
    @State private var invoiceKind = MainTabView.launchArgument("-invoiceKind").flatMap(Int.init) ?? 0

    var body: some View {
        TabView(selection: $selection) {
            Tab("Översikt", systemImage: "chart.bar.xaxis", value: .overview) {
                OverviewView { kind in
                    invoiceKind = kind
                    selection = .invoices
                }
            }
            Tab("Kvitton", systemImage: "doc.viewfinder", value: .receipts) {
                AttachmentsView()
            }
            Tab("Utlägg", systemImage: "creditcard", value: .expenses) {
                ExpensesView()
            }
            Tab("Fakturor", systemImage: "doc.text", value: .invoices) {
                InvoicesView(kind: $invoiceKind)
            }
            Tab("Mer", systemImage: "ellipsis.circle", value: .more) {
                MoreView()
            }
        }
    }

    /// `-tab receipts`, `-invoiceKind 1`: used by the simulator screenshot run (DEBUG only).
    static func launchArgument(_ name: String) -> String? {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: name), index + 1 < arguments.count {
            return arguments[index + 1]
        }
        #endif
        return nil
    }
}

struct CompanyPickerView: View {
    @Environment(Session.self) private var session

    var body: some View {
        NavigationStack {
            List(session.companies) { company in
                Button {
                    session.select(company: company)
                } label: {
                    HStack {
                        Text(company.name)
                        Spacer()
                        if company.readOnly {
                            Text("Endast läsa").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .tint(.primary)
            }
            .overlay {
                if session.companies.isEmpty {
                    ContentUnavailableView(
                        "Inget företag",
                        systemImage: "building.2",
                        description: Text("Du har inte tillgång till något företag ännu. Be en administratör lägga till dig, och dra nedåt för att uppdatera.")
                    )
                }
            }
            .navigationTitle("Välj företag")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Logga ut") { Task { await session.logout() } }
                }
            }
            .refreshable { await session.refreshCompanies() }
        }
    }
}

struct InvoicesView: View {
    @Binding var kind: Int

    var body: some View {
        NavigationStack {
            Group {
                if kind == 0 {
                    SupplierInvoicesList()
                } else {
                    CustomerInvoicesList()
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                Picker("Fakturatyp", selection: $kind) {
                    Text("Leverantörer").tag(0)
                    Text("Kunder").tag(1)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.vertical, 8)
                .background(.bar)
            }
            .navigationTitle("Fakturor")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}
