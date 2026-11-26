# SEC-1 security review — 2026-11-23

Reviewed `develop` at `78c36461e44f365c432e1ff0686be6c5d45fe20e`. Source locations refer to that revision. The target is the local Windows POS, its browser, installer, updater, SQLite data, backups, and public Git history. The review distinguishes the default loopback deployment from optional LAN mode. Findings are based on source, repository metadata, and synthetic checks. Windows deployment controls and the pharmacy PC were outside the verification environment.

**Disposition.** Nine P1 findings and no confirmed P0. Three findings directly affect cash, pricing, or retries. The remaining P1s cover account availability, update authenticity, and exposure of data in transit, at rest, in backups, and in Git history. P1 denotes a material release risk for a pharmacy deployment, with prerequisites stated below. P2 denotes a narrower integrity or exposure gap. P3 denotes hardening or coverage work. Scenarios describe the actor, prerequisite, and impact without publishing reproduction recipes, credentials, or locators for sensitive exports.

## P1 findings

### SEC-01 · P1 · Failed passwords can deny a valid login

**Evidence.** `app/core/accounts.py:14–17,103–124` checks persisted per-user lock state before verifying the submitted password. `app/api/app.py:201–205,438–448,801–809` adds a peer limiter and shares it between password and PIN login. On a shared workstation, loopback clients share that peer. `app/core/people.py:50–73` implements a separate PIN lock that also applies when an approver's PIN is verified.

**Scenario.** Someone who knows a staff username and can reach the sign-in service can cause the staff member's valid password to be refused during a shift. Shared-source throttling creates a second availability problem on a shared workstation. The PIN state can separately block quick sign-in and approvals.

**Fix.** Remove persistent account-wide denial caused by unauthenticated mistakes. Bound aggregate verification work, track abusive attempts by client and account pair, and preserve a verified-login path on a shared client. Separate PIN sign-in, approval and password controls. Clear legacy lock state safely. Preserve generic error messages and security event logging. Replace the existing account-lock expectations in `tests/test_core.py:77` and `tests/test_people.py:91` as part of the policy change.

### SEC-02 · P1 · Purchases and creation can set catalog prices without price authority

**Evidence.** `app/catalog/service.py:118–167,183–198` distinguishes `items.write` from `items.price` on edit, while item creation can persist a supplied public price. `app/purchasing/service.py:771–788,819–828,864–891` can seed pricing on a new item and post incoming batch and existing catalog price changes upon purchase approval. `app/core/permissions.py:96–104` grants pharmacists purchase approval and item creation while reserving explicit price permission for higher roles. Item import delegates creation and updates to those same catalog services at `app/dataimport/service.py:859–883`. `app/sales/service.py:915–938` evaluates the below-cost discount rule only for discounted lines with a known average cost.

**Scenario.** A pharmacist entitled to record deliveries can make an unauthorized retail price effective on purchased stock. A later cashier can use that retail price during ordinary checkout. This affects existing catalog prices and can also affect prices attached to new batches or newly created items.

**Fix.** Define which purchase price observations are informational and which change the effective selling price. Require `items.price` or an explicit transaction-bound approval for the latter on every route. Preserve prices on ordinary intake when possible, gate corrections before the first write, and audit both amounts and approver. Specify whether an already-below-cost baseline and unknown-cost items may be sold at all.

### SEC-03 · P1 · A manual payout can overdraw physical cash

**Evidence.** `app/api/app.py:2475–2495` delegates to `app/sales/cash.py:184–193`. The movement checks a positive amount, kind and reason. The shared recorder checks the open shift and writes the entry at `app/sales/cash.py:38–46`. The legacy supplier-payment path also records drawer cash directly (`app/api/app.py:1082–1095`, `app/procurement/suppliers.py:115–150`). The newer supplier and treasury payment paths check available cash at `app/procurement/suppliers.py:87–110` and `app/finance/money.py:186–194`.

**Scenario.** A cash manager can record a payout larger than the available drawer balance. The ledger can imply an impossible physical outflow. Returns, voids, and historical refunds also use the shared cash recorder and require explicit policy for when cash is unavailable.

**Fix.** Enforce available cash for physical payouts inside the same write transaction. Decide and document how refunds and voids should behave when the current shift has insufficient cash. Separate the mechanisms for correcting historical records and moving actual cash. Check that a refusal changes neither balances nor audit history.

### SEC-04 · P1 · Money replay protection can be lost at the user-intent boundary

**Evidence.** `app/api/app.py:323–347,368–390` treats a missing or oversized key as a normal POST. `app/core/idempotency.py:40–66` does so for cash and customer payments too. `app/web/app.js:281–345` preserves keys for its listed operations in memory. Ordinary partial sale returns are absent from that list and have no independent intent identifier at `app/api/app.py:2347–2357`. `app/sales/service.py:1490–1528` creates a separate return transaction for each accepted request. Completion has separate request-id handling at `app/api/app.py:2332–2344`.

**Scenario.** A cashier loses the response after a partial return commits and submits the same intended return again. If enough units remain returnable, stock and the refund can post twice. Similar ambiguity exists after a reload for key state held only in the browser's memory.

