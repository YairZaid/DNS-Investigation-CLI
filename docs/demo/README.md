# Demo video

How the one-minute demo video is made. Every frame comes from a real run of the tool from
the ZIP, using Docker only. Nothing is mocked up.

| File | What it does |
|---|---|
| `make-demo.sh` | Runs the whole pipeline below, start to finish |
| `scenes.py` | The scenes: commands, camera moves (zoom and pan), captions, narration text; renders and joins them |
| `graph.py` | Opens the `--cypher` link in a headless browser (Playwright) and records the graph |
| `sound.py` | The sound track: key clicks, transition sounds, a quiet chord pad, and the narration on top |

Pipeline:
1. `git archive` builds the same ZIP the interviewer gets.
2. VHS runs each command in a real terminal at 4K and takes three stills: the empty prompt, the typed command, and the output.
3. `scenes.py` rebuilds each scene from those stills, so the timing is exact:
   - typing one character at a time;
   - a camera that eases between framings;
   - a caption.
4. Playwright records Neo4j Browser showing the same result as a graph.
5. Kokoro (open model, Apache 2.0) speaks one line per scene, and `sound.py` mixes the track.

All sound effects and music are generated in code, so there is nothing to license.

## Rebuilding

Written for Linux (the video was built in a GitHub Codespace). It needs:
- docker, ffmpeg and unzip;
- [VHS](https://github.com/charmbracelet/vhs);
- a Python virtualenv at `~/pw` (or set `PY`) with `playwright`, `pillow`, `numpy`, `soundfile` and `kokoro-onnx`, plus `playwright install chromium`;
- the Kokoro model files `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `~/voices`, from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0).

```bash
bash docs/demo/make-demo.sh
```

The output goes to `~/demo-out`:
- `demo.mp4`, about 55s with narration;
- `demo.gif`, silent.

The script resets the local Neo4j database (`docker compose down -v`) before recording.
