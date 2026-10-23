# Security & Privacy

## Implemented (and tested)
| Control | How | Test |
|---|---|---|
| Password storage | scrypt, random salt, params in hash, rehash on login when params change | `test_core.TestPasswords` |
| Password policy | ≥10 chars, not trivially repetitive | `test_weak_password_rejected` |
| Brute force | 5 failures → 15 min lockout; per-IP login rate limit (10/min) | `test_lockout_after_repeated_failures` |
| User enumeration | same message + equalized hashing time for unknown users | `test_wrong_password_same_message_as_unknown_user` |
| Sessions | random 256-bit token, SHA-256 stored, 12h absolute + 2h idle expiry, revoke on logout / password change / org suspension | `TestAuth` |
| Cookie | HttpOnly, SameSite=Strict, Secure when `PHARM_COOKIE_SECURE=1` | `test_login_sets_httponly_cookie_and_headers` |
| CSRF | cookie-auth writes need `X-Requested-With: pharm` | `test_csrf_header_required_for_cookie_writes` |
| Authorization | permission checks inside services; roles per org | `test_permission_enforced_in_service`, `test_assistant_cannot_approve` |
| Tenant isolation | every query filtered by `ctx.org_id`; cross-tenant tests at service and HTTP level | `test_tenant_isolation` (×3), `test_cross_tenant_via_api` |
| Audit | append-only (DB triggers), written in the same transaction as the change | `TestAudit`, `test_edits_are_audited_and_original_kept` |
| Upload safety | magic-byte sniffing, size limit, storage path never from user input, spreadsheets served as attachment | `test_upload_garbage_rejected`, `test_sniff_rejects_unknown` |
| Headers | strict CSP (no inline script/style), nosniff, frame-ancestors none, no-store on API | API test |
| Errors | internal exceptions logged, generic message to client | handler |
| Self-granting features | entitlements settable only by platform operator | `test_tenant_cannot_grant_itself` |
| AI data minimization | only the invoice image is sent; no customer data | design (ai_vision.py) |
| Secrets | API keys from environment only, never in DB/repo | config.py |

## Known gaps (must close before multi-pharmacy / internet exposure)
1. **No HTTPS yet on the LAN.** Fine on `127.0.0.1`; phones on Wi-Fi need TLS (reverse proxy
   with a certificate) before use, also required for camera access.
2. **Database file is not encrypted at rest.** Protect the PC (Windows account password, BitLocker).
   Customer/patient data (future sales phase) will raise this: evaluate SQLCipher or OS encryption.
3. **Backups are local files.** Need an off-machine copy (encrypted) + a scheduled restore test.
4. **No MFA** for owner accounts (planned with the cloud phase).
5. **Rate limiter is in-memory** (resets on restart; per-process). OK for a single node.
6. **No automated dependency/security scanning** in this environment (no network). Add to CI
   when the GitHub repo exists.
7. **Health data (clinical phases):** retention policy, consent, and the Egyptian Personal Data
   Protection Law (Law 151 of 2020) obligations must be reviewed with a lawyer before storing
   patient medication histories or voice recordings.