**Fix.** Require a stable operation ID for financially material mutations, preserve it across retries and reload until reconciled, and commit unique intent, financial entries, and a replay-safe response together. Use resource state and mandatory version or shift identity where that is the stronger invariant. Test lost responses, concurrent attempts, reloads, and retries after the current seven-day retention period. Keep deliberate repeated scan actions independent.

### SEC-05 · P1 · A production update path accepts code without a release signature

**Evidence.** `app/api/app.py:551–561,608–630` retains the classic branch-based update, which defaults when the action is absent. `manage.py:650–688,700–705` stages both a GitHub source download and a local ZIP and starts candidate Python without a publisher-signature check on those paths. `app/core/smart_update.py:365–401` provides a signed alternative with channel, forward progress, and asset checks. `release_bootstrap.py:116–139` checks active code against a local manifest. The update workflow explicitly keeps the code-only path when the signing key is unavailable at `.github/workflows/update-bundle.yml:76–90`.

**Scenario.** If control of trusted branch contents or offline update media is lost, an authorized operator can select an update whose code runs on the POS with the Windows account's access. The isolated self-test points at a scratch database but does not sandbox filesystem or OS access.

**Fix.** Require a publisher-authenticated manifest before unpacking or executing any production candidate, online or offline. Sign the installer with Authenticode and verify the installer and published release provenance in CI. Keep the trust root and installed manifest under installer/service control. Verify the selected code and dependencies again before execution. Maintain a signed per-channel version floor and a separately authorized, compatible emergency rollback. Keep atomic pointer selection and the existing health-supervised recovery.

### SEC-06 · P1 · Optional LAN mode sends credentials and sessions over HTTP

**Evidence.** `app/api/app.py:1417–1439` offers HTTP LAN addresses. `launcher.py:212–217` binds all interfaces in LAN mode. `manage.py:168–179` starts Uvicorn without TLS configuration. `app/config.py:41–55` defaults the secure-cookie setting off. `app/api/hostcheck.py:59–79` validates request authority names. That check supplies no transport confidentiality.

**Scenario.** On a pharmacy network with LAN mode enabled, a device able to observe or alter traffic can see credentials or authenticated requests and potentially act as a signed-in operator. Default loopback keeps this exposure off the network.

**Fix.** Keep loopback as the default. Require TLS with a trusted certificate, secure cookies, and an interface/firewall allowlist before enabling client access. Disable LAN mode if these conditions cannot be met. Verify the installed Windows build on a separate test network.

### SEC-07 · P1 · Backup archives lack confidentiality and authenticated provenance

**Evidence.** `app/core/backup.py:278–319,342–369` stores SQLite, optional history, and uploads in a compressed ZIP and trusts checksums and counts from the archive's own manifest. `app/core/autobackup.py:355–372` copies that archive to the configured offsite destination. `app/api/app.py:670–714` accepts standard verification as its first-run restore gate. `app/core/backup.py:449–460` revokes restored sessions as an independent safeguard.

**Scenario.** Whoever obtains a lost USB backup can recover pharmacy data and password/PIN hashes. A replacement backup with internally consistent metadata also lacks a trustworthy origin check, so an operator can restore a database from the wrong or untrusted source.

**Fix.** Encrypt and authenticate the whole backup with a versioned AEAD format and a securely managed key with a documented recovery path. Authenticate scope and format before extracting or opening SQLite. Bind restores to the intended pharmacy or require deliberate enrollment on a replacement device. Put historic unencrypted imports behind an explicit offline migration flow. Verify recovery, invalid authentication, key loss and attachment integrity with synthetic data.

### SEC-08 · P1 · The live database and installed code depend on the operator's Windows profile

**Evidence.** `installer/pharmacy.iss:17` uses the current user's install privilege. `launcher.py:31–36,212–217` uses that profile and starts the program in the same identity. `app/config.py:32–46` and `app/db.py:53–71` use a regular SQLite file and WAL. `manage.py:756–786` treats local data access as sufficient authority for owner recovery. `app/licensing/service.py:49–82` defines licensing checks, while `manage.py:164–179` and `app/api/app.py:174–181,227–255` do not enforce them at startup or request authentication. `docs/SECURITY.md:42–49` records the at-rest gap.

**Scenario.** An employee working under the Windows identity that runs the POS can read its files independently of the employee's browser permissions. Another person given a copy of a regular database file can read data outside the POS login flow. The application also has no enforced device entitlement at the ordinary installed entry point.

**Fix.** Run the server under a dedicated Windows service identity. Deny cashier OS accounts access to data, executable code, keys and recovery tools. Use full-volume encryption for physical theft and key-backed database/file protection as required for copies. Add signed, device-bound entitlements at the supported launch entry point and protect release state. Publicly distributed local code is copyable, and administrators who control a running device are outside what application authentication alone can protect.

### SEC-09 · P1 · Public Git history contains pharmacy operating exports

