import SwiftUI
import UniformTypeIdentifiers

/// The share-sheet target ("Dela → SaldoVibe"): uploads shared images and PDFs as unlinked
/// attachments of the chosen company, signed in with the app's session from the shared keychain.
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
    enum Status {
        case loading, choosing, uploading(String), failed(String)
    }

    let context: NSExtensionContext?
    @State private var status = Status.loading
    @State private var api: APIClient?
    @State private var companies: [Company] = []
    @State private var companyId: Int?
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
                            .foregroundStyle(.secondary).multilineTextAlignment(.center)
                        if companies.count > 1 {
                            Picker("Företag", selection: $companyId) {
                                Text("Välj företag…").tag(Int?.none)
                                ForEach(companies) { Text($0.name).tag(Optional($0.id)) }
                            }
                            .pickerStyle(.menu)
                        }
                        Button("Spara som bilaga") { Task { await upload() } }
                            .buttonStyle(.borderedProminent)
                            .padding(.top)
                            .disabled(companyId == nil)
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
        let api = APIClient(baseURL: stored.server, token: stored.token)
        // The app's active company is preselected; the server list (offline: just that one)
        // lets the user pick another. Read-only companies cannot take uploads.
        let me: MeResponse? = try? await api.get("me/")
        companies = (me?.companies ?? []).filter { !$0.readOnly }
        companyId = companies.first { $0.id == stored.companyId }?.id ?? (companies.count == 1 ? companies[0].id : nil)
        if me == nil {
            guard let storedId = stored.companyId else { return fail("Kunde inte nå servern.") }
            companyId = storedId
        } else if companies.isEmpty {
            return fail("Du kan inte ladda upp till något företag.")
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

    private func upload() async {
        guard let api, let companyId else { return }
        api.companyId = companyId
        do {
            for (index, file) in files.enumerated() {
                status = .uploading(files.count == 1 ? "Laddar upp och läser kvittot…" : "Laddar upp \(index + 1) av \(files.count)…")
                _ = try await api.upload("bilagor/", file: file)
            }
        } catch {
            return fail(error.localizedDescription)
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
