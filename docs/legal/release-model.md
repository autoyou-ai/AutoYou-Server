---
title: Distribution model
description: Server source, local builds, and separately distributed clients.
---

# Distribution model

AutoYou Server can be run from source using the root bootstrap scripts or
compiled using the Windows, macOS, and WSL/Linux build tools in this repository.
Dependency selection and platform support vary by profile.

Separately distributed AutoYou client applications connect to a running server.
Installation of a client does not by itself install the server's dependency
stack or supply a hosted server entitlement.

Source and unofficial compiled distributions are governed by [LICENSE](../../LICENSE).
Free sharing is permitted subject to its conditions. Official signing,
publication, service access, and Enterprise use have separate authorization
requirements; local compilation does not confer those permissions.

Any distributed artifact must carry the licenses and notices for its actual
contents. Build profiles and generated source inventories do not establish
what a particular compiled artifact contains. Before release, inspect the
resolved dependency inventory, advisories, provenance, notices, and applicable
source or relinking obligations.

Release operators retain their approval records outside this repository.
A passing metadata check is not legal approval, platform approval, or a
guarantee of security.