**Evidence.** `docs/OWNER_RULES.md:55–59,109–112` records the rule against new real data and the temporary public visibility decision. `docs/HANDOFF.md:424` records management's classification of the earlier branch contents. GitHub repository metadata reported public visibility. A metadata-only pass over 344 remote refs, including the default-ref alias, found one published branch with reachable history for 14 raw operating spreadsheets. The documented categories include sales, costs, profit, supplier invoices and employee names. Two supplier-catalog branches were checked as well. Their published trees do not contain those raw pharmacy spreadsheets. Catalog build metadata and an originals checksum inventory alone do not establish exposure of original supplier documents. Spreadsheet contents, customer-level fields, downstream copies and downloads were unverified.

**Scenario.** A person with no pharmacy login can retrieve operating exports from published repository history. A cleanup limited to the current `develop` tree leaves branch history, pull-request refs, forks and existing clones as separate exposure paths.

**Fix.** Have the owner and repository administrator contain and inventory the exposure. Review access, remove sensitive objects from affected branches and reachable PR refs, coordinate cache removal and rotation if live secrets are identified, and treat prior copies as potentially retained. Keep the source repo private for commercial distribution if source confidentiality is required. Add a policy scan of every ref, including artifacts, alternate formats and data content classification.

## P2 and P3 findings

### SEC-10 · P2 · PIN sign-in uses a different origin check and can create a full privileged session

**Evidence.** Password login and setup apply browser-specific admission checks at `app/api/app.py:438–443,465–470`. PIN login checks the socket peer and parses a body at `app/api/app.py:789–815`. `app/core/people.py:29–33,76–94` permits a short PIN to create the same kind of session as password login. Regular cookie writes require `X-Requested-With` at `app/api/app.py:227–239`. No CORS middleware is installed at `app/api/app.py:3409–3410`. Host verification and strict cookies reduce cross-site risk. A PIN change updates the hash and failure state without revoking existing sessions (`app/core/people.py:37–47`). Browser-level login-CSRF behavior remains unverified.

**Scenario.** A shared terminal receives an unsolicited PIN sign-in attempt from an untrusted browser context, or someone uses a copied privileged PIN at the terminal. Successful PIN authentication has the account's full privileges, including sensitive settings where granted.

**Fix.** Apply the same strict browser admission policy to all session creation, and require an explicit origin and expected media type for browser writes. Reserve PIN-only sign-in for bounded cashier roles. Require recent password reauthentication for release, network, backup, and user administration. Track the authentication method and revoke sessions created with a changed PIN. Test all three browser authentication flows and bearer/cookie precedence.

### SEC-11 · P2 · Approval authorization is finalized after the action begins

**Evidence.** `app/api/app.py:1484–1500` issues a grant for a caller-specified permission, method and path. `app/core/approvals.py:77–112` binds the body when the grant is first used. `app/api/app.py:406–416` consumes it after a successful response. The grant has a session binding, short TTL and permission recheck.

**Scenario.** A supervisor approves what the operator shows on screen, while the eligible first request is determined later. Paths with asynchronous work can also see overlapping same-intent requests before grant consumption.

**Fix.** Show the supervisor a server-calculated action summary and bind the grant at issue to a canonical operation ID, transaction data and target. Acquire the grant once at effect commit, allowing a replay only of the recorded response. Keep administrative delegation explicit.

### SEC-12 · P2 · Audit omissions and trust gaps remain

**Evidence.** Draft creation and discard mutate state without audit entries at `app/sales/service.py:440–450,1082–1091`. The backup handler requires permission but omits actor identity from the run at `app/api/app.py:1576–1583`. Logout revokes the token without a dedicated audit event at `app/core/accounts.py:194–197`. Failed password events omit org attribution at `app/core/accounts.py:116–123`, while organization reports filter by org at `app/core/audit.py:97–100`. The hash chain checks continuity only within the file at `app/core/audit.py:34–60,74–94`; its missing external head anchor is documented at `docs/SECURITY.md:28`.

**Scenario.** An owner investigating a disputed shift cannot reconstruct some session and draft actions. Someone with filesystem access can remove the newest portion of a log, and a verifier that only knows the in-file chain can still see a contiguous prefix.

**Fix.** Write attribution and the required before/after facts in the same transaction for every material action. Define session, failed sign-in, restore, and manual backup events. Keep unaffiliated auth events in a restricted installation log. Sign and retain periodic sequence/head checkpoints outside the writable data store. Verify refusal, tail loss, and restore reconciliation against a trusted checkpoint.

### SEC-13 · P2 · Restore can leave multiple storage roots out of sync

**Evidence.** `app/core/backup.py:473–513` copies attachments into the live upload root before swapping archived history and the database. `launcher.py:205–211` archives the pending restore after invoking the child without checking its exit status. `app/api/app.py:678–714` uses a single staging filename during first-run upload and verification.

**Scenario.** A valid first-run restore or offline repair is interrupted by a locked file, a full disk, or another restore. Some files can reflect the attempted restore while the old database remains selected. The launcher can retain the input under a name that implies completion despite a failed child exit.

**Fix.** Stage each restore under a unique identity, validate all members and the resulting database, and journal a recoverable commit across attachments, history and SQLite. Serialize setup and restore. Record an attributed outcome, preserve the failed input distinctly, and check process status before reporting success. Test power-loss boundaries and locked files on Windows.

