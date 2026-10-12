"""Interface locale de lecture des transcriptions."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from extract import (
    ExtractError,
    extract_playlist,
    list_saved_playlists,
    read_manifest,
    read_video,
    rewrite_transcripts,
    timestamps_hidden,
)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

app = Flask(__name__)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_running = False


def _snapshot(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "message": job["message"],
        "playlist_id": job["playlist_id"],
        "playlist_title": job["playlist_title"],
        "index": job["index"],
        "total": job["total"],
        "video_title": job["video_title"],
        "phase": job["phase"],
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/playlists")
def playlists():
    return jsonify(list_saved_playlists(DATA))


@app.get("/api/playlists/<playlist_id>")
def playlist(playlist_id: str):
    try:
        return jsonify(read_manifest(DATA, playlist_id))
    except ExtractError as exc:
        return jsonify({"error": str(exc)}), 404


@app.get("/api/playlists/<playlist_id>/videos/<video_id>")
def video(playlist_id: str, video_id: str):
    try:
        return jsonify(read_video(DATA, playlist_id, video_id))
    except ExtractError as exc:
        return jsonify({"error": str(exc)}), 404


@app.get("/api/timestamps")
def get_timestamps():
    return jsonify({"hidden": timestamps_hidden(DATA)})


@app.post("/api/timestamps")
def set_timestamps():
    body = request.get_json(silent=True) or {}
    hidden = bool(body.get("hidden"))
    try:
        rewrite_transcripts(DATA, hidden)
    except OSError as exc:
        return jsonify({"error": f"Impossible de modifier les fichiers texte : {exc}"}), 500
    return jsonify({"hidden": hidden})


@app.post("/api/extract")
def start_extract():
    global _running
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    force = bool(body.get("force"))
    if not url:
        return jsonify({"error": "Indiquez le lien d'une playlist ou d'une vidéo."}), 400

    with _lock:
        if _running:
            return jsonify({"error": "Une extraction est déjà en cours."}), 409
        _running = True
        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "running",
            "message": "",
            "playlist_id": None,
            "playlist_title": "",
            "index": 0,
            "total": 0,
            "video_title": "",
            "phase": "listing",
        }
        _jobs[job["id"]] = job
        snapshot = _snapshot(job)

    def on_progress(event: dict) -> None:
        with _lock:
            kind = event.get("type")
            if kind == "listing":
                job["phase"] = "listing"
            elif kind == "start":
                job["phase"] = "extracting"
                job["playlist_id"] = event.get("playlist_id")
                job["playlist_title"] = event.get("playlist_title") or ""
                job["total"] = event.get("total") or 0
            elif kind == "video":
                job["phase"] = "extracting"
                job["index"] = event.get("index") or 0
                job["total"] = event.get("total") or job["total"]
                job["video_title"] = event.get("title") or ""
            elif kind == "done":
                job["phase"] = "done"
                job["playlist_id"] = event.get("playlist_id") or job["playlist_id"]

    def run() -> None:
        global _running
        try:
            manifest = extract_playlist(url, DATA, force=force, on_progress=on_progress)
            with _lock:
                job["status"] = "done"
                job["phase"] = "done"
                job["playlist_id"] = manifest["id"]
                job["playlist_title"] = manifest.get("title") or ""
                job["total"] = len(manifest.get("videos") or [])
                job["index"] = job["total"]
        except ExtractError as exc:
            with _lock:
                job["status"] = "error"
                job["message"] = str(exc)
        except Exception as exc:
            with _lock:
                job["status"] = "error"
                job["message"] = f"Erreur inattendue : {exc}"
        finally:
            with _lock:
                _running = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify(snapshot)


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with _lock:
        found = _jobs.get(job_id)
        if not found:
            return jsonify({"error": "Tâche introuvable."}), 404
        return jsonify(_snapshot(found))


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, threaded=True)
