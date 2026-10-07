import SwiftUI

@main
struct SaldoVibeApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @State private var session = Session()
    @State private var linkError: String?
    @State private var pendingExpense: Attachment?

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .task {
                    await session.restore()
                    await checkShared()
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active, !session.isRestoring {
                        Task { await checkShared() }
                    }
                }
                .sheet(item: $pendingExpense) { attachment in
                    ExpenseFormView(prefill: attachment)
                }
                .onOpenURL { url in
                    Task { linkError = await session.handle(url: url) }
                }
                .alert("Inloggningen misslyckades", isPresented: Binding(get: { linkError != nil }, set: { if !$0 { linkError = nil } })) {
                    Button("OK", role: .cancel) {}
                } message: {
                    Text(linkError ?? "")
                }
        }
    }

    /// The share extension may have uploaded attachments while the app was away: reload the
    /// lists, and open the expense form if it was asked to.
    private func checkShared() async {
        session.didChange()
        pendingExpense = await session.takePendingExpense()
    }
}
