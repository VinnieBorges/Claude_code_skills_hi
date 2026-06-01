import os
import subprocess
import winreg

def format_ass_time(seconds):
    """Formats seconds into ASS time format H:MM:SS.cs"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        centiseconds = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"

# ---------------------------------------------------------------------------
# Boundary transition support (Hook -> Demo -> CTA cross-fades)
# ---------------------------------------------------------------------------
# Friendly transition names mapped to FFmpeg `xfade` transition identifiers.
# Anything not listed here resolves to None == hard cut (plain concat).
XFADE_TRANSITIONS = {
    "none": None,
    "hard": None,
    "cut": None,
    # Cross dissolve / fade
    "fade": "fade",
    "crossfade": "fade",
    "dissolve": "dissolve",
    # Dip to black / white
    "fade_black": "fadeblack",
    "dip_to_black": "fadeblack",
    "fadeblack": "fadeblack",
    "fade_white": "fadewhite",
    "fadewhite": "fadewhite",
    # Zoom / blur
    "zoom_blur": "zoomin",
    "zoomin": "zoomin",
    "hblur": "hblur",
    # Slides / wipes
    "slide": "slideleft",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
    "wipe": "wipeleft",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    # Shapes
    "circleopen": "circleopen",
    "circleclose": "circleclose",
    "radial": "radial",
    "pixelize": "pixelize",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
}

# Defaults used when the caller asks for transitions but does not specify a
# duration. Kept conservative so we never eat meaningful spoken content.
DEFAULT_TRANSITION = "fade"
DEFAULT_TRANSITION_DURATION = 0.4
MIN_TRANSITION_DURATION = 0.1


def resolve_transition(name):
    """Maps a friendly transition name to an FFmpeg xfade id, or None for a hard cut."""
    if not name:
        return None
    return XFADE_TRANSITIONS.get(str(name).strip().lower(), None)


def _clamp_transition_duration(trans_dur, clip_durations):
    """
    Clamps the transition duration so every clip is comfortably longer than the
    crossfade. xfade overlaps the two clips, so a transition longer than (half)
    the shortest clip would consume the whole segment. Returns 0.0 when no clean
    transition is possible (caller should fall back to a hard cut).
    """
    if not clip_durations:
        return 0.0
    shortest = min(clip_durations)
    # Cap at half of the shortest clip so neither side is fully consumed.
    max_allowed = max(0.0, shortest * 0.5)
    d = min(float(trans_dur), max_allowed)
    return d if d >= MIN_TRANSITION_DURATION else 0.0


def plan_transition(transition, transition_duration, clip_durations):
    """
    Decides whether a cross-fade chain can be applied to the given clips.

    Returns a tuple (use_xfade, duration, xfade_id). When use_xfade is False the
    caller should use a plain concat. This single source of truth keeps the
    video/audio assembly and the subtitle timeline in agreement.
    """
    xf = resolve_transition(transition)
    if not xf or len(clip_durations) < 2:
        return False, 0.0, None
    if transition_duration is None:
        transition_duration = DEFAULT_TRANSITION_DURATION
    d = _clamp_transition_duration(transition_duration, clip_durations)
    if d <= 0.0:
        return False, 0.0, None
    return True, d, xf


def append_xfade_chain(filter_parts, n, clip_durations, xfade_id, duration,
                       v_in="v", a_in="a", v_out="v_concat", a_out="a_concat"):
    """
    Appends an xfade (video) + acrossfade (audio) chain that joins the labelled
    streams [v0..v{n-1}] / [a0..a{n-1}] into [v_out] / [a_out].

    xfade is pairwise and overlaps the two inputs by `duration`, so the running
    merged stream shrinks by `duration` at every boundary. The offset for each
    transition is therefore the length of everything merged so far minus the
    overlap. Returns the final total duration of the merged stream.
    """
    prev_v = f"[{v_in}0]"
    prev_a = f"[{a_in}0]"
    merged_dur = clip_durations[0]

    for i in range(1, n):
        offset = merged_dur - duration
        cur_v = f"[{v_in}{i}]"
        cur_a = f"[{a_in}{i}]"
        # Last hop writes to the public output labels expected downstream.
        out_v = f"[vx{i}]" if i < n - 1 else f"[{v_out}]"
        out_a = f"[ax{i}]" if i < n - 1 else f"[{a_out}]"

        filter_parts.append(
            f"{prev_v}{cur_v}xfade=transition={xfade_id}:duration={duration:.3f}:offset={offset:.3f}{out_v}"
        )
        # Triangular crossfade curves keep perceived loudness roughly constant.
        filter_parts.append(
            f"{prev_a}{cur_a}acrossfade=d={duration:.3f}:c1=tri:c2=tri{out_a}"
        )

        prev_v = out_v
        prev_a = out_a
        merged_dur = merged_dur + clip_durations[i] - duration

    return merged_dur

STYLE_CONFIGS = {
    "TikTok Bold": {
        "fontsize": 75, "bold": -1, "border_style": 1, "outline": 5.0, "shadow": 0.0, "alignment": 2, "margin_v": 75,
        "primary": "&H00FFFFFF", "highlight": "&H0000FF00", "outline_col": "&H00000000", "shadow_col": "&H00000000"
    },
    "Cyberpunk Neon": {
        "fontsize": 72, "bold": -1, "border_style": 1, "outline": 1.5, "shadow": 4.0, "alignment": 2, "margin_v": 70,
        "primary": "&H00FF00FF", "highlight": "&H00FFFF00", "outline_col": "&H00FFFFFF", "shadow_col": "&H70FF00FF"
    },
    "Netflix Pop": {
        "fontsize": 60, "bold": -1, "border_style": 3, "outline": 0.0, "shadow": 0.0, "alignment": 2, "margin_v": 65,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF", "outline_col": "&H00000000", "shadow_col": "&H90000000"
    },
    "Retro Yellow": {
        "fontsize": 68, "bold": -1, "border_style": 1, "outline": 3.0, "shadow": 3.0, "alignment": 2, "margin_v": 70,
        "primary": "&H0000FFFF", "highlight": "&H00FFFFFF", "outline_col": "&H00000000", "shadow_col": "&H00000000"
    },
    "Bold Yellow": {
        "fontsize": 70, "bold": -1, "border_style": 1, "outline": 4.0, "shadow": 0.0, "alignment": 2, "margin_v": 65,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF", "outline_col": "&H00000000", "shadow_col": "&H00000000"
    },
    "Clean White": {
        "fontsize": 60, "bold": 0, "border_style": 1, "outline": 0.5, "shadow": 3.0, "alignment": 2, "margin_v": 65,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF", "outline_col": "&H00000000", "shadow_col": "&H90000000"
    },
    "Minimalist": {
        "fontsize": 38, "bold": 0, "border_style": 1, "outline": 0.0, "shadow": 0.0, "alignment": 2, "margin_v": 35,
        "primary": "&H00FFFFFF", "highlight": "&H0000FFFF", "outline_col": "&H00000000", "shadow_col": "&H00000000"
    }
}

def get_ass_header(style_preset, font_family="Montserrat"):
    """Generates ASS header with styling presets."""
    header = """[Script Info]
