import Foundation

/// A money amount as the API sends it: a decimal string ("1234.50"). Formats as SEK.
struct Amount: Codable, Hashable, Comparable {
    var value: Decimal

    init(_ value: Decimal) { self.value = value }

    init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        guard let value = Decimal(string: text, locale: Locale(identifier: "en_US_POSIX")) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Ogiltigt belopp: \(text)"))
        }
        self.value = value
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(Amount.apiString(value))
    }

    static let zero = Amount(0)

    var formatted: String {
        value.formatted(.currency(code: "SEK").locale(Locale(identifier: "sv_SE")))
    }

    /// "1234.50" – what the API's forms accept.
    var apiString: String { Amount.apiString(value) }

    static func apiString(_ value: Decimal) -> String {
        var rounded = Decimal()
        var source = value
        NSDecimalRound(&rounded, &source, 2, .plain)
        return rounded.formatted(.number.precision(.fractionLength(2)).grouping(.never).locale(Locale(identifier: "en_US_POSIX")))
    }

    /// Parses what a Swede types: "1 234,50", "1234.50", "1234".
    static func parse(_ text: String) -> Decimal? {
        let cleaned = text.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "\u{a0}", with: "")
            .replacingOccurrences(of: ",", with: ".")
        guard !cleaned.isEmpty else { return nil }
        return Decimal(string: cleaned, locale: Locale(identifier: "en_US_POSIX"))
    }

    static func < (lhs: Amount, rhs: Amount) -> Bool { lhs.value < rhs.value }
}

/// ReInvGrabber fields arrive as strings or null; be forgiving about numbers too.
struct LossyString: Codable, Hashable {
    var text: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            text = nil
        } else if let value = try? container.decode(String.self) {
            text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        } else if let value = try? container.decode(Double.self) {
            text = Amount.apiString(Decimal(value))
        } else {
            text = nil
        }
        if text?.isEmpty == true { text = nil }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(text)
    }
}

struct Suggestion: Codable, Hashable {
    var datum: LossyString?
    var leverantör: LossyString?
    var totalbelopp: LossyString?
    var momsbelopp: LossyString?
    var fakturanummer: LossyString?
    var förfallodatum: LossyString?
    var ocrReferens: LossyString?

    // The decoder's convertFromSnakeCase already turns "ocr_referens" into ocrReferens;
    // the Swedish keys have no underscores and pass through unchanged.

    var date: String? { datum?.text }
    var vendor: String? { leverantör?.text }
    var total: Decimal? { totalbelopp?.text.flatMap(Amount.parse) }
    var vat: Decimal? { momsbelopp?.text.flatMap(Amount.parse) }
    var invoiceNumber: String? { fakturanummer?.text }
    var dueDate: String? { förfallodatum?.text }
    var ocr: String? { ocrReferens?.text }
}

struct User: Codable, Hashable {
    let email: String
    let name: String
}

struct Company: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let vatRegistered: Bool
    let readOnly: Bool
}

struct MeResponse: Codable {
    let user: User
    let companies: [Company]
}

struct LoginResponse: Codable {
    let token: String
    let user: User
    let companies: [Company]
}

struct Account: Codable, Identifiable, Hashable {
    let id: Int
    let number: String
    let name: String
    let accountClass: String

    var label: String { "\(number) \(name)" }
    /// Kostnadsklasserna 4–8: det man normalt bokför kvitton och fakturor på.
    var isCostAccount: Bool { ["4", "5", "6", "7", "8"].contains(accountClass) }
}

struct NamedItem: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
}

struct FormChoices: Codable {
    let vatRegistered: Bool
    let accounts: [Account]
    let paymentAccountIds: [Int]
    let defaultPaymentAccountId: Int?
    let suppliers: [NamedItem]
    let employees: [NamedItem]
    /// Optional so the app still decodes an older server without körrapporter.
    let mileageRatePerMil: Amount?

    var paymentAccounts: [Account] { accounts.filter { paymentAccountIds.contains($0.id) } }
    var defaultPaymentAccount: Account? { accounts.first { $0.id == defaultPaymentAccountId } }

    func supplier(matching name: String?) -> NamedItem? {
        guard let name, !name.isEmpty else { return nil }
        return suppliers.first { $0.name.compare(name, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }
    }
}

struct OverviewAlert: Codable, Identifiable, Hashable {
    let key: String
    let text: String
    let count: Int
    let target: String?
    var id: String { key }
}

struct OpenTotal: Codable, Hashable {
    let count: Int
    let total: Amount
}

struct BankAccount: Codable, Identifiable, Hashable {
    let number: String
    let name: String
    let balance: Amount
    var id: String { number }
}

struct AccountingYear: Codable, Hashable {
    let startDate: String
    let endDate: String
}

struct Overview: Codable {
    let company: NamedItem
    let accountingYear: AccountingYear?
    let revenue: Amount
    let costs: Amount
    let netResult: Amount
    let cashBalance: Amount
    let bankAccounts: [BankAccount]
    let payablesOpen: OpenTotal
    let receivablesOpen: OpenTotal
    let alerts: [OverviewAlert]
}

struct Attachment: Codable, Identifiable, Hashable {
    let id: Int
    let fileName: String
    let uploadedAt: String
    let isPdf: Bool
    let suggestion: Suggestion?

