"""The demo video's scenes, and the motion added to each: zooms, captions, transitions.

    python scenes.py tapes DIR            write one VHS tape per terminal scene into DIR
    python scenes.py voice DIR            speak each part's narration (Kokoro) into DIR
    python scenes.py render DIR           zoom and caption every recorded clip in DIR
    python scenes.py assemble DIR OUT     join the clips with transitions into OUT

Each terminal scene is recorded on its own (VHS), at 4K so a zoom stays sharp, then
rendered to 1080p here: a camera that eases between framings, and a caption with what
the output shows. Coordinates below are in 1920x1080 terms, the size the viewer sees.
Needs Pillow and ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 30
TYPING = 0.022  # seconds per typed character, the tape's TypingSpeed
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEMO = "~/demo"
PROJECT = "~/demo/DNS-Investigation-CLI"


@dataclass(frozen=True)
class Shot:
    """At second `at` (from the moment the output appears) the camera frames this:
    centre (x, y) and zoom (1 = the whole screen)."""

    at: float
    x: float = W / 2
    y: float = H / 2
    zoom: float = 1.0


@dataclass(frozen=True)
class Scene:
    name: str
    command: str
    hold: float  # seconds the output stays on screen
    caption: str
    shots: list[Shot] = field(default_factory=list)
    setup: str = f"cd {PROJECT}"


# Framings were read off the recording: output starts top left at y~68 and one line is
# ~18px high. While a command is typed the camera stays close on the prompt.
PROMPT = (440, 245, 2.2)


def prompt(at: float = 0) -> Shot:
    return Shot(at, *PROMPT)


SCENES = [
    Scene(
        "1-unzip",
        "unzip -q DNS-Investigation-CLI.zip && cd DNS-Investigation-CLI && ls",
        2.5,
        "The ZIP you receive: Docker is the only requirement",
        [prompt(), Shot(0.6, 560, 200, 1.75)],
        setup=f"cd {DEMO} && rm -rf DNS-Investigation-CLI",
    ),
    Scene(
        "2-doctor",
        "docker compose run --rm cli doctor",
        3.0,
        "doctor checks dig, the resolver and Neo4j before anything runs",
        [prompt(), Shot(0.7, 330, 190, 2.8)],
    ),
    Scene(
        "3-ingest",
        "docker compose run --rm cli ingest examples/urls.txt",
        3.5,
        "Every hostname is queried with dig and stored as a graph in Neo4j",
        [prompt(), Shot(0.7, 600, 220, 1.6)],
    ),
    Scene(
        "4-records",
        "docker compose run --rm cli records --inputs-only",
        5.5,
        "CNAME chains reveal who really serves a site: Fastly, Akamai, GitHub Pages",
        [prompt(), Shot(1.0, 505, 390, 1.9), Shot(2.8, 505, 390, 1.9), Shot(4.2, 505, 650, 1.9)],
    ),
    Scene(
        "5-shared-infra",
        "docker compose run --rm cli query run shared-infra --cross-apex --min-shared 6",
        5.0,
        "Unrelated organisations share the same Google Workspace mail servers",
        [prompt(), Shot(1.0, 630, 330, 1.6), Shot(2.8, 630, 330, 1.6), Shot(4.2, 1000, 330, 1.6)],
    ),
    Scene(
        "6-outage-impact",
        "docker compose run --rm cli query run outage-impact --provider fastly",
        5.0,
        "If Fastly goes down, 7 hostnames of 4 organisations go fully dark",
        [prompt(), Shot(1.0, 580, 310, 1.75), Shot(2.8, 580, 310, 1.75), Shot(4.0, 520, 190, 3.0)],
    ),
    Scene(
        "7-cypher",
        "docker compose run --rm cli query run outage-impact --provider fastly"
        " --cypher > outage.cypher",
        3.0,
        "--cypher saves the graph query and prints a Neo4j Browser link",
        [prompt(), Shot(0.7, 560, 200, 1.75)],
    ),
]

# The graph scene is recorded by graph.py; only a caption is added.
GRAPH = Scene("8-graph", "", 7.0, "The same result as a graph: Fastly in the centre")

# What the narrator says over each part, starting VOICE_AT seconds into it. A terminal
# scene holds its output longer when its line needs it; the cards and the graph have a
# fixed length, so their lines are kept short.
NARRATION = {
    "0-title": "This is the DNS Investigation CLI.",
    "1-unzip": "Unzip the project. Docker is the only thing you need.",
    "2-doctor": "Doctor checks that dig, the resolver, and Neo4j are ready.",
    "3-ingest": "Ingest resolves every hostname with dig, and stores the answers as a graph.",
    "4-records": "Records shows what DNS returned."
    " CNAME chains reveal who really serves each site.",
    "5-shared-infra": "Shared infra finds organisations that rely on the same infrastructure,"
    " like Google's mail servers.",
    "6-outage-impact": "Outage impact asks what breaks if a provider goes down."
    " For Fastly: seven hostnames, across four organisations.",
    "7-cypher": "And with the cypher flag, any result opens as a graph in Neo4j Browser.",
    "8-graph": "Here it is: Fastly in the centre, and everything that depends on it.",
    "9-end": "All you need is Docker.",
}
VOICE_AT = 0.25
FADE = 0.35  # transition length; the next part starts this much before a part ends
# Kokoro (open, Apache 2.0) through kokoro-onnx; the model files live outside the repo.
VOICES = Path("~/voices").expanduser()
VOICE = "af_heart"


def speak(out: Path) -> None:
    """One WAV per part, and voice.json with each one's length in seconds."""
    import soundfile
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(VOICES / "kokoro-v1.0.onnx"), str(VOICES / "voices-v1.0.bin"))
    lengths = {}
    for name, text in NARRATION.items():
        wav = out / f"voice-{name}.wav"
        samples, rate = kokoro.create(text, voice=VOICE, speed=1.0, lang="en-us")
        soundfile.write(str(wav), samples, rate, subtype="PCM_16")
        with wave.open(str(wav)) as w:
            lengths[name] = w.getnframes() / w.getframerate()
        print(f"  voice {name}: {lengths[name]:.1f}s", flush=True)
    (out / "voice.json").write_text(json.dumps(lengths, indent=2))


