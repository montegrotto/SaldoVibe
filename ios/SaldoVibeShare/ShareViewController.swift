import SwiftUI
import UniformTypeIdentifiers

/// The share-sheet target ("Dela → SaldoVibe"): uploads shared images and PDFs as attachments,
/// signed in with the app's session from the shared keychain, optionally handing them over to
/// the app as a new expense.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let host = UIHostingController(rootView: ShareView(context: extensionContext) { [weak self] url in
            self?.openContainingApp(url)
        })
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
    }

    // ponytail: no supported API lets a share extension open its app; the responder-chain walk
    // to the hidden UIApplication is the common workaround. If iOS closes it the hand-over still
    // works, the user just opens the app themselves (the pending list is in the keychain).
    private func openContainingApp(_ url: URL) {
        let selector = NSSelectorFromString("openURL:")
        var responder: UIResponder? = self
        while let current = responder {
            if current.responds(to: selector) {
                current.perform(selector, with: url)
                return
            }
            responder = current.next
        }
    }
}

struct ShareView: View {
    enum Status {
        case loading, choosing, uploading(String), failed(String)
    }

    let context: NSExtensionContext?
    let openApp: (URL) -> Void
    @State private var status = Status.loading
    @State private var api: APIClient?
    @State private var files: [UploadFile] = []

    var body: some View {
        NavigationStack {
            Group {
                switch status {
                case .loading:
                    ProgressView()
                case .choosing:
                    VStack(spacing: 12) {
                        Text(files.count == 1 ? "Filen laddas upp till Kvitton i SaldoVibe." : "\(files.count) filer laddas upp till Kvitton i SaldoVibe.")
                            .foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.bottom)
                        Button("Spara som bilaga") { Task { await upload(asExpense: false) } }
                            .buttonStyle(.borderedProminent)
                        Button("Registrera som utlägg") { Task { await upload(asExpense: true) } }
                            .buttonStyle(.bordered)
                    }
                    .controlSize(.large)
                case .uploading(let text):
                    VStack(spacing: 16) {
                        ProgressView().controlSize(.large)
                        Text(text)
                    }
                case .failed(let text):
                    VStack(spacing: 16) {
                        Image(systemName: "exclamationmark.triangle").font(.largeTitle).foregroundStyle(.orange)
                        Text(text).multilineTextAlignment(.center)
                    }
                }
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle("Till SaldoVibe")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(failed ? "Stäng" : "Avbryt") { context?.cancelRequest(withError: CocoaError(.userCancelled)) }
                }
            }
            .task { await prepare() }
        }
    }

    private var failed: Bool {
        if case .failed = status { return true }
        return false
    }

    private func prepare() async {
        guard let stored = Keychain.readSession() else {
            return fail("Logga in i SaldoVibe-appen först.")
        }
        let api = APIClient(baseURL: stored.server, token: stored.token, companyId: stored.companyId)
        if api.companyId == nil {
            // The session was saved by an app version before 2.4 (or the app has not been opened
            // since the update): the company is in the keychain only after the app has run once.
            let me: MeResponse? = try? await api.get("me/")
            guard let companies = me?.companies, companies.count == 1 else {
                return fail("Välj företag i SaldoVibe-appen först.")
            }
            api.companyId = companies[0].id
        }
        self.api = api
        let providers = (context?.inputItems as? [NSExtensionItem])?.flatMap { $0.attachments ?? [] } ?? []
        do {
            for provider in providers {
                if let file = try await load(provider) { files.append(file) }
            }
        } catch {
            return fail(error.localizedDescription)
        }
        guard !files.isEmpty else {
            return fail("Bara bilder och PDF-filer kan delas till SaldoVibe.")
        }
        status = .choosing
    }

    private func upload(asExpense: Bool) async {
        guard let api else { return }
        var ids: [Int] = []
        do {
            for (index, file) in files.enumerated() {
                status = .uploading(files.count == 1 ? "Laddar upp och läser kvittot…" : "Laddar upp \(index + 1) av \(files.count)…")
                ids.append(try await api.upload("bilagor/", file: file).id)
            }
        } catch {
            return fail(error.localizedDescription)
        }
        if asExpense {
            Keychain.writePendingExpense(ids)
            openApp(URL(string: "saldovibe://utlagg")!)
        }
        context?.completeRequest(returningItems: nil)
    }

    private func fail(_ message: String) {
        status = .failed(message)
    }

    /// A PDF is sent as is; an image goes through the same resize/JPEG step as a scanned receipt.
    private func load(_ provider: NSItemProvider) async throws -> UploadFile? {
        if provider.hasItemConformingToTypeIdentifier(UTType.pdf.identifier) {
            let data = try await provider.data(for: .pdf)
            var name = provider.suggestedName ?? "dokument"
            if !name.lowercased().hasSuffix(".pdf") { name += ".pdf" }
            return UploadFile(name: name, mimeType: "application/pdf", data: data)
        }
        if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
            guard let image = UIImage(data: try await provider.data(for: .image)) else { return nil }
            return ReceiptEncoder.encode([image])
        }
        return nil
    }
}

extension NSItemProvider {
    func data(for type: UTType) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            _ = loadDataRepresentation(for: type) { data, error in
                if let data {
                    continuation.resume(returning: data)
                } else {
                    continuation.resume(throwing: error ?? CocoaError(.fileReadUnknown))
                }
            }
        }
    }
}
