import SwiftUI
import VisionKit

struct LoginView: View {
    @Environment(Session.self) private var session
    @State private var server = ""
    @State private var email = ""
    @State private var password = ""
    @State private var error: String?
    @State private var busy = false
    @State private var showScanner = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("https://saldovibe.example.se", text: $server)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Server")
                } footer: {
                    Text("Samma adress som du använder i webbläsaren.")
                }
                Section("Konto") {
                    TextField("E-postadress", text: $email)
                        .keyboardType(.emailAddress)
                        .textContentType(.username)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    SecureField("Lösenord", text: $password)
                        .textContentType(.password)
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
                Section {
                    Button {
                        Task { await login() }
                    } label: {
                        HStack {
                            Text("Logga in")
                            if busy { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(busy || server.isEmpty || email.isEmpty || password.isEmpty)
                    Button {
                        showScanner = true
                    } label: {
                        Label("Skanna QR-kod från webben", systemImage: "qrcode.viewfinder")
                    }
                    .disabled(busy)
                } footer: {
                    Text("QR-koden finns under Mobilappen längst ner i menyn på datorn.")
                }
            }
            .navigationTitle("SaldoVibe")
            .sheet(isPresented: $showScanner) {
                QRScannerSheet { url in
                    showScanner = false
                    Task {
                        busy = true
                        error = await session.handle(url: url)
                        busy = false
                    }
                }
            }
        }
    }

    private func login() async {
        busy = true
        defer { busy = false }
        error = nil
        do {
            try await session.login(server: server, email: email, password: password)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct QRScannerSheet: View {
    let onFound: (URL) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    QRScannerView(onFound: onFound).ignoresSafeArea()
                } else {
                    ContentUnavailableView(
                        "Kameran kan inte användas",
                        systemImage: "camera",
                        description: Text("Skanning kräver en iPhone med kamera och att appen får använda den (Inställningar → SaldoVibe → Kamera).")
                    )
                }
            }
            .navigationTitle("Skanna QR-kod")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Avbryt") { dismiss() }
                }
            }
        }
    }
}

struct QRScannerView: UIViewControllerRepresentable {
    let onFound: (URL) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        if !scanner.isScanning {
            try? scanner.startScanning()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(onFound: onFound) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onFound: (URL) -> Void
        private var done = false

        init(onFound: @escaping (URL) -> Void) { self.onFound = onFound }

        func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !done else { return }
            for item in addedItems {
                if case .barcode(let barcode) = item, let text = barcode.payloadStringValue,
                   let url = URL(string: text), url.scheme == "saldovibe" {
                    done = true
                    dataScanner.stopScanning()
                    onFound(url)
                    return
                }
            }
        }
    }
}
