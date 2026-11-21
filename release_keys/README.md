# Update signing keys (public)

Each `*.pub` file is one Ed25519 public key (base64 of the raw 32 bytes). The in-app updater accepts a
smart-update bundle only when its `manifest.sig` matches a key in the **running** release
(docs/SMART_UPDATE.md §2). No key here = the smart path is off and the button keeps the code-only path.
Add a key: `scripts/release/make_signing_key.py`; the private half goes only into the repository secret
`UPDATE_SIGNING_KEY`. Rotate: add the new key in a release signed by the old one, then switch the secret.

**Now (owner 21/11):** one key only, `update-2.pub` (it signs). update-1 was retired (its private half was not
found). **No spare yet:** before any merge to `main` or the next installer, the owner runs `new-signing-key.bat`
and an agent commits its `update-3.pub` here, so the PCs always trust a second key (docs/SMART_UPDATE.md).
