import SwiftUI

struct MoreView: View {
    @Environment(Session.self) private var session
    @State private var confirmLogout = false

    var body: some View {
        NavigationStack {
            List {
                Section("Företag") {
                    ForEach(session.companies) { company in
                        Button {
                            session.select(company: company)
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(company.name)
                                    if company.readOnly {
                                        Text("Endast läsa").font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                                if company.id == session.company?.id {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                        .tint(.primary)
                    }
                }
                Section("Inloggad som") {
                    LabeledContent("Användare", value: session.user?.name ?? "")
                    LabeledContent("E-post", value: session.user?.email ?? "")
                    LabeledContent("Server", value: session.serverDescription)
                    Button("Logga ut", role: .destructive) { confirmLogout = true }
                }
                Section {
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
                } footer: {
                    Text("Resultat- och balansräkning finns under Översikt. Verifikationer, övriga rapporter, moms, lön och inställningar görs på datorn. Hjälp: Hjälp → Mobilappen på webben.")
                }
            }
            .navigationTitle("Mer")
            .refreshable { await session.refreshCompanies() }
            .confirmationDialog("Logga ut från den här telefonen?", isPresented: $confirmLogout, titleVisibility: .visible) {
                Button("Logga ut", role: .destructive) { Task { await session.logout() } }
            } message: {
                Text("Inloggningen tas bort på servern. Logga in igen med lösenord eller QR-kod.")
            }
        }
    }
}
