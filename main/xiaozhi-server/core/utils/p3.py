import os
import struct
import numpy as np
import opuslib_next
from pydub import AudioSegment

TAG = "core.utils.p3"

def encode_audio_to_p3_file(input_audio_path: str, output_p3_path: str, sample_rate: int = 16000) -> bool:
    """
    将任意格式音频文件（MP3/WAV/M4A/FLAC/AAC等）预转码为小智原生 .p3 (Opus 16kHz 60ms) 文件。
    预转码后播放消耗 0% CPU，且完全消除运行时转码引发的 GIL 锁竞争与音频跳帧。
    """
    try:
        file_type = os.path.splitext(input_audio_path)[1].lstrip(".")
        audio = AudioSegment.from_file(input_audio_path, format=file_type, parameters=["-nostdin"])
        audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
        raw_data = audio.raw_data

        encoder = opuslib_next.Encoder(sample_rate, 1, opuslib_next.APPLICATION_AUDIO)
        frame_duration = 60  # 60ms per frame
        frame_size = int(sample_rate * frame_duration / 1000)  # 960 samples

        os.makedirs(os.path.dirname(os.path.abspath(output_p3_path)), exist_ok=True)
        temp_output = output_p3_path + ".tmp"

        with open(temp_output, "wb") as f:
            for i in range(0, len(raw_data), frame_size * 2):
                chunk = raw_data[i : i + frame_size * 2]
                if len(chunk) < frame_size * 2:
                    chunk += b"\x00" * (frame_size * 2 - len(chunk))
                
                np_frame = np.frombuffer(chunk, dtype=np.int16)
                opus_data = encoder.encode(np_frame.tobytes(), frame_size)
                
                # 写入 4 字节头部 [1字节类型(1=Opus), 1字节保留, 2字节数据长度] + Opus数据
                header = struct.pack(">BBH", 1, 0, len(opus_data))
                f.write(header)
                f.write(opus_data)

        if os.path.exists(output_p3_path):
            os.remove(output_p3_path)
        os.rename(temp_output, output_p3_path)
        return True
    except Exception as e:
        print(f"[{TAG}] encode_audio_to_p3_file error: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False

def decode_opus_from_file_stream(input_file: str, callback, abort_check=None):
    """
    从 .p3 文件中流式读取 Opus 帧并回调，支持 0 延迟实时打断
    """
    with open(input_file, "rb") as f:
        while True:
            if abort_check and abort_check():
                break
            header = f.read(4)
            if not header or len(header) < 4:
                break
            _, _, data_len = struct.unpack(">BBH", header)
            opus_data = f.read(data_len)
            if len(opus_data) != data_len:
                break
            callback(opus_data)

def decode_opus_from_file(input_file):
    opus_datas = []
    total_frames = 0
    sample_rate = 16000
    frame_duration_ms = 60

    with open(input_file, "rb") as f:
        while True:
            header = f.read(4)
            if not header or len(header) < 4:
                break
            _, _, data_len = struct.unpack(">BBH", header)
            opus_data = f.read(data_len)
            if len(opus_data) != data_len:
                break
            opus_datas.append(opus_data)
            total_frames += 1

    total_duration = (total_frames * frame_duration_ms) / 1000.0
    return opus_datas, total_duration

def decode_opus_from_bytes(input_bytes):
    import io
    opus_datas = []
    total_frames = 0
    sample_rate = 16000
    frame_duration_ms = 60

    f = io.BytesIO(input_bytes)
    while True:
        header = f.read(4)
        if not header or len(header) < 4:
            break
        _, _, data_len = struct.unpack(">BBH", header)
        opus_data = f.read(data_len)
        if len(opus_data) != data_len:
            break
        opus_datas.append(opus_data)
        total_frames += 1

    total_duration = (total_frames * frame_duration_ms) / 1000.0
    return opus_datas, total_duration
