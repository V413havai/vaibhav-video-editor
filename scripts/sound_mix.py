"""Mix sound effects and an optional music bed under a video's voice, then level everything to -14 LUFS.

usage: python3 sound_mix.py <video> <cues.json> <out.mp4> [--sfx-dir DIR] [--lufs -14] [--no-highpass]

cues.json:
  {"music": {"file": "/abs/track.mp3", "volume": 0.18, "start": 0.0},      (optional)
   "cues": [{"t": 1.2, "name": "whoosh-short", "vol": 0.28},             (name = <sfx-dir>/<name>.mp3 or .wav)
            {"t": 4.0, "file": "/abs/cash.mp3", "vol": 0.3}]}            (or any file by path)
Times are seconds on the video's own timeline. The music loops if it's shorter than the video and ducks under the
voice (sidechain). The voice gets a light 80 Hz high-pass. Video is copied, not re-encoded.
With music, also writes <out>_nomusic.mp4 (voice + sound effects) for adding a trending sound inside the app.
The default sfx dir is awa-video-reel's sfx/ folder.
"""
import argparse, json, os, subprocess, sys

DEFAULT_SFX = os.path.join(os.path.expanduser('~'), '.claude', 'skills', 'awa-video-reel', 'sfx')
FMT = 'aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo'


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def resolve(cue, sfx_dir):
    if cue.get('file'):
        return cue['file']
    for ext in ('.mp3', '.wav', '.m4a', '.ogg'):
        p = os.path.join(sfx_dir, cue['name'] + ext)
        if os.path.isfile(p):
            return p
    man = os.path.join(sfx_dir, 'manifest.json')
    if os.path.isfile(man):
        m = json.load(open(man)).get(cue['name'])
        if m:
            return os.path.join(sfx_dir, m['file'])
    sys.exit(f'sound not found: {cue.get("name")} in {sfx_dir}')


def mix(video, cues, music, out, dur, lufs, highpass):
    inputs, chains, mixin = ['-i', video], [], []
    voice = f'[0:a]{FMT}' + (',highpass=f=80' if highpass else '')
    if music:
        chains.append(voice + ',asplit=2[voice][key]')
    else:
        chains.append(voice + '[voice]')
    mixin.append('[voice]')

    idx = 1
    for i, (path, t, vol) in enumerate(cues):
        inputs += ['-i', path]
        ms = max(0, int(round(t * 1000)))
        chains.append(f'[{idx}:a]{FMT},volume={vol:.3f},adelay={ms}|{ms}[s{i}]')
        mixin.append(f'[s{i}]')
        idx += 1

    if music:
        inputs += ['-stream_loop', '-1', '-ss', str(music.get('start', 0)), '-i', music['file']]
        chains.append(f'[{idx}:a]{FMT},atrim=0:{dur:.3f},volume={music.get("volume", 0.18):.3f},'
                      f'afade=t=in:d=0.6,afade=t=out:st={max(0, dur - 1.2):.3f}:d=1.2[mus]')
        chains.append('[mus][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[duck]')
        mixin.append('[duck]')

    pre = ';'.join(chains) + ';' + ''.join(mixin) + f'amix=inputs={len(mixin)}:duration=first:normalize=0,'
    target = f'loudnorm=I={lufs}:TP=-1.5:LRA=11'
    # pass 1: measure the mix; pass 2: normalise with those numbers (accurate on short clips)
    meas = subprocess.run(['ffmpeg', '-hide_banner', '-nostats'] + inputs +
                          ['-filter_complex', pre + target + ':print_format=json[out]', '-map', '[out]',
                           '-t', f'{dur:.3f}', '-f', 'null', '-'], capture_output=True, text=True, check=True).stderr
    m = json.loads(meas[meas.rindex('{'):meas.rindex('}') + 1])
    second = (f'{target}:measured_I={m["input_i"]}:measured_TP={m["input_tp"]}:measured_LRA={m["input_lra"]}:'
              f'measured_thresh={m["input_thresh"]}:offset={m["target_offset"]}:linear=true')
    cmd = ['ffmpeg', '-y', '-v', 'error'] + inputs + ['-filter_complex', pre + second + f',alimiter=limit=0.8:level=false,{FMT}[out]',
           '-map', '0:v', '-map', '[out]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
           '-t', f'{dur:.3f}', '-movflags', '+faststart', out]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video'); ap.add_argument('cues'); ap.add_argument('out')
    ap.add_argument('--sfx-dir', default=DEFAULT_SFX)
    ap.add_argument('--lufs', type=float, default=-14.0)
    ap.add_argument('--no-highpass', action='store_true')
    a = ap.parse_args()

    spec = json.load(open(a.cues))
    dur = duration(a.video)
    cues = [(resolve(c, a.sfx_dir), float(c['t']), float(c.get('vol', 0.3)))
            for c in spec.get('cues', []) if 0 <= float(c['t']) < dur]
    music = spec.get('music') if spec.get('music') and not spec['music'].get('off') else None
    if music and not os.path.isfile(music['file']):
        sys.exit(f'music file not found: {music["file"]}')

    mix(a.video, cues, music, a.out, dur, a.lufs, not a.no_highpass)
    print(f'OUT={a.out}  ({len(cues)} sound effects{", music" if music else ""})')
    if music:
        nm = os.path.splitext(a.out)[0] + '_nomusic.mp4'
        mix(a.video, cues, None, nm, dur, a.lufs, not a.no_highpass)
        print(f'NOMUSIC={nm}')


if __name__ == '__main__':
    main()
