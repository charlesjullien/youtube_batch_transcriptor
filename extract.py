"""Extraction des sous-titres d'une playlist YouTube."""

from __future__ import annotations

import json
import re
import html
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

BUCKET_SECONDS = 5
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
TIMESTAMP_RE = re.compile(
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
)


class ExtractError(Exception):
    """Erreur montrée telle quelle dans l'interface."""


def safe_filename(title: str, fallback: str) -> str:
    text = (title or "").replace("|", " - ")
    text = re.sub(r'[\\/:*?"<>]+', " ", text)
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text in {".", ".."}:
        text = fallback
    return text[:140]


def _within_data_dir(data_dir: Path, path: Path) -> Path:
    root = data_dir.resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise ExtractError("Nom de dossier invalide.")
    return resolved


def find_playlist_dir(data_dir: Path, playlist_id: str) -> Path | None:
    if not data_dir.exists() or not playlist_id:
        return None
    direct = data_dir / playlist_id
    if direct.is_dir() and (direct / "manifest.json").exists():
        return direct
    for entry in data_dir.iterdir():
        manifest_path = entry / "manifest.json"
        if not entry.is_dir() or not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("id") == playlist_id:
            return entry
    return None


def ensure_playlist_dir(data_dir: Path, playlist_id: str, title: str) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    desired = _within_data_dir(data_dir, data_dir / safe_filename(title, playlist_id))
    existing = find_playlist_dir(data_dir, playlist_id)
    if existing:
        if existing.resolve() != desired and not desired.exists():
            existing.rename(desired)
            return desired
        return existing
    if desired.exists():
        desired = _within_data_dir(data_dir, data_dir / safe_filename(f"{desired.name} ({playlist_id})", playlist_id))
    desired.mkdir(parents=True, exist_ok=True)
    return desired


def find_video_json(directory: Path, video_id: str) -> Path | None:
    legacy = directory / f"{video_id}.json"
    if legacy.exists():
        return legacy
    for path in directory.glob("*.json"):
        if path.name == "manifest.json":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("id") == video_id:
            return path
    return None


def claim_file_stem(directory: Path, title: str, video_id: str, reserved: set[str], current: Path | None = None) -> str:
    occupied = {path.stem.casefold() for path in directory.glob("*.json") if path.name != "manifest.json"}
    occupied.update(reserved)
    if current is not None:
        occupied.discard(current.stem.casefold())
    base = safe_filename(title, video_id)
    stem = base
    if stem.casefold() in occupied or stem.casefold() == "manifest":
        stem = safe_filename(f"{base} ({video_id})", video_id)
    suffix = 2
    while stem.casefold() in occupied or stem.casefold() == "manifest":
        stem = safe_filename(f"{base} ({video_id}) {suffix}", video_id)
        suffix += 1
    reserved.add(stem.casefold())
    return stem


def _rename_pair(json_path: Path, stem: str) -> Path:
    desired = json_path.with_name(f"{stem}.json")
    if desired != json_path:
        txt_path = json_path.with_suffix(".txt")
        json_path.rename(desired)
        if txt_path.exists():
            txt_path.rename(desired.with_suffix(".txt"))
    return desired


def format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _language_rank(lang: str, manual: bool) -> tuple:
    normalized = (lang or "").lower().replace("_", "-")
    base = normalized.split("-")[0]
    if normalized in {"fr", "fr-fr"}:
        lang_rank = 0
    elif base == "fr":
        lang_rank = 1
    else:
        lang_rank = 2
    return (0 if manual else 1, lang_rank, normalized)


def _ranked_formats(formats) -> list[dict]:
    by_ext: dict[str, dict] = {}
    if not isinstance(formats, list):
        return []
    for item in formats:
        if not isinstance(item, dict):
            continue
        ext = (item.get("ext") or "").lower()
        if ext in {"json3", "vtt"} and item.get("url") and ext not in by_ext:
            by_ext[ext] = item
    return [by_ext[ext] for ext in ("json3", "vtt") if ext in by_ext]


def choose_subtitle(info: dict) -> dict | None:
    """Choisit une piste : manuelle avant automatique, français d'abord."""
    candidates = []
    groups = (
        (True, info.get("subtitles") or {}),
        (False, info.get("automatic_captions") or {}),
    )
    for manual, table in groups:
        if not isinstance(table, dict):
            continue
        for lang, formats in table.items():
            ranked = _ranked_formats(formats)
            if not ranked:
                continue
            candidates.append((_language_rank(lang, manual), lang, manual, ranked))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, lang, manual, ranked = candidates[0]
    return {
        "language": lang,
        "source": "manual" if manual else "auto",
        "formats": ranked,
    }


def _overlap_size(previous: str, text: str) -> int:
    limit = min(len(previous), len(text))
    for size in range(limit, 0, -1):
        if previous.endswith(text[:size]):
            return size
    return 0


