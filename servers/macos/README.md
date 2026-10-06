# AutoYou Server for macOS

These tools compile the server backend and native macOS server host. Client
applications distributed separately require a running server.

## Local build

Use macOS with Xcode command-line tools and Swift, Python, Node.js, and npm.
Dependency wheels and native libraries must support the machine architecture.

From the repository root:

```bash
./servers/macos/build-all.sh --no-sign --dev
```

This is an unsigned development build and does not require official release
authorization. Review the license and notices when prompted. Add
`--accept-terms` only after reviewing them for an unattended build.

The default profile is `binary-default`. An expanded local build uses:

```bash
./servers/macos/build-all.sh --no-sign --dev --release-profile connector-full --requirements full
```

Build scripts create or reuse an environment, install or reconcile dependencies,
and may download native runtimes. Read [dependency guidance](../../requirements/README.md).
Do not point an explicit Python override at an environment you cannot safely
change.

The [Intel wrapper](intel/README.md) uses the shared pipeline. The current
Intel voice/full declarations conflict with the shared NumPy constraint;
that profile needs a separately resolved compatible environment before it can
be represented as supported. Do not disable all lock constraints as a substitute
for resolving and auditing that conflict.

## Output and sharing

Typical outputs are `servers/macos/build/AutoYou.app` and
`servers/macos/build/AutoYou.dmg`; the exact outputs depend on build options.
Version metadata comes from the repository `VERSION` file, currently `81.0.0`.

Review `servers/macos/build/AutoYou.app/Contents/Resources/Legal/LICENSE`,
`THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and `sbom.cdx.json`.
Use constitutes agreement to the applicable license terms, including the
warranty disclaimer and liability limits to the extent permitted by law.
Separate services have separate terms.

Identify shared builds as unofficial and retain all required notices and source
obligations under [LICENSE](../../LICENSE) and the bundled component licenses.
Unsigned development output is not an official signed or notarized release.

## Official release tools

Signing, notarization, and official packaging have separate authorization and
release gates. They require the relevant operator's own credentials and
approvals. This repository does not supply signing identities or store access.
The strict release gate takes the operator's external review file through
`AUTOYOU_RELEASE_CHECKLIST`; keep that private input outside this repository.