    var filePath: String { "bilagor/\(id)/fil/" }
    var thumbnailPath: String { "bilagor/\(id)/miniatyr/" }
}

/// What every settled document shares (see bookkeeping/payables.py).
protocol Payable: Identifiable, Hashable, Sendable {
    var id: Int { get }
    var totalAmount: Amount { get }
    var paidAmount: Amount { get }
    var remainingAmount: Amount { get }
    var isPaid: Bool { get }
    var isBookkept: Bool { get }
    var status: String { get }
    var statusLabel: String { get }
    var paymentDate: String? { get }
    var attachments: [Attachment]? { get }
    var title: String { get }
    var subtitle: String { get }
    var dateLabel: String { get }
    var isOverdue: Bool { get }
}

struct Expense: Payable, Codable {
    let id: Int
    let totalAmount: Amount
    let paidAmount: Amount
    let remainingAmount: Amount
    let isPaid: Bool
    let isBookkept: Bool
    let status: String
    let statusLabel: String
    let paymentDate: String?
    let description: String
    let expenseDate: String
    let person: String
    let employeeId: Int?
    let vatAmount: Amount
    let amountExVat: Amount
    let expenseAccount: Account?
    let attachments: [Attachment]?
    let mileage: Mileage?

    var title: String { description }
    var subtitle: String { person }
    var dateLabel: String { expenseDate }
    var isOverdue: Bool { false }
}

/// The körrapport behind an expense, on the detail endpoint only.
struct Mileage: Codable, Hashable {
    let route: String
    let purpose: String
    let tripDate: String
    let distanceKm: String
    let ratePerMil: Amount
}

struct CostLine: Codable, Hashable {
    let account: Account?
    let amount: Amount
}

struct SupplierInvoice: Payable, Codable {
    let id: Int
    let totalAmount: Amount
    let paidAmount: Amount
    let remainingAmount: Amount
    let isPaid: Bool
    let isBookkept: Bool
    let status: String
    let statusLabel: String
    let paymentDate: String?
    let supplierName: String
    let supplierId: Int?
    let invoiceNumber: String
    let ocrCode: String
    let invoiceDate: String
    let dueDate: String
    let isOverdue: Bool
    let vatAmount: Amount
    let amountExVat: Amount
    let costLines: [CostLine]?
    let attachments: [Attachment]?

    var title: String { supplierName }
    var subtitle: String { invoiceNumber.isEmpty ? "Utan fakturanummer" : "Faktura \(invoiceNumber)" }
    var dateLabel: String { dueDate }
}

struct InvoiceLine: Codable, Hashable {
    let description: String
    let quantity: String
    let unit: String
    let unitPrice: Amount
    let vatRate: String
    let totalExVat: Amount
}

struct CustomerInvoice: Payable, Codable {
    let id: Int
    let totalAmount: Amount
    let paidAmount: Amount
    let remainingAmount: Amount
    let isPaid: Bool
    let isBookkept: Bool
    let status: String
    let statusLabel: String
    let paymentDate: String?
    let customerName: String
    let invoiceNumber: String
    let ocrCode: String
    let invoiceDate: String
    let dueDate: String
    let isOverdue: Bool
    let isCreditInvoice: Bool
    let vatAmount: Amount
    let amountExVat: Amount
    let lines: [InvoiceLine]?
    let attachments: [Attachment]?

    var title: String { customerName }
    var subtitle: String { invoiceNumber.isEmpty ? "Utkast" : "Faktura \(invoiceNumber)" }
    var dateLabel: String { dueDate }
}

enum ISODate {
    static let formatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func parse(_ text: String?) -> Date? {
        guard let text else { return nil }
        return formatter.date(from: text)
    }

    static func string(_ date: Date) -> String { formatter.string(from: date) }

    /// "5 okt. 2026" for lists; falls back to the raw text.
    static func display(_ text: String?) -> String {
        guard let text, let date = parse(text) else { return text ?? "–" }
        return date.formatted(.dateTime.day().month(.abbreviated).year().locale(Locale(identifier: "sv_SE")))
    }
}

struct ReportYear: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let startDate: String
    let endDate: String
}

struct ReportRow: Codable, Identifiable, Hashable {
    let account: Account
    let amount: Amount
    var id: Int { account.id }
}

/// A heading with account rows and a sum line; without title and rows it is a result line
/// (Rörelseresultat, Summa eget kapital och skulder).
struct ReportSection: Codable, Hashable {
    let title: String?
    let rows: [ReportRow]
    let totalLabel: String?
    let total: Amount?
}

struct ReportResult: Codable, Hashable {
    let label: String
    let amount: Amount
    let note: String?
}

struct MonthChoice: Codable, Identifiable, Hashable {
    let value: String
    let label: String
    var id: String { value }
}

/// Resultat- or balansräkning as the web shows it (api: resultatrakning/, balansrakning/).
/// The period fields only come with the income statement.
struct Report: Codable {
    let years: [ReportYear]
    let selectedYear: ReportYear?
    let sections: [ReportSection]
    let result: ReportResult
    let monthChoices: [MonthChoice]?
    let fromMonth: String?
    let toMonth: String?
    let periodStart: String?
    let periodEnd: String?
}