def dedup_cues(cues: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Retire les répétitions des sous-titres automatiques cumulatifs."""
    emitted: list[tuple[float, str]] = []
    previous = ""
    for start, raw in cues:
        text = normalize_text(raw)
        if not text:
            continue
        if text == previous:
            continue
        addition = text
        if previous and text.startswith(previous):
            addition = normalize_text(text[len(previous) :])
        else:
            overlap = _overlap_size(previous, text) if previous else 0
            if overlap >= 12 or (overlap >= 4 and overlap >= int(len(text) * 0.6)):
                addition = normalize_text(text[overlap:])
        if addition:
            emitted.append((start, addition))
        previous = text
    return emitted


def parse_json3(payload: str) -> list[tuple[float, str]]:
    data = json.loads(payload.lstrip("\ufeff").strip())
    cues: list[tuple[float, str]] = []
    for event in data.get("events") or []:
        segments = event.get("segs") or []
        if not segments:
            continue
        text = "".join(segment.get("utf8") or "" for segment in segments)
        start = float(event.get("tStartMs") or 0) / 1000
        cues.append((start, text))
    return dedup_cues(cues)


def _parse_timestamp(value: str) -> float:
    value = value.strip().split()[0]
    match = TIMESTAMP_RE.search(value)
    if not match:
        raise ValueError(f"horodatage invalide : {value}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = (match.group(4) or "0").ljust(3, "0")[:3]
    return hours * 3600 + minutes * 60 + seconds + int(fraction) / 1000


def parse_vtt(payload: str) -> list[tuple[float, str]]:
    text = payload.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[float, str]] = []
    for block in re.split(r"\n\n+", text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        stamp_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if stamp_index is None:
            continue
        start_raw = lines[stamp_index].split("-->", 1)[0]
        try:
            start = _parse_timestamp(start_raw)
        except ValueError:
            continue
        body = " ".join(lines[stamp_index + 1 :])
        if body:
            cues.append((start, body))
    return dedup_cues(cues)


def parse_subtitle(payload: str, ext: str) -> list[tuple[float, str]]:
    ext = (ext or "").lower()
    if ext == "json3":
        return parse_json3(payload)
    if ext == "vtt":
        return parse_vtt(payload)
    raise ExtractError(f"Format de sous-titres non pris en charge : {ext}")


def bucket_cues(cues: list[tuple[float, str]], bucket: int = BUCKET_SECONDS) -> list[dict]:
    groups: dict[int, list[str]] = {}
    for start, text in cues:
        if start < 0:
            start = 0
        key = int(start // bucket) * bucket
        groups.setdefault(key, []).append(text)
    segments = []
    for key in sorted(groups):
        text = normalize_text(" ".join(groups[key]))
        if text:
            segments.append({"t": key, "text": text})
    return segments


def render_txt(title: str, url: str, segments: list[dict], with_times: bool = True) -> str:
    lines = [title or "", url or "", ""]
    for segment in segments:
        text = segment["text"]
        if with_times:
            lines.append(f"[{format_hms(segment['t'])}] {text}")
        else:
            lines.append(text)
    lines.append("")
    return "\n".join(lines)


def timestamps_hidden(data_dir: Path) -> bool:
    path = data_dir / "settings.json"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("hide_timestamps"))


def rewrite_transcripts(data_dir: Path, hide_timestamps: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    write_json(data_dir / "settings.json", {"hide_timestamps": hide_timestamps})
    if not data_dir.is_dir():
        return
    for entry in data_dir.iterdir():
        if not entry.is_dir():
            continue
        for json_path in entry.glob("*.json"):
            if json_path.name == "manifest.json":
                continue
            try:
                document = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            segments = document.get("segments")
            if not isinstance(segments, list):
                continue
            json_path.with_suffix(".txt").write_text(
                render_txt(
                    document.get("title") or "",
                    document.get("url") or "",
                    segments,
                    with_times=not hide_timestamps,
                ),
                encoding="utf-8",
            )


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _ydl_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "retries": 3,
        "extractor_retries": 3,
        "socket_timeout": 30,
    }


def _is_playlist_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    query = parse_qs(parsed.query)
    if path.endswith("/playlist"):
        return True
    return bool(query.get("list")) and not query.get("v")


def list_single_video(url: str) -> dict:
    options = _ydl_options()
    options["noplaylist"] = True
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise ExtractError(f"Impossible de lire la vidéo : {exc}") from exc
    if not info:
        raise ExtractError(
            "YouTube n'a pas renvoyé cette vidéo. Le lien est valide, "
            "mais depuis Vercel YouTube bloque la lecture des sous-titres."
        )
    if info.get("_type") in {"playlist", "multi_video"}:
        raise ExtractError("Ce lien ouvre une playlist. Pour une seule vidéo, enlevez le paramètre list=.")
    video_id = str(info.get("id") or "")
    if not SAFE_ID.match(video_id):
        raise ExtractError("Impossible de déterminer l'identifiant de la vidéo.")
    title = info.get("title") or video_id
    watch_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
    return {
        "id": video_id,
        "title": title,
        "url": watch_url,
        "videos": [{"id": video_id, "title": title, "url": watch_url}],
    }


def list_playlist(url: str) -> dict:
    options = _ydl_options()
    options["extract_flat"] = "in_playlist"
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise ExtractError(f"Impossible de lire la playlist : {exc}") from exc
    if not info or info.get("_type") not in {"playlist", "multi_video"}:
        raise ExtractError("Ce lien n'est ni une playlist ni une vidéo YouTube.")
    playlist_id = str(info.get("id") or "")
    if not SAFE_ID.match(playlist_id):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        playlist_id = (query.get("list") or [""])[0]
    if not SAFE_ID.match(playlist_id):
        raise ExtractError("Impossible de déterminer l'identifiant de la playlist.")
    videos = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video_id = str(entry.get("id") or "")
        if not SAFE_ID.match(video_id):
            continue
        title = entry.get("title") or video_id
        videos.append(
            {
                "id": video_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    if not videos:
        raise ExtractError("Cette playlist ne contient aucune vidéo accessible.")
    return {
        "id": playlist_id,
        "title": info.get("title") or playlist_id,
        "url": url,
        "videos": videos,
    }


def _fetch_subtitle(ydl: YoutubeDL, info: dict) -> dict | None:
    choice = choose_subtitle(info)
    if not choice:
        return None
    last_error = None
    for fmt in choice["formats"]:
        try:
            response = ydl.urlopen(fmt["url"])
            payload = response.read().decode("utf-8", errors="replace")
            segments = bucket_cues(parse_subtitle(payload, fmt.get("ext") or ""))
        except Exception as exc:
            last_error = exc
            continue
        if segments:
            return {
                "language": choice["language"],
                "source": choice["source"],
                "segments": segments,
            }
    if last_error:
        raise ExtractError(f"Lecture des sous-titres impossible : {last_error}")
    return None


def _load_cached(json_path: Path) -> dict | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("segments"):
        return None
    return data


def extract_playlist(
    url: str,
    data_dir: Path,
    force: bool = False,
    on_progress=None,
) -> dict:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ExtractError("Le lien doit commencer par https://")

    def progress(event: dict) -> None:
        if on_progress:
            on_progress(event)

    progress({"type": "listing"})
    playlist = list_playlist(url) if _is_playlist_url(url) else list_single_video(url)
    directory = ensure_playlist_dir(data_dir, playlist["id"], playlist["title"])
    reserved: set[str] = set()
    total = len(playlist["videos"])
    manifest_videos = []
    progress(
        {
            "type": "start",
            "playlist_id": playlist["id"],
            "playlist_title": playlist["title"],
            "total": total,
        }
    )

    options = _ydl_options()
    with_times = not timestamps_hidden(data_dir)
    with YoutubeDL(options) as ydl:
        for index, video in enumerate(playlist["videos"], start=1):
            cached_json = None if force else find_video_json(directory, video["id"])
            cached_document = _load_cached(cached_json) if cached_json else None
            if force:
                previous = find_video_json(directory, video["id"])
                if previous:
                    previous.with_suffix(".txt").unlink(missing_ok=True)
                    previous.unlink(missing_ok=True)
            name_title = (cached_document or {}).get("title") or video["title"]
            stem = claim_file_stem(directory, name_title, video["id"], reserved, cached_json)
            json_path = directory / f"{stem}.json"
            txt_path = directory / f"{stem}.txt"
            if cached_json and cached_json != json_path:
                cached_json = _rename_pair(cached_json, stem)
            base = {
                "id": video["id"],
                "title": video["title"],
                "url": video["url"],
            }
            progress(
                {
                    "type": "video",
                    "index": index,
                    "total": total,
                    "id": video["id"],
                    "title": video["title"],
                    "status": "downloading",
                }
            )
            if cached_document and json_path.exists() and txt_path.exists():
                cached = cached_document
                if cached:
                    manifest_videos.append(
                        {
                            **base,
                            "title": cached.get("title") or video["title"],
                            "status": "ok",
                            "language": cached.get("language"),
                            "source": cached.get("source"),
                            "file": stem,
                        }
                    )
                    _write_manifest(directory, playlist, manifest_videos)
                    progress(
                        {
                            "type": "video",
                            "index": index,
                            "total": total,
                            "id": video["id"],
                            "title": video["title"],
                            "status": "cached",
                        }
                    )
                    continue

            try:
                info = ydl.extract_info(video["url"], download=False)
                if not info:
                    raise ExtractError("vidéo inaccessible")
                if info.get("title"):
                    base["title"] = info["title"]
                fetched = _fetch_subtitle(ydl, info)
            except Exception as exc:
                if isinstance(exc, ExtractError):
                    reason = str(exc)
                elif isinstance(exc, DownloadError):
                    reason = f"Impossible de lire la vidéo : {exc}"
                else:
                    reason = f"Erreur pendant la transcription : {exc}"
                manifest_videos.append({**base, "status": "error", "reason": reason})
                _write_manifest(directory, playlist, manifest_videos)
                progress(
                    {
                        "type": "video",
                        "index": index,
                        "total": total,
                        "id": video["id"],
                        "title": base["title"],
                        "status": "error",
                        "detail": reason,
                    }
                )
                continue

            if not fetched:
                json_path.unlink(missing_ok=True)
                txt_path.unlink(missing_ok=True)
                manifest_videos.append(
                    {**base, "status": "skipped", "reason": "Aucun sous-titre disponible"}
                )
                _write_manifest(directory, playlist, manifest_videos)
                progress(
                    {
                        "type": "video",
                        "index": index,
                        "total": total,
                        "id": video["id"],
                        "title": base["title"],
                        "status": "skipped",
                    }
                )
                continue

            reserved.discard(stem.casefold())
            stem = claim_file_stem(directory, base["title"], video["id"], reserved)
            json_path = directory / f"{stem}.json"
            txt_path = directory / f"{stem}.txt"
            document = {
                **base,
                "language": fetched["language"],
                "source": fetched["source"],
                "segments": fetched["segments"],
            }
            write_json(json_path, document)
            txt_path.write_text(
                render_txt(base["title"], base["url"], fetched["segments"], with_times=with_times),
                encoding="utf-8",
            )
            manifest_videos.append(
                {
                    **base,
                    "status": "ok",
                    "language": fetched["language"],
                    "source": fetched["source"],
                    "file": stem,
                }
            )
            _write_manifest(directory, playlist, manifest_videos)
            progress(
                {
                    "type": "video",
                    "index": index,
                    "total": total,
                    "id": video["id"],
                    "title": base["title"],
                    "status": "ok",
                }
            )

    manifest = _write_manifest(directory, playlist, manifest_videos)
    progress({"type": "done", "playlist_id": playlist["id"]})
    return manifest


def _write_manifest(directory: Path, playlist: dict, videos: list[dict]) -> dict:
    manifest = {
        "id": playlist["id"],
        "title": playlist["title"],
        "url": playlist["url"],
        "videos": videos,
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def normalize_saved_names(data_dir: Path) -> None:
    if not data_dir.exists():
        return
    for entry in list(data_dir.iterdir()):
        manifest_path = entry / "manifest.json"
        if not entry.is_dir() or not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        reserved: set[str] = set()
        changed = False
        for video in manifest.get("videos") or []:
            video_id = str(video.get("id") or "")
            if video.get("status") != "ok" or not SAFE_ID.match(video_id):
                continue
            current = find_video_json(entry, video_id)
            if not current:
                continue
            stem = claim_file_stem(entry, video.get("title") or video_id, video_id, reserved, current)
            renamed = _rename_pair(current, stem)
            if video.get("file") != renamed.stem:
                video["file"] = renamed.stem
                changed = True
        if changed:
            write_json(manifest_path, manifest)
        ensure_playlist_dir(data_dir, str(manifest.get("id") or entry.name), manifest.get("title") or entry.name)


def read_manifest(data_dir: Path, playlist_id: str) -> dict:
    directory = find_playlist_dir(data_dir, playlist_id)
    if not directory:
        raise ExtractError("Playlist introuvable.")
    path = directory / "manifest.json"
    if not path.exists():
        raise ExtractError("Playlist introuvable.")
    return json.loads(path.read_text(encoding="utf-8"))


def read_video(data_dir: Path, playlist_id: str, video_id: str) -> dict:
    directory = find_playlist_dir(data_dir, playlist_id)
    if not directory:
        raise ExtractError("Playlist introuvable.")
    json_path = find_video_json(directory, video_id)
    if not json_path:
        raise ExtractError("Transcription introuvable.")
    return json.loads(json_path.read_text(encoding="utf-8"))


def list_saved_playlists(data_dir: Path) -> list[dict]:
    normalize_saved_names(data_dir)
    if not data_dir.exists():
        return []
    playlists = []
    for entry in sorted(data_dir.iterdir()):
        manifest_path = entry / "manifest.json"
        if not entry.is_dir() or not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        videos = manifest.get("videos") or []
        playlists.append(
            {
                "id": manifest.get("id") or entry.name,
                "title": manifest.get("title") or entry.name,
                "url": manifest.get("url") or "",
                "count": sum(1 for video in videos if video.get("status") == "ok"),
                "total": len(videos),
            }
        )
    return playlists
