// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-F-646472657373202d20334163-cca55c8d18a0d2f9d0479054

#!/usr/bin/env node

/* eslint-disable no-process-exit */

var extract = require('./')

var args = process.argv.slice(2)
var source = args[0]
var dest = args[1] || process.cwd()
if (!source) {
  console.error('Usage: extract-zip foo.zip <targetDirectory>')
  process.exit(1)
}

extract(source, { dir: dest })
  .catch(function (err) {
    console.error('error!', err)
    process.exit(1)
  })