Title: ViniCut-AI Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    if isinstance(style_preset, dict):
        cfg = style_preset
    else:
        cfg = STYLE_CONFIGS.get(style_preset, STYLE_CONFIGS["Bold Yellow"])

    fontsize = cfg["fontsize"]
    bold = cfg["bold"]
    border_style = cfg["border_style"]
    outline = cfg["outline"]
    shadow = cfg["shadow"]
    alignment = cfg["alignment"]
    margin_v = cfg["margin_v"]
    primary = cfg["primary"]
    highlight = cfg["highlight"]
    outline_col = cfg["outline_col"]
    shadow_col = cfg["shadow_col"]

    header += f"Style: Default,{font_family},{fontsize},{primary},{highlight},{outline_col},{shadow_col},{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},{alignment},10,10,{margin_v},1\n"
    header += """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header

def generate_ass_file(segments, style_preset, output_path, font_family="Montserrat"):
    """Generates an ASS subtitle file with dynamic word highlight styling."""
    content = get_ass_header(style_preset, font_family=font_family)

    if isinstance(style_preset, dict):
        cfg = style_preset
    else:
        cfg = STYLE_CONFIGS.get(style_preset, STYLE_CONFIGS["Bold Yellow"])

    primary_color = cfg.get("primary", "&H00FFFFFF")
    highlight_color = cfg.get("highlight", "&H0000FFFF")

    # Check if custom formatting/animation tags exist
    active_word_tags = cfg.get("active_word_tags")
    inactive_word_tags = cfg.get("inactive_word_tags")

    if active_word_tags:
        highlight_tag = f"{{{active_word_tags}}}"
    else:
        highlight_tag = f"{{\\c{highlight_color}&}}"

    if inactive_word_tags:
        base_tag = f"{{{inactive_word_tags}}}"
    else:
        base_tag = f"{{\\c{primary_color}&}}"

    for idx, seg in enumerate(segments):
        words = seg.get("words", [])
        if words:
            # Generate dialogue entry for each word highlight span
            for w_idx, active_word in enumerate(words):
                start_t = format_ass_time(active_word["start"])
                end_t = format_ass_time(active_word["end"])

                # Rebuild text highlighting the active word
                text_parts = []
                for curr_w_idx, w in enumerate(words):
                    word_text = w["word"].strip()
                    if curr_w_idx == w_idx:
                        text_parts.append(f"{highlight_tag}{word_text}{base_tag}")
                    else:
                        text_parts.append(word_text)

                text_line = " ".join(text_parts).strip()
                content += f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{text_line}\n"
        else:
            # Fallback if word-level data is missing
            start = format_ass_time(seg["start"])
            end = format_ass_time(seg["end"])
            text = seg["text"].strip().replace("\n", " ")
            content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path

def get_ffmpeg_env():
    """Builds clean environment with refreshed path for FFmpeg."""
    env = os.environ.copy()
    winget_links = r"C:\Users\Administrador\AppData\Local\Microsoft\WinGet\Links"

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
            sys_path, _ = winreg.QueryValueEx(key, "Path")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            usr_path, _ = winreg.QueryValueEx(key, "Path")

        # Expand environment variables like %USERPROFILE% or %SystemRoot%
        sys_path = os.path.expandvars(sys_path)
        usr_path = os.path.expandvars(usr_path)

        new_path = sys_path + ";" + usr_path + ";" + winget_links

        # Update Path in all possible casings
        path_keys = [k for k in env.keys() if k.upper() == "PATH"]
        if path_keys:
            for k in path_keys:
                env[k] = new_path
        else:
            env["PATH"] = new_path
    except Exception:
        # Fallback if registry query fails: just append to existing PATH
        path_keys = [k for k in env.keys() if k.upper() == "PATH"]
        if path_keys:
            for k in path_keys:
                env[k] = env[k] + ";" + winget_links
        else:
            env["PATH"] = winget_links

    return env

def escape_ass_path(path):
    """Escapes ASS file path for Windows FFMPEG subtitles filter."""
    path = path.replace("\\", "/")
    if ":" in path:
        path = path.replace(":", "\\:")
    return path

def render_subtitles(video_path, ass_path, output_path):
    """Burns ASS subtitles into the video using h264_nvenc hardware acceleration."""
    escaped_ass = escape_ass_path(ass_path)

    # Locate custom fonts folder
    base_dir = os.path.dirname(os.path.abspath(__file__))
    fonts_dir = os.path.join(base_dir, "fonts").replace("\\", "/")

    if os.path.exists(fonts_dir) and os.listdir(fonts_dir):
        escaped_fonts = fonts_dir.replace(":", "\\:")
        command = f'ffmpeg -i "{video_path}" -vf "subtitles=\'{escaped_ass}\':fontsdir=\'{escaped_fonts}\'" -c:v h264_nvenc -preset p4 -c:a aac -b:a 192k -y "{output_path}"'
    else:
        command = f'ffmpeg -i "{video_path}" -vf "subtitles=\'{escaped_ass}\'" -c:v h264_nvenc -preset p4 -c:a aac -b:a 192k -y "{output_path}"'

    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())
    if result.returncode != 0:
        raise Exception(f"FFmpeg error burning subtitles: {result.stderr}")
    return output_path

def get_video_duration(video_path):
    """Helper to get video duration using ffprobe."""
    command = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())
    if result.returncode == 0:
        try:
            return float(result.stdout.strip())
        except ValueError:
            pass
    return 0.0

def get_smart_cut_point(start_time, target_end_time, segments, total_duration):
    """
    Finds the closest logical word or segment boundary to target_end_time
    using the transcription segments to avoid cutting mid-word or mid-sentence.
    """
    if not segments:
        return min(target_end_time, total_duration)

    # Gather all words
    words = []
    for seg in segments:
        if "words" in seg and seg["words"]:
            words.extend(seg["words"])
        else:
            words.append({"word": seg.get("text", ""), "start": seg["start"], "end": seg["end"]})

    if not words:
        return min(target_end_time, total_duration)

    candidates = []
    for i, w in enumerate(words):
        word_text = w.get("word", "")
        has_punctuation = any(char in word_text for char in [".", ",", "!", "?", ";", "-"])

        candidates.append({
            "time": float(w["end"]),
            "type": "word_end",
            "word_index": i,
            "has_punctuation": has_punctuation
        })
        candidates.append({
            "time": float(w["start"]),
            "type": "word_start",
            "word_index": i,
            "has_punctuation": False
        })

    valid_candidates = [c for c in candidates if start_time < c["time"] <= total_duration]
    if not valid_candidates:
        return min(target_end_time, total_duration)

    best_candidate = None
    best_score = float('inf')

    for c in valid_candidates:
        dist = abs(c["time"] - target_end_time)
        score = dist

        if c["type"] == "word_end" and c["has_punctuation"]:
            score -= 0.6

        if c["type"] == "word_end":
            idx = c["word_index"]
            if idx + 1 < len(words):
                next_start = float(words[idx+1]["start"])
                gap = next_start - c["time"]
                if gap > 0.08:
                    c["time"] = c["time"] + (gap / 2.0)
                    score -= min(0.8, gap)
        elif c["type"] == "word_start":
            idx = c["word_index"]
            if idx > 0:
                prev_end = float(words[idx-1]["end"])
                gap = c["time"] - prev_end
                if gap > 0.08:
                    c["time"] = prev_end + (gap / 2.0)
                    score -= min(0.8, gap)

        if score < best_score:
            best_score = score
            best_candidate = c

    if best_candidate:
        return max(start_time + 0.5, min(best_candidate["time"], total_duration))

    return min(target_end_time, total_duration)

def make_semantic_cut(video_path, segments_map, target_duration, output_path, segments=None, zoom_effect=1, bg_music_path=None, transition=None, transition_duration=None):
    """
    Cuts and merges segments from video_path according to semantic parts to match target_duration.
    segments_map = {
        'hook': (start, end),
        'demo': (start, end),
        'cta': (start, end)
    }

    transition / transition_duration:
        When `transition` resolves to a real xfade effect (e.g. "fade",
        "fade_black", "zoom_blur") and there is more than one slice, consecutive
        segments are joined with a cross-fade (video) + acrossfade (audio)
        instead of a hard cut. Pass None / "none" to keep the original hard cut.
        Note: xfade overlaps clips, so the output is shorter than the nominal
        target by roughly (num_slices - 1) * transition_duration.
    """
    total_dur = get_video_duration(video_path)
    if total_dur <= target_duration:
        # Video is shorter than target, just output it directly with optional bg music mixing
        if bg_music_path and os.path.exists(bg_music_path):
            filter_parts = [
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v_scale]",
                f"[1:a]atrim=0:{total_dur},asetpts=PTS-STARTPTS[bg_raw]",
                "[bg_raw][0:a]sidechaincompress=threshold=0.15:ratio=4:attack=50:release=300,volume=0.15[bg_ducked]",
                "[0:a][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a]"
            ]
            filter_graph = "; ".join(filter_parts)
            command = f'ffmpeg -i "{video_path}" -stream_loop -1 -i "{bg_music_path}" -filter_complex "{filter_graph}" -map "[v_scale]" -map "[a]" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'
        else:
            command = f'ffmpeg -i "{video_path}" -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'
        subprocess.run(command, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())
        return output_path

    hook = segments_map.get("hook", (0.0, min(5.0, total_dur)))
    demo = segments_map.get("demo", (hook[1], max(hook[1], total_dur - 5.0)))
    cta = segments_map.get("cta", (total_dur - 5.0, total_dur))

    slices = []
    ai_slices = None
    if isinstance(segments_map, dict) and "standard_cuts" in segments_map:
        key = f"{target_duration}s"
        if key in segments_map["standard_cuts"]:
            ai_slices = segments_map["standard_cuts"][key]

    if ai_slices:
        for idx, (start, end) in enumerate(ai_slices):
            if end <= hook[1]:
                segment_type = "hook"
            elif start >= cta[0]:
                segment_type = "cta"
            else:
                segment_type = "demo"

            smart_start = get_smart_cut_point(max(0.0, start - 0.5), start, segments, total_dur)
            smart_end = get_smart_cut_point(start, end, segments, total_dur)
            if smart_end > smart_start:
                slices.append((smart_start, smart_end, segment_type))
    else:
        # Calculate crop durations based on targets with smart endpoints (fallback)
        if target_duration == 5:
            smart_end = get_smart_cut_point(hook[0], hook[0] + 5.0, segments, total_dur)
            slices.append((hook[0], smart_end, "hook"))
        else:
            # Hook contribution: up to 5s
            hook_target_dur = min(hook[1] - hook[0], 5.0)
            smart_hook_end = get_smart_cut_point(hook[0], hook[0] + hook_target_dur, segments, total_dur)
            hook_dur = smart_hook_end - hook[0]
            if hook_dur > 0:
                slices.append((hook[0], smart_hook_end, "hook"))

            # CTA contribution: up to 5s
            cta_target_dur = min(cta[1] - cta[0], 5.0)
            target_cta_start = max(cta[0], cta[1] - cta_target_dur)
            smart_cta_start = get_smart_cut_point(cta[0], target_cta_start, segments, total_dur)
            cta_dur = cta[1] - smart_cta_start

            # Demo contribution: the rest
            demo_target_dur = target_duration - (hook_dur if hook_dur > 0 else 0) - (cta_dur if cta_dur > 0 else 0)
            demo_dur = min(demo[1] - demo[0], demo_target_dur)

            if demo_dur > 0:
                smart_demo_end = get_smart_cut_point(demo[0], demo[0] + demo_dur, segments, total_dur)
                slices.append((demo[0], smart_demo_end, "demo"))

            if cta_dur > 0:
                slices.append((smart_cta_start, cta[1], "cta"))

    # Compile per-segment trim filters
    filter_parts = []
    inputs = []
    clip_durations = []
    for idx, (start, end, segment_type) in enumerate(slices):
        clip_durations.append(end - start)
        # Apply 1.15x scale/crop zoom for Hook and CTA to enhance retention
        if zoom_effect == 1 and segment_type in ("hook", "cta"):
            filter_parts.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"crop=w=iw/1.15:h=ih/1.15:x=(in_w-out_w)/2:y=(in_h-out_h)/2,scale=1080:1920,setsar=1[v{idx}]"
            )
        else:
            filter_parts.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v{idx}]"
            )

        filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{idx}]")
        inputs.append(f"[v{idx}][a{idx}]")

    # Assemble segments: cross-fade transitions when requested, else a hard concat.
    use_xfade, trans_d, xfade_id = plan_transition(transition, transition_duration, clip_durations)
    if use_xfade:
        actual_slices_duration = append_xfade_chain(
            filter_parts, len(slices), clip_durations, xfade_id, trans_d,
            v_out="v_concat", a_out="a_concat"
        )
    else:
        actual_slices_duration = sum(clip_durations)
        concat_str = "".join(inputs) + f"concat=n={len(slices)}:v=1:a=1[v_concat][a_concat]"
        filter_parts.append(concat_str)

    # Handle background audio ducking if present
    if bg_music_path and os.path.exists(bg_music_path):
        filter_parts.append(f"[1:a]atrim=0:{actual_slices_duration},asetpts=PTS-STARTPTS[bg_raw]")
        filter_parts.append(f"[bg_raw][a_concat]sidechaincompress=threshold=0.15:ratio=4:attack=50:release=300,volume=0.15[bg_ducked]")
        filter_parts.append(f"[a_concat][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a]")

        filter_graph = "; ".join(filter_parts)
        command = f'ffmpeg -i "{video_path}" -stream_loop -1 -i "{bg_music_path}" -filter_complex "{filter_graph}" -map "[v_concat]" -map "[a]" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'
    else:
        filter_graph = "; ".join(filter_parts)
        command = f'ffmpeg -i "{video_path}" -filter_complex "{filter_graph}" -map "[v_concat]" -map "[a_concat]" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'

    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())
    if result.returncode != 0:
        # Fallback to simple clip cutting if complex filter fails
        if bg_music_path and os.path.exists(bg_music_path):
            command_fallback = f'ffmpeg -ss 0 -i "{video_path}" -stream_loop -1 -i "{bg_music_path}" -t {target_duration} -filter_complex "[1:a]volume=0.1[bg];[0:a][bg]amix=inputs=2:duration=first[a]" -map 0:v -map "[a]" -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'
        else:
            command_fallback = f'ffmpeg -ss 0 -i "{video_path}" -t {target_duration} -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" -c:v h264_nvenc -preset p4 -c:a aac -y "{output_path}"'
        subprocess.run(command_fallback, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())

    return output_path

def render_custom_reordered_cut(video_path, segments_map, order, subtitle_segments, style_preset, output_path, font_family="Montserrat", zoom_effect=1, bg_music_path=None, target_duration=None, transition=None, transition_duration=None):
    """
    Slices segments, concatenates them in custom order, shifts subtitles (including word timestamps), and burns them.

    transition / transition_duration:
        Optional boundary cross-fade between reordered parts (see make_semantic_cut).
        When a transition is applied, the subtitle timeline is recomputed using the
        same per-boundary overlap so burned captions stay in sync after the shorter,
        overlapped concatenation.
    """
    total_dur = get_video_duration(video_path)
    if total_dur <= 0:
        total_dur = 30.0

    hook = segments_map.get("hook", [0.0, min(5.0, total_dur)])
    demo = segments_map.get("demo", [hook[1], max(hook[1], total_dur - 5.0)])
    cta = segments_map.get("cta", [total_dur - 5.0, total_dur])

    orig_hook_dur = max(0.0, hook[1] - hook[0])
    orig_demo_dur = max(0.0, demo[1] - demo[0])
    orig_cta_dur = max(0.0, cta[1] - cta[0])

    # Calculate budgets for Hook, Demo, CTA based on target_duration
    if target_duration is not None:
        has_hook = any(p.lower().strip() == "hook" for p in order)
        has_demo = any(p.lower().strip() == "demo" for p in order)
        has_cta = any(p.lower().strip() == "cta" for p in order)

        if target_duration <= 5.0:
            hook_target = 1.5 if has_hook else 0.0
            cta_target = 1.5 if has_cta else 0.0
            demo_target = max(0.0, target_duration - hook_target - cta_target) if has_demo else 0.0
        elif target_duration <= 15.0:
            hook_target = min(orig_hook_dur, 3.0) if has_hook else 0.0
            cta_target = min(orig_cta_dur, 3.0) if has_cta else 0.0
            demo_target = max(0.0, target_duration - hook_target - cta_target) if has_demo else 0.0
        elif target_duration <= 30.0:
            hook_target = min(orig_hook_dur, 4.0) if has_hook else 0.0
            cta_target = min(orig_cta_dur, 4.0) if has_cta else 0.0
            demo_target = max(0.0, target_duration - hook_target - cta_target) if has_demo else 0.0
        else: # >= 60.0
            hook_target = min(orig_hook_dur, 5.0) if has_hook else 0.0
            cta_target = min(orig_cta_dur, 5.0) if has_cta else 0.0
            demo_target = max(0.0, target_duration - hook_target - cta_target) if has_demo else 0.0

        hook_budget = min(orig_hook_dur, hook_target)
        cta_budget = min(orig_cta_dur, cta_target)
        demo_budget = min(orig_demo_dur, demo_target)

        sum_budgets = hook_budget + cta_budget + demo_budget
        if sum_budgets < target_duration:
            if has_demo and orig_demo_dur > demo_budget:
                demo_budget = min(orig_demo_dur, demo_budget + (target_duration - sum_budgets))
                sum_budgets = hook_budget + cta_budget + demo_budget
            if sum_budgets < target_duration and has_hook and orig_hook_dur > hook_budget:
                hook_budget = min(orig_hook_dur, hook_budget + (target_duration - sum_budgets))
                sum_budgets = hook_budget + cta_budget + demo_budget
            if sum_budgets < target_duration and has_cta and orig_cta_dur > cta_budget:
                cta_budget = min(orig_cta_dur, cta_budget + (target_duration - sum_budgets))
    else:
        hook_budget = orig_hook_dur
        demo_budget = orig_demo_dur
        cta_budget = orig_cta_dur

    # Pass 1: resolve each part's source window and capture overlapping subtitles
    # with times relative to the start of that part (independent of the final
    # output placement, which depends on the transition overlap computed below).
    slices = []
    part_subs = []

    for part in order:
        part_cleaned = part.lower().strip()
        if part_cleaned == "hook":
            part_start = hook[0]
            part_end = hook[0] + hook_budget
            if subtitle_segments:
                smart_end = get_smart_cut_point(part_start, part_end, subtitle_segments, total_dur)
                if smart_end > part_start:
                    part_end = smart_end
        elif part_cleaned == "demo":
            part_start = demo[0]
            part_end = demo[0] + demo_budget
            if subtitle_segments:
                smart_end = get_smart_cut_point(part_start, part_end, subtitle_segments, total_dur)
                if smart_end > part_start:
                    part_end = smart_end
        elif part_cleaned == "cta":
            part_end = cta[1]
            part_start = max(cta[0], cta[1] - cta_budget)
            if subtitle_segments:
                smart_start = get_smart_cut_point(cta[0], part_start, subtitle_segments, total_dur)
                if smart_start < part_end:
                    part_start = smart_start
        else:
            continue

        part_dur = part_end - part_start
        if part_dur <= 0:
            continue

        slices.append((part_start, part_end, part_cleaned))

        # Capture overlapping subtitles with times relative to this part's start.
        subs_here = []
        if subtitle_segments:
            for seg in subtitle_segments:
                overlap_start = max(part_start, float(seg["start"]))
                overlap_end = min(part_end, float(seg["end"]))

                if overlap_end > overlap_start:
                    rel_words = []
                    has_words = "words" in seg
                    if has_words:
                        for w in seg["words"]:
                            w_start = max(overlap_start, float(w["start"]))
                            w_end = min(overlap_end, float(w["end"]))
                            if w_end > w_start:
                                rel_words.append({
                                    "word": w["word"],
                                    "start": w_start - part_start,
                                    "end": w_end - part_start
                                })
                    subs_here.append({
                        "rel_start": overlap_start - part_start,
                        "rel_end": overlap_end - part_start,
                        "text": seg["text"],
                        "has_words": has_words,
                        "rel_words": rel_words
                    })
        part_subs.append(subs_here)

    if not slices:
        # Fallback to direct copy
        ass_path = output_path + ".ass"
        generate_ass_file(subtitle_segments, style_preset, ass_path, font_family=font_family)
        return render_subtitles(video_path, ass_path, output_path)

    # Decide the transition plan from the final clip durations. This single
    # decision drives BOTH the subtitle timeline and the filter graph so they
    # never drift apart.
    clip_durations = [end - start for (start, end, _t) in slices]
    use_xfade, trans_d, xfade_id = plan_transition(transition, transition_duration, clip_durations)

    # Pass 2: place subtitles on the output timeline. Each xfade boundary pulls
    # the following part earlier by the transition duration (the overlap).
    shifted_subtitles = []
    out_offset = 0.0
    for i, (subs_here, dur) in enumerate(zip(part_subs, clip_durations)):
        for s in subs_here:
            new_seg = {
                "start": out_offset + s["rel_start"],
                "end": out_offset + s["rel_end"],
                "text": s["text"]
            }
            if s["has_words"]:
                new_seg["words"] = [
                    {
                        "word": w["word"],
                        "start": out_offset + w["start"],
                        "end": out_offset + w["end"]
                    }
                    for w in s["rel_words"]
                ]
            shifted_subtitles.append(new_seg)

        if use_xfade and i < len(clip_durations) - 1:
            out_offset += dur - trans_d
        else:
            out_offset += dur

    # Compile per-segment trim filters for reordering
    filter_parts = []
    inputs = []
    for idx, (start, end, segment_type) in enumerate(slices):
        if zoom_effect == 1 and segment_type in ("hook", "cta"):
            filter_parts.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"crop=w=iw/1.15:h=ih/1.15:x=(in_w-out_w)/2:y=(in_h-out_h)/2,scale=1080:1920,setsar=1[v{idx}]"
            )
        else:
            filter_parts.append(
                f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v{idx}]"
            )

        filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{idx}]")
        inputs.append(f"[v{idx}][a{idx}]")

    # Assemble segments: cross-fade transitions when requested, else a hard concat.
    if use_xfade:
        actual_slices_duration = append_xfade_chain(
            filter_parts, len(slices), clip_durations, xfade_id, trans_d,
            v_out="v_concat", a_out="a_concat"
        )
    else:
        actual_slices_duration = sum(clip_durations)
        concat_str = "".join(inputs) + f"concat=n={len(slices)}:v=1:a=1[v_concat][a_concat]"
        filter_parts.append(concat_str)

    # Render concatenated video to a temp file
    temp_dir = os.path.dirname(output_path)
    temp_video = os.path.join(temp_dir, f"temp_reorder_{os.path.basename(output_path)}")

    # Integrate sidechain background music ducking on reordered segments
    if bg_music_path and os.path.exists(bg_music_path):
        filter_parts.append(f"[1:a]atrim=0:{actual_slices_duration},asetpts=PTS-STARTPTS[bg_raw]")
        filter_parts.append(f"[bg_raw][a_concat]sidechaincompress=threshold=0.15:ratio=4:attack=50:release=300,volume=0.15[bg_ducked]")
        filter_parts.append(f"[a_concat][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a]")

        filter_graph = "; ".join(filter_parts)
        command = f'ffmpeg -i "{video_path}" -stream_loop -1 -i "{bg_music_path}" -filter_complex "{filter_graph}" -map "[v_concat]" -map "[a]" -c:v h264_nvenc -preset p4 -c:a aac -y "{temp_video}"'
    else:
        filter_graph = "; ".join(filter_parts)
        command = f'ffmpeg -i "{video_path}" -filter_complex "{filter_graph}" -map "[v_concat]" -map "[a_concat]" -c:v h264_nvenc -preset p4 -c:a aac -y "{temp_video}"'

    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=get_ffmpeg_env())
    if result.returncode != 0:
        raise Exception(f"FFmpeg error concatenating segments: {result.stderr}")

    # Generate shifted subtitles ASS file
    if subtitle_segments:
        ass_path = output_path + ".ass"
        generate_ass_file(shifted_subtitles, style_preset, ass_path, font_family=font_family)

        # Burn subtitles onto the reordered video
        try:
            render_subtitles(temp_video, ass_path, output_path)
        finally:
            # Clean up temp files
            if os.path.exists(temp_video):
                try:
                    os.remove(temp_video)
                except Exception:
                    pass
    else:
        # No subtitles to burn, just move temp_video to output_path
        import shutil
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        shutil.move(temp_video, output_path)

    return output_path
