export type Kind = 'movie' | 'episode' | 'video' | 'photo' | 'audio' | 'document';
export interface AuthState { role: 'viewer' | 'editor' | 'admin'; otp_unlocked: boolean; expires?: number; otp_expires?: number }
export interface Config { library_name: string; autoplay_next: boolean; auth: AuthState; capabilities: { download: boolean; transcode: boolean; probe: boolean } }
export interface Metadata {
  status?: 'matched' | 'missing_key' | 'error' | 'not_found'; message?: string; plot?: string;
  imdb_id?: string; imdb_rating?: string; genre?: string; rated?: string; runtime?: string;
  actors?: string; director?: string; poster?: string; title?: string; year?: string;
}
export interface Item {
  id: string; title: string; kind: Kind; year?: number | null; folder_id?: string | null;
  season?: number | null; episode?: number | null; series?: string | null; status: string;
  metadata: Metadata; technical: { duration?: number; width?: number; height?: number; video_codec?: string;
    audio_codec?: string; extension?: string; size?: number; file_name?: string; browser_ready?: boolean; artwork?: boolean; streaming_ready?: boolean };
  created: number; updated: number; stream_url: string; download_url?: string; artwork_url?: string; is_remote: boolean; is_hls: boolean;
  subtitles?: { id: string; label: string; language: string }[]; episodes?: Item[]; next_episode?: Item | null;
}
export interface Folder { id: string; name: string; parent_id: string | null; source: boolean; item_count?: number; folder_count?: number; created: number }
export interface FolderPage { folders: Folder[]; breadcrumbs: Folder[] }
export interface LibraryPage { items: Item[]; count: number; total: number; offset: number; has_more: boolean; genres: string[] }
export interface Job { id: string; kind: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; progress: number;
  message: string; payload: { item_id?: string; folder_id?: string; title?: string }; result: { item_id?: string }; created: number; updated: number }
export interface Invitation { id: string; label: string; role: 'editor' | 'admin'; expires: number; revoked: boolean | number }
export interface Settings { library_name: string; autoplay_next: boolean; adaptive_streaming: boolean; omdb_configured: boolean;
  sources: { id: string; name: string; root_path: string }[]; invitations: Invitation[] }
export interface Progress { position: number; duration: number; updated: number; completed: boolean }
export type ProgressMap = Record<string, Progress>;
export type Notify = (message: string, error?: boolean) => void;
