"""Persistent imports, scanning, metadata and optional browser renditions."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException

from .media import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS, fetch_response, http_session, parse_name, permitted_path, public_url, subtitles, subtitle_content
from . import playback
from .store import Store, uid


def executable(name: str) -> str | None:
    env_key = "AUTOYOU_TV_" + name.replace("-", "_").upper()
    configured = os.getenv(env_key)
    if configured and Path(configured).is_file():
        return configured

    found = shutil.which(name)
    if found:
        return found

    import sys
    exe_name = name + (".exe" if sys.platform == "win32" else "")
    candidates: list[Path] = []

    res_root = os.getenv("AUTOYOU_PACKAGED_RESOURCES_ROOT")
    if res_root:
        candidates.extend([
            Path(res_root) / "runtime" / "ffmpeg" / "bin" / exe_name,
            Path(res_root) / "runtime" / "ffmpeg" / exe_name,
            Path(res_root) / "bin" / exe_name,
        ])

    py_dir = Path(sys.executable).resolve().parent
    candidates.extend([
        py_dir / "runtime" / "ffmpeg" / "bin" / exe_name,
        py_dir / "runtime" / "ffmpeg" / exe_name,
        py_dir.parent / "runtime" / "ffmpeg" / "bin" / exe_name,
        py_dir.parent / "runtime" / "ffmpeg" / exe_name,
    ])

    try:
        repo_dir = Path(__file__).resolve().parents[4]
        candidates.extend([
            repo_dir / "runtime" / "ffmpeg" / "bin" / exe_name,
            repo_dir / "runtime" / "ffmpeg" / exe_name,
        ])
    except Exception:
        pass

    if sys.platform == "win32":
        local_app = os.getenv("LOCALAPPDATA", "")
        if local_app:
            candidates.append(Path(local_app) / "Microsoft" / "WinGet" / "Links" / exe_name)
            winget_pkgs = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
            if winget_pkgs.is_dir():
                try:
                    for sub in winget_pkgs.glob(f"*{name}*/**/{exe_name}"):
                        if sub.is_file():
                            candidates.append(sub)
                    for sub in winget_pkgs.glob(f"*FFmpeg*/**/{exe_name}"):
                        if sub.is_file():
                            candidates.append(sub)
                except Exception:
                    pass
        candidates.extend([
            Path("C:/ffmpeg/bin") / exe_name,
            Path("C:/Program Files/ffmpeg/bin") / exe_name,
            Path.home() / "scoop" / "shims" / exe_name,
            Path("C:/ProgramData/chocolatey/bin") / exe_name,
            Path.home() / "AppData" / "Local" / "AutoYou" / "tools" / "ffmpeg" / "bin" / exe_name,
            Path.home() / "AppData" / "Local" / "AutoYou" / "tools" / "ffmpeg" / exe_name,
        ])
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
            found_reg = shutil.which(name, path=user_path)
            if found_reg and Path(found_reg).is_file():
                os.environ[env_key] = found_reg
                return found_reg
        except Exception:
            pass
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/opt/homebrew/bin") / name,
            Path("/usr/local/bin") / name,
            Path("/opt/local/bin") / name,
            Path.home() / ".autoyou" / "tools" / "ffmpeg" / "bin" / name,
            Path.home() / ".autoyou" / "tools" / "ffmpeg" / name,
        ])
    else:
        candidates.extend([
            Path("/usr/bin") / name,
            Path("/usr/local/bin") / name,
            Path("/snap/bin") / name,
            Path.home() / ".autoyou" / "tools" / "ffmpeg" / "bin" / name,
            Path.home() / ".autoyou" / "tools" / "ffmpeg" / name,
        ])

    for candidate in candidates:
        try:
            if candidate.is_file():
                resolved = str(candidate.resolve())
                os.environ[env_key] = resolved
                return resolved
        except (OSError, PermissionError):
            continue

    return None


def probe(path: Path) -> dict:
    info = {"size": path.stat().st_size, "extension": path.suffix.lower(), "file_name": path.name}
    ffprobe = executable("ffprobe")
    if not ffprobe:
        return info
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                                capture_output=True, timeout=15, check=True)
        media = json.loads(result.stdout)
        info["duration"] = float(media.get("format", {}).get("duration", 0))
        video = next((s for s in media.get("streams", []) if s.get("codec_type") == "video"), {})
        audio = next((s for s in media.get("streams", []) if s.get("codec_type") == "audio"), {})
        info.update(width=video.get("width"), height=video.get("height"), video_codec=video.get("codec_name"),
                    audio_codec=audio.get("codec_name"))
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        info["probe_error"] = True
    return info


def make_artwork(store: Store, item: dict) -> bool:
    ffmpeg = executable("ffmpeg")
    if not ffmpeg or not item.get("file_path"):
        return False
    destination = store.artwork_dir / (item["id"] + ".jpg")
    if destination.is_file():
        return True
    try:
        source = permitted_path(store, item["file_path"])
        timestamp = min(float(item["technical"].get("duration", 0)) * .1, 60)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", str(timestamp), "-i", str(source),
                        "-frames:v", "1", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
                        "-q:v", "3", str(destination)], capture_output=True, timeout=20, check=True)
        return destination.is_file()
    except (OSError, subprocess.SubprocessError, HTTPException, ValueError):
        destination.unlink(missing_ok=True)
        return False


class Metadata:
    def __init__(self, store: Store):
        self.store = store

    async def match(self, item: dict, imdb_id: str = "") -> dict:
        key = self.store.key()
        if not key:
            return {"status": "missing_key", "message": "Configure the OMDb key in Settings for automatic IMDb details"}
        params = {"plot": "full", "r": "json"}
        if imdb_id:
            if not re.fullmatch(r"tt\d{7,10}", imdb_id):
                raise ValueError("Use an IMDb ID such as tt1234567")
            params["i"] = imdb_id
        else:
            params["t"] = item.get("series") or item["title"]
            if item.get("year"):
                params["y"] = str(item["year"])
            if item["kind"] == "episode" and item.get("season") is not None and item.get("episode") is not None:
                params.update(Season=str(item["season"]), Episode=str(item["episode"]))
        cache_key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            cached = db.execute("SELECT value FROM metadata_cache WHERE key=? AND expires>?", (cache_key, time.time())).fetchone()
        if cached:
            return json.loads(cached[0])
        async with http_session() as session:
            upstream = await fetch_response(session, "https://www.omdbapi.com/?" + urlencode({**params, "apikey": key}),
                                            headers={"Accept": "application/json", "Accept-Encoding": "identity"})
            try:
                body = await upstream.content.read(2 * 1024 * 1024 + 1)
                if upstream.status != 200 or len(body) > 2 * 1024 * 1024:
                    raise ValueError("The metadata provider could not complete this lookup")
                data = json.loads(body)
            finally:
                upstream.close()
        if data.get("Response") != "True":
            # Do not expose provider text that could contain a credential.
            if "key" in str(data.get("Error", "")).lower() or "limit" in str(data.get("Error", "")).lower():
                return {"status": "error", "message": "Verify the OMDb key and provider quota in Settings, then retry."}
            return {"status": "not_found", "message": "No IMDb match. Correct the title/year or use an IMDb ID."}
        def value(name):
            v = data.get(name, "")
            return "" if v == "N/A" else v
        poster = value("Poster")
        if not isinstance(poster, str) or urlsplit(poster).scheme not in {"http", "https"}:
            poster = ""
        if poster.startswith("http:"):
            poster = "https:" + poster[5:]
        result = {"status": "matched", "imdb_id": value("imdbID"), "imdb_rating": value("imdbRating"),
                  "plot": value("Plot"), "genre": value("Genre"), "rated": value("Rated"), "runtime": value("Runtime"),
                  "director": value("Director"), "actors": value("Actors"), "poster": poster,
                  "title": value("Title"), "year": value("Year"), "source": "OMDb", "matched_at": time.time()}
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata_cache VALUES (?,?,?)", (cache_key, json.dumps(result), time.time() + 30 * 86400))
        return result


class Jobs:
    def __init__(self, store: Store):
        self.store = store
        self.metadata = Metadata(store)
        self.queue = asyncio.Queue()
        self.workers: list[asyncio.Task] = []
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.rendition_slot = asyncio.Semaphore(1)

    async def start(self):
        if self.workers:
            return
        with self.store.connect() as db:
            # Scan and metadata operations are idempotent. Interrupted external
            # downloads/renditions need a deliberate retry, not duplicate work.
            db.execute("UPDATE jobs SET status='queued' WHERE status='running' AND kind IN ('scan','metadata','prepare')")
            db.execute("UPDATE jobs SET status='failed',message='Interrupted by server restart. Retry this import.' WHERE status='running'")
            ids = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created").fetchall()
        for row in ids:
            self.queue.put_nowait(row[0])
        self.workers = [asyncio.create_task(self.worker()) for _ in range(2)]

    async def stop(self):
        for process in list(self.processes.values()):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def enqueue(self, kind: str, payload: dict) -> dict:
        await self.start()
        job = self.store.add_job(kind, payload)
        self.queue.put_nowait(job["id"])
        return job

    async def worker(self):
        while True:
            job_id = await self.queue.get()
            try:
                job = self.store.job(job_id)
                if job["status"] != "queued":
                    continue
                self.store.update_job(job_id, status="running", message="Starting " + job["kind"])
                handler = {"scan": self.scan, "metadata": self.enrich, "download": self.download,
                           "transcode": self.transcode, "prepare": self.prepare, "streaming": self.streaming}[job["kind"]]
                result = await handler(job)
                if self.store.job(job_id)["status"] != "cancelled":
                    self.store.update_job(job_id, status="completed", progress=1, message="Complete", result=result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                # Avoid persisting upstream URLs, cookies or tool logs containing credentials.
                message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else "Import failed. Check source availability and retry."
                if not self.cancelled(job_id):
                    self.store.update_job(job_id, status="failed", message=message)
            finally:
                self.queue.task_done()

    def cancelled(self, job_id: str) -> bool:
        return self.store.job(job_id)["status"] == "cancelled"

    async def cancel(self, job_id: str):
        job = self.store.job(job_id)
        if job["status"] not in {"queued", "running"}:
            raise ValueError("Only queued or running jobs can be cancelled")
        self.store.update_job(job_id, status="cancelled", message="Cancelled")
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()

    async def enrich_item(self, item_id: str, imdb_id: str = ""):
        item = self.store.item(item_id)
        try:
            metadata = await self.metadata.match(item, imdb_id)
        except Exception:
            metadata = {"status": "error", "message": "IMDb lookup unavailable. Retry from Studio."}
        self.store.update_item(item_id, {"metadata": metadata})
        return metadata

    async def enrich(self, job: dict) -> dict:
        item_id = job["payload"]["item_id"]
        metadata = await self.enrich_item(item_id, job["payload"].get("imdb_id", ""))
        if metadata.get("status") in {"error", "missing_key"}:
            raise ValueError(metadata["message"])
        return {"item_id": item_id, "metadata_status": metadata["status"]}

    async def prepare(self, job: dict) -> dict:
        item = self.store.item(job["payload"]["item_id"])
        source = permitted_path(self.store, item["file_path"])
        if item["kind"] == "photo":
            self.store.update_item(item["id"], {"technical": {"extension": source.suffix.lower().lstrip("."), "size": source.stat().st_size, "file_name": source.name}})
            return {"item_id": item["id"]}
        technical = await asyncio.to_thread(probe, source)
        item = self.store.update_item(item["id"], {"technical": {**item["technical"], **technical}})
        if await asyncio.to_thread(make_artwork, self.store, item):
            self.store.update_item(item["id"], {"technical": {**item["technical"], "artwork": True}})
        await self.auto_streaming(item)
        return await self.enrich(job)

    async def scan(self, job: dict) -> dict:
        folder = self.store.folder(job["payload"]["folder_id"])
        if not folder["root_path"]:
            raise ValueError("This collection has no source folder to scan")
        root = Path(folder["root_path"]).resolve()
        scan_root = (root / folder["relative_path"]).resolve()
        if not scan_root.is_relative_to(root) or not scan_root.is_dir():
            raise ValueError("Source folder is not available")
        seen, matched, added, skipped = set(), 0, 0, 0
        folder_ids = {str(scan_root): folder["id"]}
        # Iterating os.walk one directory at a time avoids keeping a huge file
        # inventory in memory and lets cancellation happen between directories.
        walker = os.walk(scan_root, followlinks=False)
        sentinel = object()
        while True:
            row = await asyncio.to_thread(next, walker, sentinel)
            if row is sentinel or self.cancelled(job["id"]):
                break
            directory, names, filenames = row
            directory = Path(directory)
            names[:] = sorted(n for n in names if not n.startswith(".") and not (directory / n).is_symlink())
            parent_id = folder_ids[str(directory)]
            for name in names:
                path = directory / name
                subfolder = self.store.add_folder(name, parent_id, root_path=str(root), relative_path=path.relative_to(root).as_posix())
                folder_ids[str(path)] = subfolder["id"]
            for filename in sorted(filenames):
                if self.cancelled(job["id"]):
                    break
                path = directory / filename
                if path.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                try:
                    path = permitted_path(self.store, str(path))
                    technical = await asyncio.to_thread(probe, path)
                    item, is_new = self.store.add_item(source_key="file:" + str(path), folder_id=parent_id,
                                                      file_path=str(path), technical=technical, **({**parse_name(filename), "kind": "photo"} if path.suffix.lower() in IMAGE_EXTENSIONS else parse_name(filename)))
                    seen.add(item["id"])
                    await self.auto_streaming(item)
                    added += int(is_new)
                    if not item["technical"].get("artwork") and await asyncio.to_thread(make_artwork, self.store, item):
                        item = self.store.update_item(item["id"], {"technical": {**item["technical"], "artwork": True}})
                    if item["kind"] != "photo" and (is_new or item["metadata"].get("status") in {None, "missing_key", "error"}):
                        await self.enrich_item(item["id"])
                    matched += 1
                    if not self.cancelled(job["id"]):
                        self.store.update_job(job["id"], status="running", message=f"Indexed {matched} videos; {added} new")
                except (OSError, ValueError, HTTPException):
                    skipped += 1
        if not self.cancelled(job["id"]):
            with self.store.connect() as db:
                rows = db.execute("SELECT id,file_path FROM items WHERE file_path IS NOT NULL AND status!='deleted'").fetchall()
            for item in rows:
                path = Path(item["file_path"])
                if path.is_relative_to(scan_root) and item["id"] not in seen and not path.exists():
                    self.store.update_item(item["id"], {"status": "missing"})
        return {"indexed": matched, "added": added, "skipped": skipped, "folder_id": folder["id"]}

    async def run_process(self, job: dict, args: list[str], *, duration: float = 0):
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.processes[job["id"]] = process
        try:
            async for line in process.stdout:
                if self.cancelled(job["id"]):
                    break
                text = line.decode(errors="replace").strip()
                progress = re.search(r"TVPROGRESS:(\d+(?:\.\d+)?)", text)
                if progress:
                    self.store.update_job(job["id"], status="running", progress=min(float(progress[1]) / 100, .99), message="Downloading media")
                if duration and text.startswith("out_time_us="):
                    value = text.split("=", 1)[1]
                    if value.isdigit():
                        self.store.update_job(job["id"], status="running", progress=min(int(value) / 1e6 / duration, .99), message="Creating browser rendition")
            await process.wait()
            if self.cancelled(job["id"]):
                raise ValueError("Cancelled")
            if process.returncode != 0:
                raise ValueError("Media tool failed. Verify the source is available and the host tools are up to date.")
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                await process.wait()
            self.processes.pop(job["id"], None)

    async def download(self, job: dict) -> dict:
        payload = job["payload"]
        url = await asyncio.to_thread(public_url, payload["url"])
        # CLI extractors follow links internally. Restrict that workflow to
        # supported public providers; arbitrary direct streams use the guarded
        # HTTP path rather than an unrestricted extractor.
        host = urlsplit(url).hostname.lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com", "archive.org"}:
            raise ValueError("Downloads support YouTube, Vimeo and archive.org. Use Stream for other public media URLs.")
        downloader = executable("yt-dlp")
        if not downloader:
            raise FileNotFoundError("Install yt-dlp on the host to enable downloads")
        job_dir = self.store.media_dir / job["id"]
        job_dir.mkdir(exist_ok=True)
        args = [downloader, "--ignore-config", "--no-playlist", "--no-plugin-dirs", "--no-cache-dir", "--newline",
                "--progress-template", "download:TVPROGRESS:%(progress._percent)d", "--max-filesize", "20G",
                "--restrict-filenames", "--write-info-json", "--clean-info-json", "--print", "after_move:filepath", "-o", str(job_dir / "media.%(ext)s")]
        ffmpeg = executable("ffmpeg")
        if ffmpeg:
            args += ["-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b", "--merge-output-format", "mp4", "--ffmpeg-location", ffmpeg]
        else:
            args += ["-f", "b[ext=mp4]/b"]
        args += ["--", url]
        await self.run_process(job, args)
        candidates = [p for p in job_dir.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and p.is_file()]
        if not candidates:
            raise ValueError("The downloader did not produce a playable media file")
        path = candidates[0].resolve()
        provider = {}
        info_path = job_dir / "media.info.json"
        if info_path.is_file() and info_path.stat().st_size <= 2 * 1024 * 1024:
            try:
                provider = json.loads(info_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if not isinstance(provider, dict):
            provider = {}
        title = payload.get("title") or provider.get("title") or parse_name(Path(urlsplit(url).path).name)["title"] or "Downloaded video"
        title = str(title).strip()[:300]
        info = parse_name(title + path.suffix)
        info["title"] = title
        year = payload.get("year") or provider.get("release_year")
        if isinstance(year, int) and 1880 <= year <= 2200:
            info["year"] = year
        info["kind"] = payload.get("kind") or info["kind"]
        item, _ = self.store.add_item(source_key="download:" + job["id"], file_path=str(path),
                                      folder_id=payload.get("folder_id"), technical=await asyncio.to_thread(probe, path), **info)
        if await asyncio.to_thread(make_artwork, self.store, item):
            self.store.update_item(item["id"], {"technical": {**item["technical"], "artwork": True}})
        await self.enrich_item(item["id"])
        await self.auto_streaming(item)
        return {"item_id": item["id"]}

    async def auto_streaming(self, item: dict):
        if (item["kind"] == "photo" or not self.store.setting("adaptive_streaming", True) or not executable("ffmpeg") or not executable("ffprobe")
                or playback.ready(self.store, item)):
            return
        if not any(j["kind"] == "streaming" and j["payload"].get("item_id") == item["id"]
                   and j["status"] in {"queued", "running"} for j in self.store.jobs()):
            await self.enqueue("streaming", {"item_id": item["id"]})

    async def streaming(self, job: dict) -> dict:
        async with self.rendition_slot:
            if self.cancelled(job["id"]):
                raise ValueError("Cancelled")
            item = self.store.item(job["payload"]["item_id"])
            ffmpeg = executable("ffmpeg")
            if not ffmpeg or not executable("ffprobe"):
                raise FileNotFoundError("Install FFmpeg and FFprobe on the host to optimize streaming")
            if not item.get("file_path") or item["status"] == "deleted":
                raise ValueError("Download this title before optimizing streaming")
            source = permitted_path(self.store, item["file_path"])
            source_version = playback.fingerprint(source)
            info = await asyncio.to_thread(probe, source)
            root = playback.rendition_root(self.store, item["id"], job["id"])
            if not root.resolve().is_relative_to(self.store.root.resolve()):
                raise ValueError("Streaming directory is outside the TV data root")
            root.mkdir(parents=True, exist_ok=True)
            published = False
            try:
                variants = []
                for height, bitrate in playback.profiles(info.get("height")):
                    destination = root / f"{height}p"
                    destination.mkdir(exist_ok=True)
                    await self.run_process(job, playback.ffmpeg_args(ffmpeg, source, destination, height, bitrate),
                                           duration=info.get("duration", 0))
                    if not (destination / "index.m3u8").is_file():
                        raise ValueError("The host did not produce a streaming playlist")
                    segment = next(iter(sorted(destination.glob("segment-*.ts"))), None)
                    encoded = await asyncio.to_thread(probe, segment) if segment else {}
                    variants.append((height, bitrate, encoded.get("width"), encoded.get("height")))
                master = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
                tracks = subtitles(self.store, item)
                for index, track in enumerate(tracks):
                    (root / f"sub-{index}.vtt").write_text(subtitle_content(self.store, item, track["id"]), encoding="utf-8")
                    duration = max(1, float(info.get("duration", 1)))
                    (root / f"sub-{index}.m3u8").write_text(
                        f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{int(duration) + 1}\n"
                        f"#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:{duration:.3f},\nsub-{index}.vtt\n#EXT-X-ENDLIST\n", encoding="utf-8")
                    language = re.sub(r"[^A-Za-z0-9-]", "", track["language"]) or "und"
                    master.append(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{language} {index + 1}",'
                                  f'LANGUAGE="{language}",AUTOSELECT=YES,DEFAULT=NO,URI="sub-{index}.m3u8"')
                for height, bitrate, width, actual_height in variants:
                    subs = ',SUBTITLES="subs"' if tracks else ""
                    resolution = f",RESOLUTION={width}x{actual_height}" if width and actual_height else ""
                    master.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={int((bitrate + 96_000) * 1.15)}{resolution}{subs}", f"{height}p/index.m3u8"])
                (root / "master.m3u8").write_text("\n".join(master) + "\n", encoding="utf-8")
                current = self.store.item(item["id"])
                if (self.cancelled(job["id"]) or current["status"] == "deleted"
                        or playback.fingerprint(permitted_path(self.store, current["file_path"])) != source_version):
                    raise ValueError("Source changed while preparing streaming. Retry this title.")
                (root / "published.json").write_text(json.dumps({"source": source_version}), encoding="utf-8")
                self.store.update_item(item["id"], {"technical": {**current["technical"], **info,
                    "hls_version": job["id"], "hls_source": source_version,
                    "streaming_ready": True, "streaming_qualities": [actual_height or height for height, _, _, actual_height in variants]}})
                published = True
                return {"item_id": item["id"]}
            finally:
                if not published and root.resolve().is_relative_to(self.store.root.resolve()):
                    # Root is derived solely from validated database IDs.
                    await asyncio.to_thread(shutil.rmtree, root, True)

    async def transcode(self, job: dict) -> dict:
        item = self.store.item(job["payload"]["item_id"])
        ffmpeg = executable("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("Install FFmpeg on the host to create browser renditions")
        if not item.get("file_path"):
            raise ValueError("Download remote media before creating a browser rendition")
        source = permitted_path(self.store, item["file_path"])
        destination = self.store.media_dir / (item["id"] + "-browser.mp4")
        partial_path = self.store.media_dir / (job["id"] + ".partial.mp4")
        source_version = playback.fingerprint(source)
        info = await asyncio.to_thread(probe, source)
        duration = info.get("duration", 0)
        codecs = (["-c:v", "copy", "-c:a", "copy"] if info.get("video_codec") == "h264" and info.get("audio_codec") in {None, "aac", "mp3"}
                  else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"])
        try:
            await self.run_process(job, [ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", str(source),
                                        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", *codecs,
                                        "-movflags", "+faststart", "-progress", "pipe:1", str(partial_path)], duration=duration)
            if playback.fingerprint(source) != source_version:
                raise ValueError("The source changed during video preparation. Retry from Studio.")
            partial_path.replace(destination)
        finally:
            partial_path.unlink(missing_ok=True)
        technical = {**item["technical"], "browser_path": str(destination), "browser_ready": True, "browser_source": source_version}
        self.store.update_item(item["id"], {"technical": technical})
        return {"item_id": item["id"]}
