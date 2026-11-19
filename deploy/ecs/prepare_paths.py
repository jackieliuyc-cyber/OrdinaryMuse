"""Build an immutable /score/ + /audio/ static release from the two existing apps.

The score H5 belongs to jackieliuyc-cyber/dynamic-score-stitcher. Apply its small
mount-path adaptation during packaging rather than maintaining a second copy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def score_sources(index, feedback):
    old_action, new_action = 'action="/api/feedback"', 'action="./api/feedback"'
    old_fetch, new_fetch = "fetch('/api/feedback', {", "fetch(feedbackForm.action, {"
    # Fail closed if the upstream form changes, before writing either output.
    for text, old, new in ((index, old_action, new_action), (feedback, old_fetch, new_fetch)):
        if not ((text.count(old) == 1 and text.count(new) == 0) or
                (text.count(old) == 0 and text.count(new) == 1)):
            raise ValueError("Score feedback markup changed; review its mount-path adaptation first.")
    index = index.replace(old_action, new_action)
    feedback = feedback.replace(old_fetch, new_fetch)
    return index, feedback


def prepare(score_dir, audio_dir, release):
    score_dir, audio_dir, release = Path(score_dir), Path(audio_dir), Path(release)
    if release.exists():
        raise FileExistsError("Use a new release path; existing releases are never overwritten.")
    index, feedback = score_sources((score_dir / "index.html").read_text(encoding="utf-8"),
                                    (score_dir / "feedback-form.js").read_text(encoding="utf-8"))
    for name in ("index.html", "app.js", "style.css"):
        if not (audio_dir / name).is_file():
            raise FileNotFoundError(audio_dir / name)
    # Only copy the public static directories. Databases, mail config and SSH
    # credentials never enter this release.
    release.mkdir(parents=True)
    shutil.copytree(score_dir, release / "score")
    shutil.copytree(audio_dir, release / "audio")
    (release / "score/index.html").write_text(index, encoding="utf-8")
    (release / "score/feedback-form.js").write_text(feedback, encoding="utf-8")
    manifest = {str(path.relative_to(release)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(release.rglob("*")) if path.is_file()}
    (release / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-dir", required=True)
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    manifest = prepare(args.score_dir, args.audio_dir, args.release)
    print(json.dumps({"release": args.release, "files": len(manifest)}))
