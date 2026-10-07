import SwiftUI
import UniformTypeIdentifiers

/// The share-sheet target ("Dela → SaldoVibe"): uploads shared images and PDFs as unlinked
/// attachments, signed in with the app's session from the shared keychain.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let host = UIHostingController(rootView: ShareView(context: extensionContext))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
    }
}

struct ShareView: View {
    let context: NSExtensionContext?
    @State private var status = "Laddar upp…"
    @State private var failed = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                if failed {
                    Image(systemName: "exclamationmark.triangle").font(.largeTitle).foregroundStyle(.orange)
                } else {
                    ProgressView().controlSize(.large)
                }
                Text(status).multilineTextAlignment(.center)
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
            .task { await run() }
        }
    }

    private func run() async {
        guard let stored = Keychain.readSession() else {
            return fail("Logga in i SaldoVibe-appen först.")
        }
        guard let companyId = stored.companyId else {
            return fail("Välj företag i SaldoVibe-appen först.")
        }
        let api = APIClient(baseURL: stored.server, token: stored.token, companyId: companyId)
        let providers = (context?.inputItems as? [NSExtensionItem])?.flatMap { $0.attachments ?? [] } ?? []
        do {
            var files: [UploadFile] = []
            for provider in providers {
                if let file = try await load(provider) { files.append(file) }
            }
            guard !files.isEmpty else {
                return fail("Bara bilder och PDF-filer kan delas till SaldoVibe.")
            }
            for (index, file) in files.enumerated() {
                status = files.count == 1 ? "Laddar upp och läser kvittot…" : "Laddar upp \(index + 1) av \(files.count)…"
                _ = try await api.upload("bilagor/", file: file)
            }
            context?.completeRequest(returningItems: nil)
        } catch {
            fail(error.localizedDescription)
        }
    }

    private func fail(_ message: String) {
        status = message
        failed = true
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
