import SwiftUI

struct AttachmentsView: View {
    @Environment(Session.self) private var session
    @State private var attachments: [Attachment] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var uploader = Uploader()
    @State private var isScanning = false
    @State private var isPicking = false

    var body: some View {
        NavigationStack {
            List {
                if let message = uploader.error {
                    Section { Text(message).foregroundStyle(.red) }
                }
                ForEach(attachments) { attachment in
                    NavigationLink(value: attachment) {
                        AttachmentRow(attachment: attachment)
                    }
                }
            }
            .navigationDestination(for: Attachment.self) { attachment in
                AttachmentDetailView(attachment: attachment)
            }
            .navigationTitle("Kvitton")
            .toolbar {
                if !(session.company?.readOnly ?? false) {
                    ToolbarItem(placement: .topBarTrailing) {
                        CaptureMenu(isScanning: $isScanning, isPicking: $isPicking)
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                if uploader.isUploading {
                    HStack {
                        ProgressView()
                        Text("Laddar upp och läser kvittot…")
                    }
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.bar)
                }
            }
            .overlay {
                if loaded && attachments.isEmpty && error == nil {
                    ContentUnavailableView(
                        "Inga kvitton väntar",
                        systemImage: "doc.viewfinder",
                        description: Text("Skanna ett kvitto med knappen uppe till höger. Bilagor som kopplats till ett utlägg eller en faktura försvinner härifrån.")
                    )
                } else if let error, attachments.isEmpty {
                    ContentUnavailableView("Kunde inte hämta bilagor", systemImage: "wifi.exclamationmark", description: Text(error))
                }
            }
            .modifier(CaptureModifier(isScanning: $isScanning, isPicking: $isPicking, uploader: uploader) { attachment in
                attachments.insert(attachment, at: 0)
            })
            .reloads(on: session.changeCounter) { await load() }
        }
    }

    private func load() async {
        guard let api = session.api else { return }
        do {
            attachments = try await api.get("bilagor/")
            error = nil
        } catch {
            self.error = session.describe(error)
        }
        loaded = true
    }
}

struct AttachmentDetailView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    let attachment: Attachment
    var allowRegister = true
    @State private var fileURL: URL?
    @State private var error: String?
    @State private var registering = false

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if let fileURL {
                    QuickLookPreview(url: fileURL)
                } else if let error {
                    ContentUnavailableView("Kunde inte hämta filen", systemImage: "doc", description: Text(error))
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if allowRegister && !(session.company?.readOnly ?? false) {
                VStack(spacing: 8) {
                    if let suggestion = attachment.suggestion, let total = suggestion.total {
                        Text("Läst från bilden: \([suggestion.vendor, Amount(total).formatted, suggestion.date].compactMap { $0 }.joined(separator: " · "))")
                            .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    Button("Registrera som utlägg", systemImage: "creditcard") { registering = true }
                        .buttonStyle(.bordered)
                        .controlSize(.large)
                }
                .padding()
                .frame(maxWidth: .infinity)
                .background(.bar)
            }
        }
        .navigationTitle(attachment.fileName)
        .navigationBarTitleDisplayMode(.inline)
        .task { await download() }
        .sheet(isPresented: $registering) {
            ExpenseFormView(prefill: attachment) { dismiss() }
        }
    }

    private func download() async {
        guard let api = session.api else { return }
        do {
            let data = try await api.data(attachment.filePath)
            let directory = FileManager.default.temporaryDirectory.appending(path: "bilagor", directoryHint: .isDirectory)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let url = directory.appending(path: "\(attachment.id)-\(attachment.fileName)")
            try data.write(to: url, options: .atomic)
            fileURL = url
        } catch {
            self.error = session.describe(error)
        }
    }
}

/// Pick one of the unlinked attachments (for a form).
struct AttachmentPickerSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss
    let exclude: Set<Int>
    let onPick: (Attachment) -> Void
    @State private var attachments: [Attachment] = []
    @State private var error: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List(attachments.filter { !exclude.contains($0.id) }) { attachment in
                Button {
                    onPick(attachment)
                } label: {
                    AttachmentRow(attachment: attachment)
                }
                .tint(.primary)
            }
            .overlay {
                if loaded && attachments.isEmpty {
                    ContentUnavailableView("Inga uppladdade bilagor", systemImage: "paperclip", description: Text(error ?? "Skanna ett kvitto i stället."))
                }
            }
            .navigationTitle("Välj bilaga")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Avbryt") { dismiss() } }
            }
            .task {
                guard let api = session.api else { return }
                do {
                    attachments = try await api.get("bilagor/")
                } catch {
                    self.error = session.describe(error)
                }
                loaded = true
            }
        }
    }
}

/// The "Kvitto" section of a document form: selected attachments plus ways to add one.
struct AttachmentsSection: View {
    @Binding var attachments: [Attachment]
    var onSuggestion: (Suggestion) -> Void = { _ in }
    @State private var uploader = Uploader()
    @State private var isScanning = false
    @State private var isPicking = false
    @State private var choosing = false

    var body: some View {
        Section {
            ForEach(attachments) { attachment in
                HStack(spacing: 12) {
                    AttachmentThumbnail(attachment: attachment, size: 44)
                    Text(attachment.fileName).lineLimit(1)
                }
            }
            .onDelete { attachments.remove(atOffsets: $0) }
            if uploader.isUploading {
                HStack {
                    ProgressView()
                    Text("Laddar upp och läser…").foregroundStyle(.secondary)
                }
            }
            ErrorText(message: uploader.error)
            if DocumentScannerView.isSupported {
                Button("Skanna kvitto", systemImage: "doc.viewfinder") { isScanning = true }
            }
            Button("Välj foto", systemImage: "photo") { isPicking = true }
            Button("Välj uppladdad bilaga", systemImage: "paperclip") { choosing = true }
        } header: {
            Text("Kvitto")
        } footer: {
            Text("Belopp, moms och datum som läses ur bilden fylls i automatiskt – kontrollera dem.")
        }
        .modifier(CaptureModifier(isScanning: $isScanning, isPicking: $isPicking, uploader: uploader) { attachment in
            add(attachment)
        })
        .sheet(isPresented: $choosing) {
            AttachmentPickerSheet(exclude: Set(attachments.map(\.id))) { attachment in
                add(attachment)
                choosing = false
            }
        }
    }

    private func add(_ attachment: Attachment) {
        attachments.append(attachment)
        if let suggestion = attachment.suggestion {
            onSuggestion(suggestion)
        }
    }
}