def hold(scene: Scene, out: Path) -> float:
    """The scene's hold, stretched so its narration ends before the next part starts."""
    voices = out / "voice.json"
    spoken = json.loads(voices.read_text()).get(scene.name, 0.0) if voices.exists() else 0.0
    return max(scene.hold, VOICE_AT + spoken + 0.5 + FADE - typing_time(scene))


LEAD = 0.4  # seconds on the empty prompt before typing starts


def typing_time(scene: Scene) -> float:
    """When the output appears: the lead, the typing, a short beat before Enter."""
    return LEAD + len(scene.command) * TYPING + 0.25


def write_tapes(out: Path) -> None:
    """Each tape takes three exact screenshots instead of a video: the empty prompt, the
    typed command, and the output. VHS drops frames when it records at 4K, which shortens
    the scene unpredictably; the typing and the hold are rebuilt from these stills."""
    out.mkdir(parents=True, exist_ok=True)
    for s in SCENES:
        shot = out.parent / s.name
        (out / f"{s.name}.tape").write_text(
            f"""Output "{out / (s.name + ".gif")}"
Set Shell "bash"
Set FontSize 30
Set Width 3840
Set Height 2160
Set Padding 56
Set Theme "Dracula"
Set WindowBar Colorful
Hide
Type@1ms "unset CODESPACE_NAME; export PS1='$ ' COMPOSE_PROGRESS=quiet; {s.setup} && clear" Enter
Wait /\\$\\s*$/
Sleep 500ms
Show
Screenshot "{shot}-prompt.png"
Sleep 300ms
Hide
Type@1ms "{s.command}"
Sleep 500ms
Show
Screenshot "{shot}-typed.png"
Sleep 300ms
Hide
Enter
Wait@120s /\\$\\s*$/
Sleep 700ms
Show
Screenshot "{shot}-output.png"
Sleep 300ms
Hide
"""
        )


# ---- camera ------------------------------------------------------------------------------


