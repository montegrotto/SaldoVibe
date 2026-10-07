import UIKit

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
