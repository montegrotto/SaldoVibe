import PhotosUI
import QuickLook
import SwiftUI
import UIKit
import VisionKit

/// Uploads one file at a time and remembers the outcome for the UI.
@MainActor
@Observable
final class Uploader {
    var isUploading = false
    var error: String?

    func upload(_ file: UploadFile, api: APIClient) async -> Attachment? {
        isUploading = true
        error = nil
        defer { isUploading = false }
        do {
            return try await api.upload("bilagor/", file: file)
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }
}

/// Turns scanned pages / a photo into what the server accepts: one page → JPEG, several → PDF.
enum ReceiptEncoder {
    static let maxPixels: CGFloat = 2200
    static let pdfPageWidth: CGFloat = 595 // A4 in points; the embedded image keeps its full resolution.

    static func encode(_ images: [UIImage]) -> UploadFile? {
        let jpegs = images.compactMap { normalized($0).jpegData(compressionQuality: 0.8) }
        guard !jpegs.isEmpty else { return nil }
        let stamp = Date().formatted(Date.FormatStyle(date: .numeric, time: .standard, locale: Locale(identifier: "sv_SE")))
            .replacingOccurrences(of: " ", with: "-").replacingOccurrences(of: ":", with: "")
        if jpegs.count == 1 {
            return UploadFile(name: "kvitto-\(stamp).jpg", mimeType: "image/jpeg", data: jpegs[0])
        }
        let data = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: pdfPageWidth, height: 842)).pdfData { context in
            for jpeg in jpegs {
                // A JPEG-backed UIImage is embedded as JPEG, keeping the PDF small.
                guard let image = UIImage(data: jpeg), image.size.width > 0 else { continue }
                let bounds = CGRect(x: 0, y: 0, width: pdfPageWidth, height: pdfPageWidth * image.size.height / image.size.width)
                context.beginPage(withBounds: bounds, pageInfo: [:])
                image.draw(in: bounds)
            }
        }
        return UploadFile(name: "kvitto-\(stamp).pdf", mimeType: "application/pdf", data: data)
    }

    /// Re-rendered upright at most `maxPixels` on the long side, scale 1.
    static func normalized(_ image: UIImage) -> UIImage {
        let pixelSize = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
        let factor = min(1, maxPixels / max(pixelSize.width, pixelSize.height, 1))
        let target = CGSize(width: (pixelSize.width * factor).rounded(), height: (pixelSize.height * factor).rounded())
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }
}

struct DocumentScannerView: UIViewControllerRepresentable {
    let onScan: ([UIImage]) -> Void
    let onCancel: () -> Void

    static var isSupported: Bool { VNDocumentCameraViewController.isSupported }

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let controller = VNDocumentCameraViewController()
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ uiViewController: VNDocumentCameraViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onScan: onScan, onCancel: onCancel) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        let onScan: ([UIImage]) -> Void
        let onCancel: () -> Void

        init(onScan: @escaping ([UIImage]) -> Void, onCancel: @escaping () -> Void) {
            self.onScan = onScan
            self.onCancel = onCancel
        }

        func documentCameraViewController(_ controller: VNDocumentCameraViewController, didFinishWith scan: VNDocumentCameraScan) {
            onScan((0..<scan.pageCount).map { scan.imageOfPage(at: $0) })
        }

        func documentCameraViewControllerDidCancel(_ controller: VNDocumentCameraViewController) {
            onCancel()
        }

        func documentCameraViewController(_ controller: VNDocumentCameraViewController, didFailWithError error: Error) {
            onCancel()
        }
    }
}

/// Camera + photo library capture with upload. Attach to a view and drive it with the two flags.
struct CaptureModifier: ViewModifier {
    @Environment(Session.self) private var session
    @Binding var isScanning: Bool
    @Binding var isPicking: Bool
    let uploader: Uploader
    let onUploaded: (Attachment) -> Void
    @State private var photoItem: PhotosPickerItem?

    func body(content: Content) -> some View {
        content
            .fullScreenCover(isPresented: $isScanning) {
                DocumentScannerView { images in
                    isScanning = false
                    Task { await upload(images) }
                } onCancel: {
                    isScanning = false
                }
                .ignoresSafeArea()
            }
            .photosPicker(isPresented: $isPicking, selection: $photoItem, matching: .images)
            .onChange(of: photoItem) { _, item in
                guard let item else { return }
                photoItem = nil
                Task {
                    if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                        await upload([image])
                    } else {
                        uploader.error = "Bilden kunde inte läsas."
                    }
                }
            }
    }

    private func upload(_ images: [UIImage]) async {
        guard let api = session.api, let file = ReceiptEncoder.encode(images) else { return }
        if let attachment = await uploader.upload(file, api: api) {
            onUploaded(attachment)
        }
    }
}

struct CaptureMenu: View {
    @Binding var isScanning: Bool
    @Binding var isPicking: Bool

    var body: some View {
        Menu {
            if DocumentScannerView.isSupported {
                Button("Skanna kvitto", systemImage: "doc.viewfinder") { isScanning = true }
            }
            Button("Välj foto", systemImage: "photo") { isPicking = true }
        } label: {
            Label("Lägg till", systemImage: "plus.circle.fill")
        }
    }
}

struct QuickLookPreview: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: QLPreviewController, context: Context) {
        if context.coordinator.url != url {
            context.coordinator.url = url
            controller.reloadData()
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(url: url) }

    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        var url: URL

        init(url: URL) { self.url = url }

        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }

        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem {
            url as NSURL
        }
    }
}
