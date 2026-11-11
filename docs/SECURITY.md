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
| Owner sign-in | the owner's own password/PIN change needs his current password (users screen too); nobody else, not even with an owner approval, sets the owner's PIN (#131) | `test_sec_owner_credentials.py` |
| Authorization | permission checks inside services; roles per org | `test_permission_enforced_in_service`, `test_assistant_cannot_approve` |
| Tenant isolation | every query filtered by `ctx.org_id`; cross-tenant tests at service and HTTP level | `test_tenant_isolation` (×3), `test_cross_tenant_via_api` |
| Audit | append-only (DB triggers), written in the same transaction as the change | `TestAudit`, `test_edits_are_audited_and_original_kept` |
| Upload safety | magic-byte sniffing, size limit, storage path never from user input, spreadsheets served as attachment | `test_upload_garbage_rejected`, `test_sniff_rejects_unknown` |
| Spreadsheet formula injection | every exported workbook (screen reports, items export, order suggestions, dead stock, import templates) passes through `core/xlsx_safe.as_text`: a string starting with `=` `+` `-` `@`, tab or CR is stored as text, never as a formula (#139) | `test_sec_xlsx_formulas.py` |
| Headers | strict CSP (no inline script/style), nosniff, frame-ancestors none, no-store on API | API test |
| Errors | internal exceptions logged, generic message to client | handler |
| Self-granting features | entitlements settable only by platform operator | `test_tenant_cannot_grant_itself` |
| AI data minimization | only the invoice image is sent; no customer data | design (ai_vision.py) |
| Secrets | API keys from environment only, never in DB/repo; pre-commit guard blocks data files and key-like strings | config.py, `scripts/check_secrets.py` |
| Audit tamper evidence | sha256 hash chain; `manage.py verify` detects edited/deleted rows even with raw DB access | `TestAuditChain` |
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

### نسيان كلمة سر المالك (#151)
مفيش أي طريقة من الشبكة ولا كلمة سر افتراضية. الاسترجاع من جهاز الصيدلية نفسه بس (اللي قدامه الجهاز وفولدر البيانات):

```
cd /d "%LOCALAPPDATA%\Programs\PharmacyPlatform\program"
set PHARM_DATA_DIR=%LOCALAPPDATA%\PharmacyPlatform\data
"%LOCALAPPDATA%\Programs\PharmacyPlatform\python\python.exe" manage.py reset-owner-password
```

بيطلب كلمة السر الجديدة مرتين، وبيطبّق نفس شروط كلمة السر، وبيخرّج المالك من كل الأجهزة، وبيتسجّل في سجل التدقيق (`user.password_reset`, `via: local_cli`). لو فيه أكتر من مالك: `--username`.
