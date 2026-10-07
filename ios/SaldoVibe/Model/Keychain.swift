import Foundation
import OSLog
import Security

/// What the keychain holds: the signed-in session. Shared with the share extension through the
/// keychain access group in the entitlements, so `companyId` lives here rather than only in
/// UserDefaults.
struct StoredSession: Codable {
    var server: URL
    var token: String
    var companyId: Int?
}

/// One blob in the keychain: the signed-in session (server, token, company).
enum Keychain {
    private static let service = "se.saldovibe.app"
    private static let sessionAccount = "session"

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func readSession() -> StoredSession? {
        read().flatMap { try? JSONDecoder().decode(StoredSession.self, from: $0) }
    }

    static func write(_ session: StoredSession) {
        if let data = try? JSONEncoder().encode(session) {
            write(data)
        }
    }

    static func read(account: String = sessionAccount) -> Data? {
        var query = query(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    static func write(_ data: Data, account: String = sessionAccount) {
        SecItemDelete(query(account) as CFDictionary)
        var query = query(account)
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        if status != errSecSuccess {
            // -34018 here means the build is unsigned (simulator without "Sign to Run Locally").
            Logger(subsystem: service, category: "keychain").error("SecItemAdd failed: \(status)")
        }
    }

    static func delete(account: String = sessionAccount) {
        SecItemDelete(query(account) as CFDictionary)
    }
}
