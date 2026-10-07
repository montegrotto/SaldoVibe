import SwiftUI

@main
struct SaldoVibeApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @State private var session = Session()
    @State private var linkError: String?

    var body: some Scene {
        WindowGroup {
            RootView()
                .task { await session.restore() }
                .onChange(of: scenePhase) { _, phase in
                    // The share extension may have uploaded attachments while the app was away.
                    if phase == .active, !session.isRestoring { session.didChange() }
                }
                .onOpenURL { url in
                    Task { linkError = await session.handle(url: url) }
                }
                .alert("Inloggningen misslyckades", isPresented: Binding(get: { linkError != nil }, set: { if !$0 { linkError = nil } })) {
                    Button("OK", role: .cancel) {}
                } message: {
                    Text(linkError ?? "")
                }
                .environment(session) // outermost so the alert gets it too
        }
    }
}