### SEC-14 · P2 · Several inputs accept ambiguous types or implicit defaults

**Evidence.** `app/core/numbers.py:4–25` implements bounded whole-number parsing. `app/api/app.py:1309–1315,1427–1431,1538–1541` coerces decisions and settings using Python truthiness. `app/sales/cash.py:101–115` normalizes denomination keys into a map and can silently replace a duplicate normalized denomination. `app/api/app.py:2503–2520` accepts a close without the viewed shift ID.

**Scenario.** A stale integration can send a textual false value for an expense decision or network setting and cause the reverse action. A stale shift close that lacks its target identity can apply a count to a later shift. Alternate representations of the same denomination can silently change the recorded count.

**Fix.** Validate real booleans and typed objects, reject duplicate normalized keys, and require the immutable shift identity on all close requests. Validate units, bounds, totals and domain constraints consistently between API and service. Reject the entire request before any write.

### SEC-15 · P2 · Backup and restore paths need destination and archive containment policy

**Evidence.** `app/core/autobackup.py:190–198,355–372` accepts an absolute destination and prunes at that destination. `app/core/backup.py:147–196,342–350,473–486` checks registered members but also tests and extracts ZIP entries without a single manifest allowlist and aggregate extraction budget. Purchase and sale attachments use org-scoped lookup and derived basenames at `app/purchasing/service.py:86–103` and `app/sales/attachments.py:89–100`.

**Scenario.** A sensitive backup can be copied by the POS to an unintended mounted location. A damaged or untrusted archive can exhaust restore resources, contain extra attachments, or collide when member names are normalized by Windows. Local connections through junctions or other reparse points also need a deployment check.

**Fix.** Restrict backup destinations to approved roots and device classes. Explicitly authorize any network share. Before extraction, validate canonical paths, unique Windows-normalized names, member allowlist, number, ratio, total expanded bytes and available space. Reject links and reparse points where they cross a trusted root. Read and restore only registered members.

### SEC-16 · P2 · Build inputs have exact versions but lack independent hash enforcement

**Evidence.** `requirements.txt:6–32` pins versions. `.github/workflows/windows-installer.yml:79,106` and `.github/workflows/update-bundle.yml:93–117` install dependencies without per-wheel hashes. The update workflow references actions by mutable major-version tag. The PyPI advisory check reported no known vulnerabilities for the pinned Python packages, including the Windows-only entries checked by removing their platform markers in a temporary copy. That scan did not inspect the Windows wheels or embedded native runtimes.

**Scenario.** A compromised dependency publishing or build channel can influence the application before release signing. Untrusted spreadsheets, PDFs, images, and uploads exercise these parsers during ordinary intake.

**Fix.** Use platform-specific hash locks and pinned action commits. Scan both shipped environments and the installer SBOM, sign the installer, and isolate and limit document parsers. Enforce advisory and provenance gates before signing a production bundle.

### SEC-17 · P3 · Secret scanning has history and format blind spots

**Evidence.** `scripts/check_secrets.py:12–22,31–45` uses a format and pattern list and permits certain paths. CI passes current tracked paths at `.github/workflows/ci.yml:74`. The tracked-tree guard reported no matches. An independent pattern scan of 3,821 reachable text blobs outside export paths found zero matches for the selected GitHub, AWS, AI-key and PEM patterns. The scan excluded binaries, exports, platform secret settings, runtime credentials and remote refs outside the fetched set. `app/config.py:41–51` reads AI configuration from the environment. `app/core/updater.py:43–57` supports a local update token file.

**Scenario.** A secret in an older commit, an archive, or an unsupported credential format can survive a clean current-tree scan.

**Fix.** Add redacted, provider-aware scanning of all reachable refs and release artifacts. Keep scoped tokens in OS-controlled storage and revoke any verified disclosure. Mark public trust keys and synthetic fixtures explicitly so the scanner can distinguish them from private material.

### SEC-18 · P3 · Render and SQL guard coverage should follow each import sink

**Evidence.** Browser item labels encode each name at `app/web/app.js:193–196`, and customer POS search, profile and archived-history tables encode text at `app/web/app.js:4414,5293–5305,5901`. Printing escapes names at `app/api/app.py:2828–2846,2869–2878`. The default CSP restricts scripts to same-origin at `app/api/app.py:108–114,145–152`. Value binding and org predicates were observed in `app/catalog/search.py:72–101` and `app/dataimport/estock_history.py:235–241,424–440`. Archive identifiers are built from expected table and column names at `app/dataimport/estock_history.py:78–92`. No reachable stored XSS from an item or customer name or request-value SQL injection was confirmed in the paths traced.

**Scenario.** A new report or document renderer can introduce an unescaped text sink, or a new archive query can accidentally treat input as a SQL identifier. These would become reachable through ordinary data imports.

**Fix.** Use text nodes or context-aware escaping throughout, and keep the CSP. Add inert punctuation and markup-shaped text fixtures across import, search, detail, printable receipts, and reports. Bind values, allowlist schema identifiers, reuse an identifier-quoting function where needed, and compare results for confusing names and identifiers in synthetic archives.

