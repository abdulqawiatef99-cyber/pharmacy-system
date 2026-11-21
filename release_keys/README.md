# Update signing keys (public)

Each `*.pub` file is one Ed25519 public key (base64 of the raw 32 bytes). The in-app updater accepts a
smart-update bundle only when its `manifest.sig` matches a key in the **running** release
(docs/SMART_UPDATE.md §2). No key here = the smart path is off and the button keeps the code-only path.
Add a key: `scripts/release/make_signing_key.py`; the private half goes only into the repository secret
`UPDATE_SIGNING_KEY`. Rotate: add the new key in a release signed by the old one, then switch the secret.

**Now (owner 21/11):** two keys. `update-2.pub` signs (the secret `UPDATE_SIGNING_KEY`). `update-3.pub` is the
spare: made 21/11 14:41 by the owner with `new-signing-key.bat` on the cashier PC, private half only on the flash
drive (`F:\pharmacy-update-keys-3`), never in the secret unless update-2 is lost. update-1 was retired (its private
half was not found). The PCs must always trust two keys (docs/SMART_UPDATE.md).
