import SwiftUI

@main
struct SaldoVibeApp: App {
    @State private var session = Session()
    @State private var linkError: String?

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .task { await session.restore() }
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
}