### SEC-19 · P2 · Some branch-changing operations use organization-only authorization

**Evidence.** `app/core/accounts.py:179–191` supplies the actor's branch. Delivery listing filters by it at `app/sales/service.py:1331–1335`. Delivery mutation and cash posting use the selected sale's branch after an organization-only lookup at `app/sales/service.py:1293–1323`. Purchase payments likewise derive the receiving branch at `app/purchasing/service.py:294–296`.

**Scenario.** If a pharmacy configures multiple branches and intends staff to work within one branch, a staff action on another branch's delivery can post cash into that branch. Current single-branch deployments do not have that cross-branch case.

**Fix.** Make branch-wide access an explicit permission. Gate every branch-owned resource at load and resolve, including checkout, warehouse receipts, count sessions and payment sources. Build a two-branch test matrix with staff-only and cross-branch operator cases.

### SEC-20 · P2 · Supplier discount validation does not cap the combined settlement

**Evidence.** `app/api/app.py:1096–1107` computes cash and discount parts and posts both in one transaction. `app/procurement/suppliers.py:132–150` applies the due/advance rule to cash. `app/finance/reconcile.py:74–92,100–136` checks the direction of the adjustment and records the discount separately. The combined amount is not compared with the due balance on this path.

**Scenario.** A reconciliation operator records an early-payment discount that is inconsistent with the remaining supplier balance. A valid cash payout can still leave an unsupported supplier credit or conceal a payable.

**Fix.** Validate the combined settlement under the posting transaction, identify the invoices or accrued credit being cleared, and require an independently authorized and reasoned exception for legitimate residual credit. Keep the payment and discount fully atomic.

## Server authorization and validation inventory

Route numbers below refer to `app/api/app.py`. Service checks are evaluated with current session permissions. Where an approver can lend permission, visibility checks that use `ctx.can` remain actor-only (`app/core/permissions.py:131–146`). Expected stock, money and audit writes should stay unchanged when any check fails.

Session tokens use cryptographic randomness and are stored as hashes (`app/core/security.py:70–75`, `app/core/accounts.py:150–178`). The server enforces a 12-hour absolute lifetime and a two-hour idle limit, re-reads active membership and effective permissions, and invalidates sessions on credential reset (`app/core/accounts.py:14–15,164–191,200–213`, `app/core/people.py:278–305`). Browser sign-in cookies are HttpOnly and SameSite Strict (438–456,801–815). Cookie-authenticated writes require the custom header, bearer authorization takes precedence, API responses use no-store, and the service worker limits its cache to the shell (145–155,227–239, `app/web/sw.js:1–24`). The Host allowlist precedes handlers, checks the effective address and configured names, and disables proxy-header interpretation in the normal server entry point (3409–3410, `app/api/hostcheck.py:59–79`, `manage.py:178–179`).

