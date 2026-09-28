// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-G-425a59663352447455546d73-e86dcb76147fab46d4d4cc1f

// Based on the type definitions for extract-zip 1.6
// Definitions by: Mizunashi Mana <https://github.com/mizunashi-mana>
// Definitions: https://github.com/DefinitelyTyped/DefinitelyTyped/blob/e69b58e/types/extract-zip/index.d.ts

import { Entry, ZipFile } from 'yauzl';

declare namespace extract {
    interface Options {
        dir: string;
        defaultDirMode?: number;
        defaultFileMode?: number;
        onEntry?: (entry: Entry, zipfile: ZipFile) => void;
    }
}

declare function extract(
  zipPath: string,
  opts: extract.Options,
): Promise<void>;

export = extract;
