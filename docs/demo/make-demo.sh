#!/usr/bin/env bash
# Builds the demo video from real runs of the tool: each terminal scene recorded with VHS,
# the graph with Playwright, then zooms, captions and transitions added by scenes.py,
# and a narrated sound track by sound.py.
# Needs docker, vhs, ffmpeg, unzip and a Python virtualenv with playwright, Pillow, numpy, soundfile and kokoro-onnx (see
# docs/demo/README.md). Written for a Linux machine such as a GitHub Codespace; output
# goes to $OUT (default ~/demo-out).
set -euo pipefail

REPO=${REPO:-/workspaces/DNS-Investigation-CLI}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HOME/demo-out}
PY=${PY:-$HOME/pw/bin/python}
FONT=${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}
BOLD=${BOLD:-/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf}
SIZE=1920x1080
mkdir -p "$OUT" "$HOME/demo"

echo "1/6 the ZIP the interviewer receives, and an empty graph"
git -C "$REPO" archive --format=zip --prefix=DNS-Investigation-CLI/ \
    -o "$HOME/demo/DNS-Investigation-CLI.zip" HEAD
# Started off camera so no scene waits for Neo4j. Same compose project name as the
# unzipped copy, so the scenes use this database.
(cd "$REPO" && docker compose down -v && docker compose up -d --wait neo4j) >/dev/null 2>&1

echo "2/6 terminal scenes"
"$PY" "$HERE/scenes.py" tapes "$OUT/tapes"
for tape in "$OUT"/tapes/*.tape; do
    (cd "$OUT" && vhs "$tape" >/dev/null)
    echo "  recorded $(basename "$tape" .tape)"
done

echo "3/6 graph scene"
# The browser runs on this same machine, so it wants the localhost link a laptop user
# gets; inside a codespace --cypher would otherwise print the forwarded GitHub address.
LINK=$(cd "$HOME/demo/DNS-Investigation-CLI" && CODESPACE_NAME= COMPOSE_PROGRESS=quiet \
    docker compose run --rm -T cli query run outage-impact --provider fastly --cypher \
    2>&1 >/dev/null | grep '^http')
"$PY" "$HERE/graph.py" "$LINK" "$OUT"

echo "4/6 narration, zooms and captions"
"$PY" "$HERE/scenes.py" voice "$OUT"
"$PY" "$HERE/scenes.py" render "$OUT"

echo "5/6 cards"
card() {  # card OUTPUT SECONDS TITLE SUBTITLE: text fades in and rises into place
    printf '%s' "$3" > "$OUT/title.txt"
    printf '%s' "$4" > "$OUT/subtitle.txt"
    local rise='if(lt(t\,0.8)\,(1-t/0.8)*(1-t/0.8)*40\,0)'
    ffmpeg -y -loglevel error -f lavfi -i "color=c=0x282a36:s=$SIZE:d=$2:r=30" -vf \
        "drawtext=fontfile=$BOLD:textfile=$OUT/title.txt:fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2-60+$rise:alpha=min(t/0.6\,1),\
drawtext=fontfile=$FONT:textfile=$OUT/subtitle.txt:fontcolor=0xbd93f9:fontsize=38:x=(w-text_w)/2:y=(h-text_h)/2+60+$rise:alpha=if(lt(t\,0.3)\,0\,min((t-0.3)/0.6\,1))" \
        -c:v libx264 -pix_fmt yuv420p "$1"
}
card "$OUT/0-title.mp4" 3.5 "DNS Investigation CLI" "URLs  ->  dig  ->  Neo4j graph  ->  investigation queries"
card "$OUT/9-end.mp4" 3 "github.com/YairZaid/DNS-Investigation-CLI" "Requirement: Docker only"

echo "6/6 assembly and sound"
"$PY" "$HERE/scenes.py" assemble "$OUT" "$OUT/silent.mp4"
"$PY" "$HERE/sound.py" "$OUT" "$OUT/silent.mp4" "$OUT/demo.mp4"
# A light GIF of the whole video, for the README.
ffmpeg -y -loglevel error -i "$OUT/demo.mp4" \
    -vf "fps=6,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
    "$OUT/demo.gif"

for f in demo.mp4 demo.gif; do
    printf '%-10s %8s  %ss\n' "$f" "$(du -h "$OUT/$f" | cut -f1)" \
        "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/$f" | cut -d. -f1)"
done