| Route group and registration | Server-side gates and checks observed |
| --- | --- |
| Sales create, update details, add/edit/remove lines, discard, preview and complete (3289–3306) | `sales.create` and scoped draft visibility in `app/sales/service.py:440–461,511–585,751–820,1072–1098,1140–1177,1295–1337`. Completion checks version/state, branch stock, allocations, tender, customer and discounts. Tests should include repeated line entry, completion replay, cross-org IDs, invalid payment splits and unavailable stock. |
| Line and invoice discounts (3300–3301) | `sales.create`, fixed/role limits, fresh recalculation, below-cost discount refusal for everyone and at-cost authorization with reason in `app/sales/service.py:915–941,1188–1293`. Pricing across purchase and sale has a policy gap in SEC-02. |
| Sale return, exchange and void (3307–3310), E-Stock return (3319) | `sales.return`, conditional other-user, late, and method checks, remaining quantity and tender caps in `app/sales/service.py:1339–1368,1462–1509`. Exchange also requires `sales.create` and uses a common transaction (`1607–1688`). Void requires `sales.void` (`1704–1764`). Archived returns require other-user permission and conditional late permission (`app/sales/estock_returns.py:267–297`). Cash availability and repeats are SEC-03/04. |
| Delivery and customer operations (3299, 3321–3326) | Delivery checks organization and state, posts to the sale's branch, and uses `sales.create` (`app/sales/service.py:1295–1330`). Customer creation and collections require `customers.manage`, and collections check debt inside the posting transaction (`374–436`, API `2437–2459`). Profile mutation and refills use `customers.manage` (`app/sales/customers.py:191,266,281`). Contract changes/link use `contracts.manage` (`app/sales/contracts.py:26–73`). |
| Supplier creation, update, pay and supplier returns (3222, 3235–3240) | `suppliers.write` for creation/update, `suppliers.pay` for payment, conditional advance exception, due/balance checks in `app/catalog/service.py:25–48` and `app/procurement/suppliers.py:34–60,88–152`. Supplier returns use `purchases.approve` and conditional permission for immediate money or bonus fractions (`app/procurement/returns.py:20–28,74–103,322–367`). |
| Purchase entry/import, header/line edit, split/merge/delete, new item, acknowledge, approve and cancel (3244–3257) | `purchases.create` for entry/import, `purchases.edit` for draft writes, `items.write` for item creation, `purchases.approve` for acknowledgements/approval and `purchases.cancel` for approved cancellation (`app/purchasing/service.py:108–138,427–443,530–774,790–848,950–992`). Approval validates invoice date, line issues, effective totals, and payment before posting. Immediate payment requires `suppliers.pay` and is part of approval (`328–357,913–925`). Price effects are SEC-02. |
| Item create/update, flags, category, barcode, shortage, adjustment, pack correction (3200–3234) | Catalog writes use `items.write`, with `items.price` and `items.hidden` on corresponding updates, and block direct pack changes for items with movements (`app/catalog/service.py:118–167,183–203,247–266`). Flags require `items.write` (889–905). Category, shortage and barcode changes use `items.write` and restricted category changes also use `items.no_return` (1033–1066, `app/catalog/card.py:43–56,84–110,189–190`). Stock adjustment requires `inventory.adjust` (`app/inventory/adjust.py:85–117`). Pack correction requires both `items.write` and `inventory.adjust` and audits the correction (`app/catalog/repack.py:38–39,109–114`). |
| Data import, stock count and procurement changes (3259–3264, 3279–3287, 3374–3378) | Import analyze/apply use `items.import` plus stock or party rights by import type (`app/dataimport/service.py:218–226,679–727,859–883`). Count create/approve/cancel use `inventory.adjust` and record uses `inventory.count` (`app/inventory/counting.py:39–42,88–92,244–253,316–317`). Procurement order create/edit and supplier-name memory use `purchases.create`. Consumption import uses `items.import` (`app/procurement/purchase_plan.py:118–120,151–153,436–437`, `app/procurement/service.py:319–328`). |
| Cash open, manual movement, close, float and drawer (3334, 3344–3356) | `sales.create` opens a shift, `cash.manage` moves cash, `cash.close` closes and money handover applies conditional manager/approver rules (`app/sales/cash.py:134–148,184–269`). API additionally gates movement visibility and float changes (2475–2497,2674–2681). Physical drawer open requires `cash.manage` (2800–2805, `app/printing/service.py:156–159`). See SEC-03/04/14. |
| Treasury and accounts, counts, opening corrections, transfers, owner movements, routes, reconciliation (3357–3371) | Account creation/edits and routing require settings, transfer and owner movement require `cash.manage`, account count also requires `cash.see_expected`, and opening adjustment has separate settings/visibility checks (`app/finance/money.py:265–323,512–517,556–617,686–691`). Reconciliation and party opening corrections have separate party gates (`app/finance/reconcile.py:38–39,67–70,127`, API `2594–2633`). Despite the name, owner movements are delegated to cash managers. Confirm that role contract before deployment. |
| Expenses, schedules, staff/payroll and proof review (3179–3194) | Expense record uses `expenses.record`, decisions enforce approval/owner limits and payout validation, schedules use `expenses.approve`, and payroll operations use `payroll.manage` (`app/finance/expenses.py:43–44,71–126,178–211`, `app/finance/schedules.py:60–76,166–203`, `app/finance/payroll.py:61,121,134,161,185,213`). Transfer proof review uses `sales.totals` (`app/sales/attachments.py:103–113`). |
| User administration and settings (3153, 3164–3178, 3295–3296, 3331, 3352) | User administration requires `users.manage` or constrained `users.permissions`. Owner credential protection and self-service current-password checks are distinct (`app/core/people.py:166–219,235–317`, API `818–828,1521–1554`). Org settings use `settings.manage` in handlers or services (1417–1439,1586–1615,2645–2654,3125–3131, `app/core/ui_settings.py:63`, `app/sales/rounding.py:38`, `app/safety/dispensing.py:47`, `app/sales/channels.py:41`, `app/printing/settings.py:138`). |
| Backup and restore (3156, 3195–3196, 3340–3342) | Backup admin and device settings restore require `settings.manage` (1570–1583,3070–3095, `app/core/autobackup.py:171–172,624–625`, `app/core/device_settings.py:226–227,383–384`). First-run data restore requires an empty installation, loopback, launcher and custom header (670–686). Full repair CLI uses OS access and operator confirmation (`manage.py:217–230`). See SEC-07/13/15. |
| Update (3151–3152) | Settings permission covers checking, classic, preparing, scheduling and cancelling (600–667). Signing applies to smart updates. Failed first start uses a journal to restore the previous code and, where applicable, DB (`release_bootstrap.py:486–507`). CLI code-only rollback refuses an active journaled DB change (`manage.py:638–648`). See SEC-05. |

## Strict expected-failure tests for P1 findings

This PR adds one Markdown file. The executable block below is kept here and was run from a temporary file. Run it from a full checkout with the shipped requirements and pytest, using `PYTHONPATH=. python -m pytest -q -rx <temporary-test-file>`. The expected outcome on the reviewed revision is nine XFAILs. Tests use safe policy assertions and generated fixtures. Source-level checks are sentinels for the proposed guard locations. Replace those with behavioral security assertions when implementing the corresponding fixes. Strict markers detect a change in the expected result and mark only assertion failures as expected.

