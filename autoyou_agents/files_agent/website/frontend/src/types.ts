// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-E-pay-7ddc9b38c45a9c7d9e46c5f0

export type FileKind = "file" | "directory" | "other";
export type PreviewKind = "folder" | "text" | "image" | "binary";

export type FileEntry = {
  name: string;
  path: string;
  kind: FileKind;
  parent?: string | null;
  extension?: string;
  hidden?: boolean;
  size_bytes?: number | null;
  modified?: string | null;
  preview_kind?: PreviewKind;
};

export type LocationEntry = {
  label: string;
  path: string;
  kind: "workspace" | "home" | "folder" | "drive";
};

export type LocationsPayload = {
  success: boolean;
  default_path: string;
  locations: LocationEntry[];
  error?: string;
};

export type ListPayload = FileEntry & {
  success: boolean;
  entries: FileEntry[];
  count: number;
  truncated: boolean;
  default_path: string;
  error?: string;
};

export type PreviewPayload = FileEntry & {
  success: boolean;
  children_count?: number;
  media_type?: string;
  data_uri?: string;
  text?: string;
  truncated?: boolean;
  error?: string;
};
