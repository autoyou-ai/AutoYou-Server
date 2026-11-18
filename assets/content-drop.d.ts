export function relativePath(file: File): string;
export function collectDroppedFiles(transfer: DataTransfer, onProgress?: (count: number) => void): Promise<File[]>;