```python
import ast
from pathlib import Path
import re
import sqlite3
import subprocess
import zipfile

import pytest

from app.core import backup
from app.db import Database


ROOT = Path.cwd()


def source(relative, function):
    text = (ROOT / relative).read_text(encoding="utf-8")
    definition = next(
        node for node in ast.walk(ast.parse(text))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function
    )
    return ast.get_source_segment(text, definition)


def expected_failure(finding):
    return pytest.mark.xfail(reason=finding, raises=AssertionError, strict=True)


@expected_failure("SEC-01")
def test_failed_password_policy_has_no_account_wide_lockout():
    login = source("app/core/accounts.py", "login")
    assert 'code="locked"' not in login


@expected_failure("SEC-02")
def test_catalog_price_writes_have_price_authority_guard():
    create = source("app/catalog/service.py", "create_item")
    approve = source("app/purchasing/service.py", "approve")
    assert all('require("items.price")' in path for path in (create, approve))


@expected_failure("SEC-03")
def test_manual_cash_payout_uses_checked_drawer_outflow():
    movement = source("app/sales/cash.py", "movement")
    assert "_drawer_out(" in movement or "_require_drawer_funds(" in movement


@expected_failure("SEC-04")
def test_partial_return_keeps_one_intent_across_uncertain_submission():
    browser = (ROOT / "app/web/app.js").read_text(encoding="utf-8")
    tracked_routes = browser.split("const ONCE_PATHS = [", 1)[1].split(
        "function onceKey", 1
    )[0]
    assert any(
        "sales" in route and "return" in route and "estock" not in route
        for route in re.findall(r"\/\^.*?\$\/", tracked_routes)
    )


@expected_failure("SEC-05")
def test_update_invokes_only_publisher_authenticated_candidate_paths():
    update = source("manage.py", "cmd_update")
    assert "updater.apply_zip(" not in update


@expected_failure("SEC-06")
def test_lan_ui_advertises_tls_only():
    network = source("app/api/app.py", "system_network")
    assert "https://" in network and "http://" not in network


@expected_failure("SEC-07")
def test_new_backup_has_no_unencrypted_zip_members(tmp_path):
    db = Database(tmp_path / "fixture.sqlite")
    try:
        db.migrate()
        output = backup.create(db, tmp_path / "uploads", tmp_path / "backups")
    finally:
        db.close()
    if zipfile.is_zipfile(output):
        with zipfile.ZipFile(output) as archive:
            assert all(member.flag_bits & 1 for member in archive.infolist())


@expected_failure("SEC-08")
def test_plain_sqlite_cannot_read_application_schema_without_key(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = Database(path)
    try:
        db.migrate()
    finally:
        db.close()
    try:
        with sqlite3.connect(path) as reader:
            row = reader.execute(
                "SELECT name FROM sqlite_master WHERE name = ?",
                ("organizations",),
            ).fetchone()
    except sqlite3.DatabaseError:
        return
    assert row is None


@expected_failure("SEC-09")
def test_published_heads_have_no_raw_spreadsheet_exports():
    history = subprocess.run(
        ["git", "log", "--remotes=origin", "--format=", "--name-only"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    disallowed = re.compile(r"^work/.+/raw/.+\.xlsx$", re.IGNORECASE)
    assert not any(disallowed.fullmatch(path) for path in history.splitlines())
```

The backup test checks for readable ZIP members. A companion test in the implementation must verify authenticated provenance and an actual recovery with the proper key. The live database test checks the file copy threat under ordinary SQLite. Deployment must also verify Windows identity separation. The history test requires fetched public heads and checks recorded paths only. Content classification and non-head refs remain incident-response work.

## Development suggestions

Priority is ordered by when the work should compete for time. **Now** addresses distribution or operating risk. **Next** improves routine pharmacy work after release blockers. **Later** needs a commercial, regulatory or product decision. For relative effort, **S** is a focused change, **M** spans several flows, and **L** needs cross-component work or external integration. These are proposals and include improvements to features already present.

