# Content and recording storage

My Content and Organizer use the same catalog. The catalog indexes each agent's
records; it does not replace their databases. New managed files use the output
folder selected in Admin's content storage settings. Changing this folder does
not move or delete existing originals. Existing destinations remain read roots.

| Producer | New file destination under the shared output folder | Catalog source |
| --- | --- | --- |
| Music imports | `music_agent/` | `music_agent` |
| Media uploads and downloads | `media_agent/` | `media_agent` |
| Pages UI and chat/tool attachments | `page_agent/` | `page_agent` |
| Notes attachments | `notes_agent/media/` | `notes_agent` |
| Generated images, audio and video | `media_generation_agent/` | `media_generation_agent` |
| New voice training workspace | `voice_training_agent/` | `voice_training_agent` |
| Inbound/computer video recording | `media_agent/recordings/video/` | `media_agent` |
| Audio-only call recording | `media_agent/recordings/video/audio-only/` | `music_agent` |
| Safety audio recording | `music_agent/recordings/safety/` | `music_agent` |

Explicit video/safety recording directories and the configured voice workspace
remain authoritative. Existing voice datasets retain their transcripts, consent,
recordings and model references together. Voice recordings, transcripts and
completed model artifacts are indexed even when there is no native Voice
Training app on a client. No capture or consent setting is enabled by indexing.

Location samples remain in the Location agent's private `locations.sqlite3`
database. My Content indexes a day entry and an authenticated link to Locations;
it does not copy coordinates into public content metadata. Native iOS and Android
continue sending location and recording input to the current server endpoints.

## Compatibility

`uploads/` is previous Pages storage, retained for existing database references
and Music's optional Pages-audio source. New Pages UI and tool uploads share
`page_agent/`. Deleting or tagging an existing Page still uses its stored blob
path. Do not delete `uploads/` just because new uploads use another folder.

`audio_agent` and `tv_agent` are previous names for Music and Media. Old chat/tool
identifiers resolve to the current runtime names, including in the Ollama
adapter and REST steering. New output directories and catalog publications use
current names. Readers retain previous output roots. The Media store also finds
the released TV catalog in the retired package's directory without moving it.
If multiple Media catalogs coexist, the preferred existing catalog is selected;
their databases are not implicitly merged.

Recorders publish completed files after finalization. WAV and MP4 files remain
under `.partial` names until closed; WAV names include a recording instance ID
to prevent rapid reconnects from overwriting an earlier batch. Video-frame mode
indexes its manifest. Content Sync recovers known recording formats from current,
configured and previous recording directories. It never treats `.partial`
files as completed recordings. Existing finalized recordings are kept in place.

## Attachment and repository boundaries

Untrusted client, tool and provider attachment paths must identify app-owned
media. The entire OS temporary folder and the entire test root are not trusted
attachment roots. Temporary uploads and synthesized voice files use the shared
`autoyou_media` helper. Tests place it under `AUTOYOU_TEST_ROOT`.
Broad custom conversation archive directories authorize only the exact files
archived and indexed by the backend, not unrelated files beside them.

Runtime media, voice datasets, location stores, content catalogs, retired agent
data directories and generated recordings are ignored by Git. Source files,
synthetic tests, frontend assets and the storage rules remain tracked. Custom
output folders outside a checkout are not part of either repository.
