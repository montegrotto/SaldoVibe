import Foundation
import OSLog
import Security

/// One blob in the keychain: the signed-in session (server, token, company).
enum Keychain {
    private static let service = "se.saldovibe.app"
    private static let account = "session"

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func read() -> Data? {
        var query = query
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    static func write(_ data: Data) {
        SecItemDelete(query as CFDictionary)
        var query = query
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        if status != errSecSuccess {
            // -34018 here means the build is unsigned (simulator without "Sign to Run Locally").
            Logger(subsystem: service, category: "keychain").error("SecItemAdd failed: \(status)")
        }
    }

    static func delete() {
        SecItemDelete(query as CFDictionary)
    }
}