def ease(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def camera(shots: list[Shot], t: float) -> tuple[float, float, float]:
    if not shots:
        return W / 2, H / 2, 1.0
    if t <= shots[0].at:
        a = shots[0]
        return a.x, a.y, a.zoom
    for a, b in pairwise(shots):
        if t <= b.at:
            p = ease((t - a.at) / (b.at - a.at))
            # Interpolating 1/zoom keeps the zoom speed even to the eye.
            inv = 1 / a.zoom + (1 / b.zoom - 1 / a.zoom) * p
            return a.x + (b.x - a.x) * p, a.y + (b.y - a.y) * p, 1 / inv
    last = shots[-1]
    return last.x, last.y, last.zoom


def frame_box(x: float, y: float, zoom: float) -> tuple[float, float, float, float]:
    """The source region, in 1920x1080 terms, that fills the screen; kept inside it."""
    w, h = W / zoom, H / zoom
    left = min(max(x - w / 2, 0), W - w)
    top = min(max(y - h / 2, 0), H - h)
    return left, top, left + w, top + h


# ---- caption -------------------------------------------------------------------------------


def caption_layer(text: str) -> Image.Image:
    font = ImageFont.truetype(BOLD, 40)
    pad_x, pad_y = 38, 22
    box = font.getbbox(text)
    tw, th = box[2] - box[0], box[3] - box[1]
    layer = Image.new("RGBA", (tw + 2 * pad_x + 14, th + 2 * pad_y), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(
        (0, 0, layer.width - 1, layer.height - 1), radius=22, fill=(20, 21, 30, 225)
    )
    d.rounded_rectangle((0, 0, 14, layer.height - 1), radius=7, fill=(189, 147, 249, 255))
    d.text((pad_x + 14 - box[0], pad_y - box[1]), text, font=font, fill=(248, 248, 242, 255))
    return layer


def place_caption(frame: Image.Image, layer: Image.Image, since: float) -> None:
    """Slide up and fade in over 0.4s, at the bottom centre."""
    if since < 0:
        return
    p = ease(since / 0.4)
    alpha = layer.getchannel("A").point(lambda a: int(a * p))
    faded = layer.copy()
    faded.putalpha(alpha)
    x = (W - layer.width) // 2
    y = H - layer.height - 70 + int(30 * (1 - p))
    frame.alpha_composite(faded, (x, y))


# ---- render --------------------------------------------------------------------------------


def probe(path: Path) -> tuple[int, int, float]:
    info = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    stream = info["streams"][0]
    return stream["width"], stream["height"], float(info["format"]["duration"])


Frames = Iterator[tuple[float, Image.Image]]


def video_frames(src: Path, start: float, length: float) -> Frames:
    sw, sh, _ = probe(src)
    reader = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            str(src),
            "-t",
            str(length),
            "-vf",
            f"fps={FPS}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert reader.stdout is not None
    size, index = sw * sh * 3, 0
    while len(chunk := reader.stdout.read(size)) == size:
        yield index / FPS, Image.frombytes("RGB", (sw, sh), chunk)
        index += 1
    reader.wait()


def terminal_frames(scene: Scene, stills: Path, hold_for: float) -> Frames:
    """The scene rebuilt from its three stills: the command typed one character at a time
    (revealed from the typed still, with a moving cursor), then the output held."""
    empty = Image.open(f"{stills}-prompt.png").convert("RGB")
    typed = Image.open(f"{stills}-typed.png").convert("RGB")
    output = Image.open(f"{stills}-output.png").convert("RGB")
    # The command line is where the two stills differ; both cursors are inside it.
    x0, y0, x1, y1 = ImageChops.difference(empty, typed).getbbox() or (0, 0, 1, 1)
    cell = (x1 - x0) / (len(scene.command) + 1)
    cursor = empty.getpixel((int(x0 + cell / 2), (y0 + y1) // 2))
    out_at = typing_time(scene)
    for index in range(round((out_at + hold_for) * FPS)):
        t = index / FPS
        if t >= out_at:
            yield t, output
            continue
        typed_chars = min(max(int((t - LEAD) / TYPING), 0), len(scene.command))
        edge = round(x0 + typed_chars * cell)
        frame = empty.copy()
        if typed_chars:
            frame.paste(typed.crop((x0, y0, edge, y1)), (x0, y0))
        ImageDraw.Draw(frame).rectangle((edge, y0, round(edge + cell) - 1, y1 - 1), fill=cursor)
        yield t, frame


def render(scene: Scene, frames: Frames, dst: Path, output_at: float) -> None:
    """Re-frame the frames (any 16:9 size) to 1080p with the scene's camera and caption.
    `output_at` is the second at which the command's output appears."""
    writer = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ],
        stdin=subprocess.PIPE,
    )
    assert writer.stdin is not None
    layer = caption_layer(scene.caption)
    last: tuple[int, tuple[float, ...]] | None = None
    view = Image.new("RGB", (W, H))
    for t, image in frames:
        x, y, zoom = camera(scene.shots, t - output_at)
        scale = image.width / W
        box = tuple(v * scale for v in frame_box(x, y, zoom))
        # A still under a still camera needs no new resize, the slow part.
        if last != (id(image), box) or scene.name == GRAPH.name:
            view = image.resize((W, H), Image.Resampling.LANCZOS, box=box)
            last = (id(image), box)
        frame = view.convert("RGBA")
        place_caption(frame, layer, t - output_at - 0.5)
        writer.stdin.write(frame.convert("RGB").tobytes())
    writer.stdin.close()
    if writer.wait() != 0:
        raise SystemExit(f"ffmpeg failed on {dst}")


def render_all(out: Path) -> None:
    for s in SCENES:
        print("  render", s.name, flush=True)
        render(
            s,
            terminal_frames(s, out / s.name, hold(s, out)),
            out / f"{s.name}-final.mp4",
            typing_time(s),
        )
    start = float((out / "graph-start.txt").read_text())
    print("  render", GRAPH.name, flush=True)
    render(
        GRAPH,
        video_frames(out / "graph.webm", start, GRAPH.hold),
        out / f"{GRAPH.name}-final.mp4",
        0.0,
    )


# ---- assemble ------------------------------------------------------------------------------


def assemble(out: Path, dst: Path) -> None:
    """Title, scenes, graph, end card, with a short transition between each pair."""
    parts = (
        [out / "0-title.mp4"]
        + [out / f"{s.name}-final.mp4" for s in SCENES]
        + [out / f"{GRAPH.name}-final.mp4", out / "9-end.mp4"]
    )
    # Slides mark the big steps (into the terminal, into the browser, out); fades between
    # terminal scenes keep it calm.
    kinds = ["slideleft"] + ["fade"] * (len(SCENES) - 1) + ["slideleft", "fade"]
    fade = FADE
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]
    # xfade wants identical frame rates and time bases on both sides.
    chain = [f"[{i}:v]fps={FPS},settb=AVTB,format=yuv420p[s{i}]" for i in range(len(parts))]
    label, offset = "[s0]", 0.0
    starts = {parts[0].stem: 0.0}
    for i, kind in enumerate(kinds, start=1):
        offset += probe(parts[i - 1])[2] - fade
        starts[parts[i].stem.removesuffix("-final")] = round(offset, 3)
        nxt = f"[v{i}]"
        chain.append(
            f"{label}[s{i}]xfade=transition={kind}:duration={fade}:offset={offset:.3f}{nxt}"
        )
        label = nxt
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(chain),
            "-map",
            label,
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        check=True,
    )
    # Where each part starts in the joined video, for the sound track (sound.py).
    timeline = {
        "starts": starts,
        "fade": fade,
        "length": probe(dst)[2],
        "typing": {s.name: [LEAD, TYPING, len(s.command), typing_time(s)] for s in SCENES},
    }
    (out / "timeline.json").write_text(json.dumps(timeline, indent=2))


if __name__ == "__main__":
    action, folder = sys.argv[1], Path(sys.argv[2]).expanduser()
    if action == "tapes":
        write_tapes(folder)
    elif action == "voice":
        speak(folder)
    elif action == "render":
        render_all(folder)
    elif action == "assemble":
        assemble(folder, Path(sys.argv[3]).expanduser())
    else:
        raise SystemExit(__doc__)
