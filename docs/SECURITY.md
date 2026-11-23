# Security & Privacy

## Implemented (and tested)
| Control | How | Test |
|---|---|---|
| Password storage | scrypt, random salt, params in hash, rehash on login when params change | `test_core.TestPasswords` |
| Password policy | ≥10 chars, not trivially repetitive | `test_weak_password_rejected` |
| Failed password attempts | Bound concurrent verification. Record failures by account and peer, delay repeated errors, and alert owner and managers after five failures in 15 minutes. A valid password can still authenticate. | `test_failed_password_policy_has_no_account_wide_lockout` |
| User enumeration | same message + equalized hashing time for unknown users | `test_wrong_password_same_message_as_unknown_user` |
| Sessions | random 256-bit token, SHA-256 stored, 12h absolute + 2h idle expiry, revoke on logout / password change / org suspension | `TestAuth` |
| Cookie | HttpOnly, SameSite=Strict, Secure when `PHARM_COOKIE_SECURE=1` | `test_login_sets_httponly_cookie_and_headers` |
| CSRF | cookie-auth writes need `X-Requested-With: pharm`, logout included (#140) | `test_csrf_header_required_for_cookie_writes` |
| PIN sign-in | Same browser origin and JSON checks as password sign-in. Assistants and pharmacists get cashier permissions only. A PIN change revokes PIN sessions. | `test_pin_sign_in_applies_browser_origin_admission`, `test_pin_session_stays_bounded_and_pin_change_revokes_it` |
| Write approvals | The supervisor sees the server's operation summary. The grant is bound to the exact write and reserved before execution. An authorized retry may replay a recorded result. | `test_approval_checks_the_intent_before_the_action_starts`, `test_approved_response_replays_only_to_a_currently_authorized_request` |
| Release updates | Signed manifests cover code and packages. The installed launcher rechecks both before execution. Production installer publication requires the pinned Authenticode signer. | `test_prepared_release_signature_is_checked_before_activation`, `test_installed_launcher_checks_publisher_proof_and_exact_release_files` |
| Owner sign-in | the owner's own password/PIN change needs his current password (users screen too); nobody else, not even with an owner approval, sets the owner's PIN (#131) | `test_sec_owner_credentials.py` |
| Authorization | permission checks inside services; roles per org | `test_permission_enforced_in_service`, `test_assistant_cannot_approve` |
| Tenant isolation | every query filtered by `ctx.org_id`; cross-tenant tests at service and HTTP level | `test_tenant_isolation` (×3), `test_cross_tenant_via_api` |
| Audit | append-only (DB triggers), written in the same transaction as the change | `TestAudit`, `test_edits_are_audited_and_original_kept` |
| Upload safety | magic-byte sniffing, size limit, storage path never from user input, spreadsheets served as attachment | `test_upload_garbage_rejected`, `test_sniff_rejects_unknown` |
| Request size before reading | JSON bodies and every upload route are refused from `Content-Length` before reading; a body without a length is read only up to the limit; uploads need a `Content-Length` (#140). JSON limit 256 KB, except the screen-table Excel export (8 MB; its rows are capped at 50,000) | `test_sec_misc_hardening.TestRequestSize`, `test_export_xlsx_size` |
| Spreadsheet bombs | an uploaded xlsx is refused if it unpacks past 256 MB or holds more than 5 million cells (a tiny file can declare a huge sheet) (#140) | `test_sec_misc_hardening.TestSpreadsheetBomb` |
| No personal data in the server log | the access log keeps the path, never the query string (customer search by phone or name) (#140) | `test_sec_misc_hardening.TestAccessLog` |
| Spreadsheet formula injection | every exported workbook (screen reports, items export, order suggestions, dead stock, import templates) passes through `core/xlsx_safe.as_text`: a string starting with `=` `+` `-` `@`, tab or CR is stored as text, never as a formula (#139) | `test_sec_xlsx_formulas.py` |
| Host allowlist (DNS rebinding) | every request whose `Host` is not `127.0.0.1`/`localhost`/`[::1]`, the address the connection arrived on (LAN mode), or a name in `PHARM_ALLOWED_HOSTS` gets 403 `bad_host`; a Host port must be the listening port (#136) | `test_sec_host_header.py` |
| Headers | strict CSP (no inline script/style), nosniff, frame-ancestors none, no-store on API | API test |
| Errors | internal exceptions logged, generic message to client; malformed import fields and item ids answer 422, not 500 (#140) | handler, `TestBadFields` |
| Self-granting features | entitlements settable only by platform operator | `test_tenant_cannot_grant_itself` |
| AI data minimization | only the invoice image is sent; no customer data | design (ai_vision.py) |
| Secrets | API keys from environment only, never in DB/repo; pre-commit guard blocks data files and key-like strings | config.py, `scripts/check_secrets.py` |
| Audit tamper evidence | sha256 hash chain; `manage.py verify` detects edited rows and rows deleted in the middle even with raw DB access. **Limit:** the newest rows deleted from the end leave no trace yet (no anchor outside the DB file), #229 | `TestAuditChain` |
| No secrets in audit | password/token keys scrubbed at any depth | `test_no_secrets_in_audit` |
| Replay / double submit | Idempotency-Key on POSTs; domain-level ids for sale lines and payment | `TestApiHardening`, `TestSaleIdempotency` |
| Cross-tenant sweep | every id route called by another pharmacy with real ids: no 2xx, no data change | `test_isolation_sweep.py` |
| Import tenant safety | link decisions validated against the importing pharmacy | `test_import_cannot_touch_other_tenant` |
| Feature switches | a disabled tool answers 403 without affecting others | `test_feature_switch` |
| Backup integrity | backups verified on write and before restore; restore refuses tampered archives and keeps a pre-restore copy | `test_backup_resilience.py` |

## Review 2026-10-29 (LAN mode, local-only routes): no new defect
- First-run setup, first-run restore and PIN quick login accept only the PC itself (`req.client.host`,
  the TCP peer; uvicorn trusts forwarded headers only from 127.0.0.1). LAN mode is off by default,
  owner-only, audited, and its screen says not to enable it on a Wi-Fi open to customers.
- Retried money POSTs can no longer post twice (generic Idempotency-Key reservation, #23).
- Still open, see gaps below: plain HTTP in LAN mode (session cookies readable by anyone on the same
  Wi-Fi), no encryption at rest, no MFA.

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

## After launch: designs, no code yet (from #131)
Each item needs a bigger change than is safe before the launch. The trade-offs are written down so the
choice can be made later without repeating the analysis.

### Backup encryption and PIN hashes (#138)
- **Now:** a backup is the whole database in clear (customers, staff password and PIN hashes); a 4–6 digit
  PIN hash falls to an offline attacker in minutes to hours.
- **Option A, PIN pepper (smaller):** PIN hashes become HMAC-SHA256(pepper, scrypt(pin)), with the pepper in a
  file outside the data folder (`%USERPROFILE%\.pharmacy\pin_pepper`), never in a backup. A stolen backup
  no longer gives the PINs. Cost: a restore on a new PC asks every user to set the PIN again (passwords
  still work), and losing the pepper file means the same.
- **Option B, encrypted backups (complete):** AES-GCM with a key derived (scrypt) from a backup password the
  owner chooses once; the restore screen asks for it. Protects customer data too. Cost: a forgotten password
  means a useless backup, so it needs a printed recovery code and a restore test at setup; the
  first-run restore and `manage.py restore` both change; a new dependency (`cryptography`) ships only through
  the installer.
- **Recommendation:** A first (cheap, no lost data risk), B with the off-site copy (flash drive / cloud).
  Meanwhile `BACKUP.md` should say plainly that a backup equals full access.

### LAN exposure (phones on the pharmacy Wi-Fi, #140 item 4)
- **Now:** LAN mode binds `0.0.0.0` (every adapter, including Radmin VPN) over plain HTTP; the Host check
  (#136, PR #154) accepts only the address the request arrived on.
- **Design:** bind only to the shop LAN address the owner picks on the LAN screen (re-checked at every start,
  fallback to 127.0.0.1 if it is gone), plus a Windows Firewall rule for the Private profile only, added by
  the installer. Phones then need HTTPS: a local certificate authority created on the PC and installed on each
  phone once (also needed for the camera).
- **Trade-offs:** a DHCP address change hides the program from phones until the owner picks again (a fixed
  address on the router avoids it); the firewall rule needs admin once at install; certificate setup on each
  phone is a manual step. Until then: keep LAN mode off, or on only on a Wi-Fi with a password customers do
  not have.

### نسيان كلمة سر المالك (#151)
مفيش أي طريقة من الشبكة ولا كلمة سر افتراضية. الاسترجاع من جهاز الصيدلية نفسه بس (اللي قدامه الجهاز وفولدر البيانات):

```
cd /d "%LOCALAPPDATA%\Programs\PharmacyPlatform\program"
set PHARM_DATA_DIR=%LOCALAPPDATA%\PharmacyPlatform\data
"%LOCALAPPDATA%\Programs\PharmacyPlatform\python\python.exe" manage.py reset-owner-password
```

بيطلب كلمة السر الجديدة مرتين، وبيطبّق نفس شروط كلمة السر، وبيخرّج المالك من كل الأجهزة، وبيتسجّل في سجل التدقيق (`user.password_reset`, `via: local_cli`). لو فيه أكتر من مالك: `--username`.
