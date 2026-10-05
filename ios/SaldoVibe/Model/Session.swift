import Foundation
import Observation
import UIKit

/// Signed-in state: server, token, user, companies and the active company. Persisted in the
/// keychain; the active company id in UserDefaults.
@MainActor
@Observable
final class Session {
    struct Stored: Codable {
        var server: URL
        var token: String
    }

    private(set) var api: APIClient?
    private(set) var user: User?
    private(set) var companies: [Company] = []
    private(set) var company: Company?
    private(set) var choices: FormChoices?
    var isRestoring = true
    /// Bumped whenever something was written, so list screens know to reload.
    var changeCounter = 0

    var isLoggedIn: Bool { api != nil }
    var serverDescription: String { api?.baseURL.absoluteString ?? "" }

    private static let companyKey = "activeCompanyId"

    // MARK: - lifecycle

    func restore() async {
        defer { isRestoring = false }
        if let data = Keychain.read(), let stored = try? JSONDecoder().decode(Stored.self, from: data) {
            let client = APIClient(baseURL: stored.server, token: stored.token)
            do {
                try await adopt(client: client, me: client.get("me/"))
                return
            } catch let error as APIError where error.isUnauthorized {
                Keychain.delete()
            } catch {
                // Offline: keep the session so the app opens; requests will show the error.
                api = client
                let companyId = UserDefaults.standard.integer(forKey: Session.companyKey)
                if companyId != 0 {
                    client.companyId = companyId
                    company = Company(id: companyId, name: "…", vatRegistered: true, readOnly: false)
                }
                return
            }
        }
        #if DEBUG
        await devAutoLogin()
        #endif
    }

    func login(server: String, email: String, password: String) async throws {
        guard let url = Session.normalize(server: server) else {
            throw APIError(message: "Ange serverns adress, t.ex. https://saldovibe.example.se", status: 0)
        }
        let client = APIClient(baseURL: url)
        let response: LoginResponse = try await client.post(
            "auth/login/",
            json: ["email": email, "password": password, "device_name": UIDevice.current.name]
        )
        client.token = response.token
        try await adopt(client: client, me: MeResponse(user: response.user, companies: response.companies))
    }

    /// QR code / saldovibe://login?server=…&token=… – the token already exists on the server.
    func login(server: URL, token: String) async throws {
        let client = APIClient(baseURL: server, token: token)
        try await adopt(client: client, me: client.get("me/"))
    }

    func handle(url: URL) async -> String? {
        guard url.scheme == "saldovibe", url.host() == "login",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let server = items.first(where: { $0.name == "server" })?.value.flatMap(URL.init(string:)),
              let token = items.first(where: { $0.name == "token" })?.value, !token.isEmpty
        else {
            return "Länken gick inte att tolka."
        }
        do {
            try await login(server: server, token: token)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    func logout() async {
        if let api {
            try? await api.postExpectingNoContent("auth/logout/")
        }
        Keychain.delete()
        UserDefaults.standard.removeObject(forKey: Session.companyKey)
        api = nil
        user = nil
        companies = []
        company = nil
        choices = nil
    }

    func select(company: Company) {
        self.company = company
        api?.companyId = company.id
        choices = nil
        UserDefaults.standard.set(company.id, forKey: Session.companyKey)
        changeCounter += 1
    }

    func refreshCompanies() async {
        guard let api else { return }
        if let me: MeResponse = try? await api.get("me/") {
            user = me.user
            companies = me.companies
            if let current = company, let updated = me.companies.first(where: { $0.id == current.id }) {
                company = updated
            } else if company != nil {
                company = nil
            }
        }
    }

    /// Accounts, suppliers and employees for the forms; fetched once per company.
    func loadChoices() async throws -> FormChoices {
        if let choices { return choices }
        guard let api else { throw APIError(message: "Inte inloggad.", status: 401) }
        let loaded: FormChoices = try await api.get("formulardata/")
        choices = loaded
        return loaded
    }

    func invalidateChoices() { choices = nil }

    func didChange() { changeCounter += 1 }

    // MARK: - helpers

    private func adopt(client: APIClient, me: MeResponse) async throws {
        api = client
        user = me.user
        companies = me.companies
        choices = nil
        if let token = client.token {
            Keychain.write(try JSONEncoder().encode(Stored(server: client.baseURL, token: token)))
        }
        let remembered = UserDefaults.standard.integer(forKey: Session.companyKey)
        if let match = me.companies.first(where: { $0.id == remembered }) ?? (me.companies.count == 1 ? me.companies.first : nil) {
            select(company: match)
        } else {
            company = nil
            client.companyId = nil
        }
    }

    static func normalize(server: String) -> URL? {
        var text = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") {
            text = "https://" + text
        }
        while text.hasSuffix("/") { text.removeLast() }
        guard let url = URL(string: text + "/"), url.host() != nil else { return nil }
        return url
    }

    #if DEBUG
    /// Simulator conveniences (see ios/README.md): `-loginURL saldovibe://login?…` signs in the
    /// way a scanned QR code would; SALDOVIBE_DEV_SERVER/EMAIL/PASSWORD in the environment sign
    /// in with a password. Neither exists in Release builds.
    private func devAutoLogin() async {
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "-loginURL"), index + 1 < arguments.count, let url = URL(string: arguments[index + 1]) {
            // Same path as a scanned QR code; `simctl openurl` can't be used because it prompts.
            _ = await handle(url: url)
            return
        }
        let env = ProcessInfo.processInfo.environment
        guard let server = env["SALDOVIBE_DEV_SERVER"], let email = env["SALDOVIBE_DEV_EMAIL"],
              let password = env["SALDOVIBE_DEV_PASSWORD"]
        else { return }
        try? await login(server: server, email: email, password: password)
    }
    #endif
}

extension Session {
    /// User-facing text for an error. A 401 means the login was revoked on the server (or the
    /// QR token expired): forget it locally so the app returns to the login screen.
    func describe(_ error: Error) -> String {
        if let apiError = error as? APIError, apiError.isUnauthorized {
            Task { await forgetLocalSession() }
        }
        return error.localizedDescription
    }

    private func forgetLocalSession() async {
        Keychain.delete()
        api = nil
        user = nil
        companies = []
        company = nil
        choices = nil
    }
}
