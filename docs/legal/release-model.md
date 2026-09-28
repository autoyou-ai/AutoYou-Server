---
title: Release Model
description: How AutoYou separates source installs from signed commercial binaries.
---

# Release Model

AutoYou uses a dual-track release model.

## Source and Bootstrap

The source/bootstrap track supports users and developers who install AutoYou from source. It can include optional local model, messaging, voice, browser, and connector dependencies depending on the setup the owner chooses.

## Signed Binaries

Official signed Windows and macOS binaries are packaged as local-first desktop releases. They include legal attribution material such as NOTICE files, license information, software bill of materials where applicable, and release-profile metadata.

Signed binaries may intentionally ship with a narrower default feature set than source installs while platform, provider, and licensing approvals are reviewed.

## Connector Builds

Connector-capable builds are separate release artifacts. They should only be distributed when the relevant provider, platform, and licensing approvals are current for that release.

## Verification

Before public release packaging, AutoYou runs a release legal gate to check attribution coverage, third-party licensing metadata, and release-profile expectations.