| What to develop | Why it helps a pharmacy | Effort | Priority |
| --- | --- | :---: | --- |
| Split the private source repository from the public authenticated release channel. Publish a signed installer and a signed update manifest. Exercise key rotation and failed-update recovery in a test installation. | Protects source and operating information while keeping updates available for pharmacies without their own repository credentials. | L | Now |
| Ship a Windows service with least-privilege staff accounts. Include encrypted local data and portable encrypted backups, an independent audit checkpoint, and a recovery-key handover. | An employee can run the till without having direct control of the pharmacy database. The owner can recover after a stolen or broken PC. | L | Now |
| Use an offline-first device-bound licence with a signed entitlement, a repair/replacement transfer flow and a clearly defined renewal grace period. Preserve access to existing records and export during licence recovery. | Raises the cost of licence sharing and keeps a paying pharmacy able to operate during connectivity trouble or hardware replacement. | L | Now, before commercial distribution |
| Add role-based account controls with an owner recovery code and recent-password checks for sensitive changes. Make shared terminal lock and user switching quick. | Keeps the till moving while individually attributing privileged changes and preventing third-party password failures from disabling staff. | M | Now |
| Add a settlement and cash-close confirmation screen that shows counted notes, destination, amount, receiver and an operation receipt. Show uncertain submissions in a recoverable list. | Reduces duplicate cash movements and gives incoming staff a clear handover record after a disconnect. | M | Now |
| Finish the guided E-Stock move as two stages with a resumable read-only rehearsal, discrepancy sign-off and labelled historical records. Provide a reversal plan before the first real posting. | A new pharmacy can switch systems while reviewing opening cash, balances and stock without posting the historical ledger twice. | L | Next |
| Make sales and receiving keyboard-first. Keep scan, quantity, pack/unit and line total in one visible row, with sticky totals, remembered column order and a clear focus indicator in Arabic and English. | Speeds common transactions on small displays and helps staff spot unit and price mistakes before confirmation. | M | Next |
| Set a Windows performance budget on a low-spec till, starting with p95 under 300 ms for routine scan/add operations and tracking checkout and report latency separately. Benchmark with synthetic multi-year ledgers while backups run. | Shows whether staff experience slowdown at busy times. Makes it possible to prioritize expensive queries, virtualization and background work with measurements. | M | Next |
| Add an owner and shift-lead dashboard organized as a work queue for expiring stock, unmatched transfers, unposted purchases, stock discrepancies, backup attention and supplier credits. Give every item a resolution action. | A pharmacist can start a shift from actionable exceptions. PioneerRx advertises a dashboard to-do list and reorder alerts as precedents. [PioneerRx pharmacy software](https://www.pioneerrx.com/pharmacy-software) | M | Next |
| Extend smart ordering into receiving and supplier-returns follow-up. Match purchase orders to accepted batches and invoice amounts, show shortages and supplier return windows, and reconcile pending credits. | Reduces duplicate orders and helps recover money before returns expire. dawaTech advertises supplier-connected replenishment and stock posting, and PioneerRx advertises supplier-based return recommendations. [dawaTech ERP](https://dawatech.com/en/erp) [PioneerRx Recommended Return](https://www.pioneerrx.com/blog/featured-feature-recommended-return) | L | Next |
| Add a scanning mode for shelf-by-shelf blind cycle counts. Support pause/resume and supervisor reconciliation before stock is posted. Pair handheld devices over a secured connection. | Staff can count stock without closing the pharmacy and can review unexplained differences before they change inventory. PioneerRx advertises barcode counting from a handheld device. [PioneerRx Mobile Inventory App](https://www.pioneerrx.com/blog/featured-feature-mobile-inventory-app) | M | Next |
| Build a shared-data traceability pilot for product codes, serials, batches, expiry and returns. Extend the existing GS1 parser. Verify current requirements and integration access with the Egyptian Drug Authority before committing to dates. | Speeds batch recalls and makes affected packs easier to identify at receiving and checkout. [Egyptian Drug Authority EPTTS guide](https://www.edaegypt.gov.eg/media/pz5mij1g/eptts-user-guide-notice-to-applicant.pdf) | L | Next for discovery |
| Explore Egyptian tax receipt and insurance workflows with a local accountant and pharmacy partner. Start with reviewable exports, then add queued submission and reconciliation if feasible. | Avoids re-keying cash receipts and claims. Local competitors advertise e-invoicing or insurance integration. [PSR Pharmacy](https://psr.com.eg/en/the-pharmacy-solution/) [Tawazun Soft pharmacy management](https://www.tawazunsoft.com/en/products/pharmacy-software/) | L | Later |
| Build an optional owner read-only view with branch and shift exceptions and minimal exported fields. Default to aggregate data and use narrowly scoped device enrollment. | Supports management away from the till while reducing the number of devices that receive customer and transaction details. dawaTech advertises branch-level reporting. [dawaTech ERP](https://dawatech.com/en/erp) | L | Later |

Competitor references are published feature descriptions. Their speed, usability, pricing and deployment requirements were outside this comparison. A useful comparison on the pharmacy's target PC is time and error count for the same workflows across products, including receiving a delivery, locating a recalled batch, completing a partial return, reconciling a shift, and recovering after a restart.

## Verification record

- Extracting the Python block into a temporary file produced **9 strict XFAILs**, one per P1, with no unexpected pass.
- The unchanged Linux baseline ran **2,969 tests** using `python -m unittest discover -s tests -t .`. It reported 2,873 ordinary passes, 67 skips, 2 expected failures and 27 failures. All 27 were in the licensing CLI and release-signature test modules. The sandbox's synthetic `/tmp/.git` marker caused the key-file safety checks to refuse the tests' temporary paths. All 59 tests in those two modules passed on rerun with a temporary directory outside those markers. A separate 129-test focused run also passed using that setup.
- The repository guard and Markdown whitespace check passed for this file. The PyPI advisory scan found no known advisories for the pinned Python packages. Browser/device verification on Windows, incident containment and real data recovery remain outside this review.
