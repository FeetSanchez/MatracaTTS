"""MatracaTTS - Gerador de Áudios Longos com edge-tts.

Aplicativo desktop em CustomTkinter para converter textos longos (com chunking)
em um único MP3 usando Edge TTS.
"""

# pylint: disable=duplicate-code

import asyncio
import os
import queue
import tempfile
import threading
from dataclasses import dataclass
from typing import Dict
from tkinter import StringVar, filedialog, messagebox

import customtkinter as ctk
import edge_tts
from edge_tts.exceptions import (
    EdgeTTSException,
    SkewAdjustmentError,
    WebSocketError,
)


# ==============================================================
# Configuração visual (requisito: dark + blue)
# ==============================================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ==============================================================
# Vozes reais (Edge TTS) por idioma/locale
#
# Importante sobre “faixa etária”:
# O Edge TTS/Voices do serviço não expõe um campo oficial e consistente
# de idade (jovem/idoso) para a maioria dos idiomas.
# Para cumprir o requisito sem inventar IDs, usamos vozes reais e mapeamos:
# - “Jovem” como uma voz adulta que costuma soar mais leve/conversacional.
# - “Envelhecida/Madura” como a voz adulta com timbre mais grave/autoridade.
# Quando o idioma tem poucas opções (ex.: só 1 voz masculina), algumas
# categorias apontam para o mesmo ID, e isso é documentado.
#
# “Infantil” só é incluído quando há pista oficial em tags (ex.: Cartoon/Cute).
# ==============================================================

VOICE_CATALOG = {
    # Português (Brasil)
    "Português (PT-BR)": {
        "locale": "pt-BR",
        "voices": {
            "Narrador Adulto (Masculino / PT-BR)": "pt-BR-AntonioNeural",
            "Narradora Adulta (Feminino / PT-BR)": "pt-BR-FranciscaNeural",
            # Jovem (fallback: única voz masculina disponível em PT-BR)
            # fallback: única voz M em PT-BR
            "Voz Jovem (Masculino / PT-BR)": "pt-BR-AntonioNeural",
            "Voz Jovem (Feminino / PT-BR)": "pt-BR-ThalitaMultilingualNeural",
            # Infantil: não há candidata detectável por tags em PT-BR (não exibir)
            # Envelhecida: sem classificação oficial → escolhemos a voz mais “grave/madura”.
            "Voz Envelhecida/Madura (Masculino / PT-BR)": "pt-BR-AntonioNeural",
            "Voz Envelhecida/Madura (Feminino / PT-BR)": "pt-BR-FranciscaNeural",
        },
        "has_child": False,
    },

    # Espanhol (Espanha)
    "Espanhol (ES-ES)": {
        "locale": "es-ES",
        "voices": {
            "Narrador Adulto (Masculino / ES-ES)": "es-ES-AlvaroNeural",
            "Narradora Adulta (Feminino / ES-ES)": "es-ES-ElviraNeural",
            # Jovem (fallback: única voz masculina disponível em ES-ES)
            # fallback: única voz M em ES-ES
            "Voz Jovem (Masculino / ES-ES)": "es-ES-AlvaroNeural",
            "Voz Jovem (Feminino / ES-ES)": "es-ES-XimenaNeural",
            # Infantil: não há candidata detectável por tags em ES-ES (não exibir)
            # Envelhecida: sem classificação oficial → voz mais madura/autoridade (mesmo ID)
            "Voz Envelhecida/Madura (Masculino / ES-ES)": "es-ES-AlvaroNeural",
            "Voz Envelhecida/Madura (Feminino / ES-ES)": "es-ES-ElviraNeural",
        },
        "has_child": False,
    },

    # Inglês (Estados Unidos)
    "Inglês (EN-US)": {
        "locale": "en-US",
        "voices": {
            "Narrador Adulto (Masculino / EN-US)": "en-US-GuyNeural",
            "Narradora Adulta (Feminino / EN-US)": "en-US-JennyNeural",
            "Voz Jovem (Masculino / EN-US)": "en-US-BrianNeural",
            "Voz Jovem (Feminino / EN-US)": "en-US-EmmaNeural",
            # Infantil: tags oficiais detectadas (Cartoon/Cute)
            "Voz Infantil (Feminino / EN-US)": "en-US-AnaNeural",
            # Envelhecida/Madura: sem “Senior” oficial → escolhemos voz com mais “autoridade”.
            "Voz Envelhecida/Madura (Masculino / EN-US)": "en-US-ChristopherNeural",
            "Voz Envelhecida/Madura (Feminino / EN-US)": "en-US-AriaNeural",
        },
        "has_child": True,
    },

    # Francês (França)
    "Francês (FR-FR)": {
        "locale": "fr-FR",
        "voices": {
            "Narrador Adulto (Masculino / FR-FR)": "fr-FR-HenriNeural",
            "Narradora Adulta (Feminino / FR-FR)": "fr-FR-DeniseNeural",
            "Voz Jovem (Masculino / FR-FR)": "fr-FR-RemyMultilingualNeural",
            "Voz Jovem (Feminino / FR-FR)": "fr-FR-EloiseNeural",
            # Infantil: não há candidata detectável por tags em FR-FR (não exibir)
            "Voz Envelhecida/Madura (Masculino / FR-FR)": "fr-FR-HenriNeural",
            "Voz Envelhecida/Madura (Feminino / FR-FR)": "fr-FR-VivienneMultilingualNeural",
        },
        "has_child": False,
    },

    # Alemão (Alemanha)
    "Alemão (DE-DE)": {
        "locale": "de-DE",
        "voices": {
            "Narrador Adulto (Masculino / DE-DE)": "de-DE-ConradNeural",
            "Narradora Adulta (Feminino / DE-DE)": "de-DE-KatjaNeural",
            "Voz Jovem (Masculino / DE-DE)": "de-DE-KillianNeural",
            "Voz Jovem (Feminino / DE-DE)": "de-DE-AmalaNeural",
            # Infantil: não há candidata detectável por tags em DE-DE (não exibir)
            "Voz Envelhecida/Madura (Masculino / DE-DE)": "de-DE-FlorianMultilingualNeural",
            "Voz Envelhecida/Madura (Feminino / DE-DE)": "de-DE-SeraphinaMultilingualNeural",
        },
        "has_child": False,
    },
}


# ==============================================================
# Utilitários de texto e MP3
# ==============================================================

MAX_INPUT_CHARS = 120_000
MAX_CHUNK_CHARS = 5_000


@dataclass(frozen=True)
class EdgeAudioSettings:
    """Parâmetros de áudio do Edge TTS em formato aceito pela API."""

    rate: str
    volume: str
    pitch: str


def split_text_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Divide automaticamente o texto em blocos de até max_chars.

    Para melhorar a qualidade do TTS sem “inventar funcionalidades”, tentamos
    quebrar em um espaço/quebra de linha próximo ao limite.
    """

    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + max_chars, n)
        if end < n:
            window_start = max(i, end - 500)
            cut = text.rfind(" ", window_start, end)
            cut_nl = text.rfind("\n", window_start, end)
            cut = max(cut, cut_nl)
            if cut > i + 50:
                end = cut
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        i = end
    return chunks


def _strip_id3v2_header(data: bytes) -> bytes:
    # Remove ID3v2 se existir no início do arquivo.
    if len(data) < 10:
        return data
    if data[0:3] != b"ID3":
        return data
    # Tamanho synchsafe 4 bytes
    size_bytes = data[6:10]
    tag_size = 0
    for b in size_bytes:
        tag_size = (tag_size << 7) | (b & 0x7F)
    total = 10 + tag_size
    if total >= len(data):
        return b""
    return data[total:]


def _strip_id3v1_trailer(data: bytes) -> bytes:
    # Remove ID3v1 (128 bytes no final) se existir.
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        return data[:-128]
    return data


def concatenate_mp3_safely(mp3_paths: list[str], output_path: str) -> None:
    # pylint: disable=too-many-branches
    """Concatena MP3 de forma robusta.

    Concatenação binária com remoção de headers ID3, o que evita duplicar
    metadados entre blocos.
    """

    if not mp3_paths:
        raise ValueError("Nenhum arquivo MP3 para concatenar")

    # Escreve de forma atômica para evitar arquivo final corrompido em caso de erro.
    output_dir = os.path.dirname(output_path) or os.getcwd()
    tmp_out = os.path.join(output_dir, f".{os.path.basename(output_path)}.tmp")

    try:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
    except OSError:
        # Se não der para limpar, seguimos; o os.replace abaixo ainda é atômico.
        pass

    # Concatenação binária com remoção de ID3 (sem FFmpeg).
    try:
        with open(tmp_out, "wb") as out:
            for idx, p in enumerate(mp3_paths):
                with open(p, "rb") as f:
                    data = f.read()
                if not data:
                    raise ValueError(f"Bloco de áudio vazio: {p}")
                data = _strip_id3v2_header(data) if idx > 0 else data
                data = _strip_id3v1_trailer(data)
                out.write(data)

        # Validação mínima (evita arquivo final vazio)
        if os.path.getsize(tmp_out) <= 0:
            raise RuntimeError("Falha ao concatenar: arquivo final vazio")

        os.replace(tmp_out, output_path)
    finally:
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass


# ==============================================================
# Aplicativo
# ==============================================================


class GeradorTTS(ctk.CTk):
    """Aplicativo GUI principal."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self):
        super().__init__()

        self.title("MatracaTTS - Gerador de Áudios Longos com edge-tts")
        self.geometry("980x700")
        self.minsize(880, 620)

        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._is_running = False

        self._voice_label_to_id: Dict[str, str] = {}

        # ===== Layout =====
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_header()
        self._build_top_controls()
        self._build_audio_controls()
        self._build_status_and_text()

        # Default selections
        first_lang = list(VOICE_CATALOG.keys())[0]
        self.combo_language.set(first_lang)
        self.on_language_change(first_lang)

        # Start polling UI queue
        self.after(100, self._drain_ui_queue)

    def _build_header(self):
        title = ctk.CTkLabel(
            self,
            text="MatracaTTS - Gerador de Áudios Longos com edge-tts",
            font=("Arial", 18, "bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))

        subtitle = ctk.CTkLabel(
            self,
            text=(
                "Motor: edge-tts. "
                "Passo a passo: 1) Cole o texto. 2) Selecione o idioma. 3) Selecione a voz. "
                "4) Ajuste pitch/volume/velocidade. 5) (Opcional) Clique em 'Prévia'. "
                "6) Clique em 'Gerar MP3'."
            ),
            justify="left",
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

    def _build_top_controls(self):
        top = ctk.CTkFrame(self)
        top.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        top.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(top, text="Idioma:").grid(
            row=0,
            column=0,
            padx=(12, 6),
            pady=12,
            sticky="w",
        )
        self.combo_language = ctk.CTkComboBox(
            top,
            values=list(VOICE_CATALOG.keys()),
            command=self.on_language_change,
        )
        self.combo_language.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="w")

        ctk.CTkLabel(top, text="Voz:").grid(
            row=0,
            column=2,
            padx=(0, 6),
            pady=12,
            sticky="w",
        )
        self.combo_voice = ctk.CTkComboBox(top, values=["(selecione um idioma)"])
        self.combo_voice.set("(selecione um idioma)")
        self.combo_voice.grid(row=0, column=3, padx=(0, 12), pady=12, sticky="ew")

        self.btn_preview = ctk.CTkButton(top, text="Prévia", command=self.on_preview)
        self.btn_preview.grid(row=0, column=4, padx=(0, 8), pady=12, sticky="e")

        self.btn_generate = ctk.CTkButton(top, text="Gerar MP3", command=self.on_click_generate)
        self.btn_generate.grid(row=0, column=5, padx=(0, 12), pady=12, sticky="e")

    def _build_audio_controls(self):
        controls = ctk.CTkFrame(self)
        controls.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))
        controls.grid_columnconfigure(1, weight=1)
        controls.grid_columnconfigure(4, weight=1)
        controls.grid_columnconfigure(7, weight=1)

        self._pitch_value = StringVar(value="0")
        self._rate_value = StringVar(value="1.00")
        self._volume_value = StringVar(value="100%")

        ctk.CTkLabel(controls, text="Pitch").grid(
            row=0,
            column=0,
            padx=(12, 6),
            pady=12,
            sticky="w",
        )
        self.slider_pitch = ctk.CTkSlider(
            controls,
            from_=-20,
            to=20,
            number_of_steps=80,
            command=self._on_pitch_change,
        )
        self.slider_pitch.set(0)
        self.slider_pitch.grid(row=0, column=1, padx=(0, 10), pady=12, sticky="ew")
        self.lbl_pitch = ctk.CTkLabel(controls, textvariable=self._pitch_value, width=50)
        self.lbl_pitch.grid(row=0, column=2, padx=(0, 12), pady=12, sticky="w")

        ctk.CTkLabel(controls, text="Velocidade").grid(
            row=0,
            column=3,
            padx=(0, 6),
            pady=12,
            sticky="w",
        )
        self.slider_rate = ctk.CTkSlider(
            controls,
            from_=0.25,
            to=4.0,
            number_of_steps=150,
            command=self._on_rate_change,
        )
        self.slider_rate.set(1.0)
        self.slider_rate.grid(row=0, column=4, padx=(0, 10), pady=12, sticky="ew")
        self.lbl_rate = ctk.CTkLabel(controls, textvariable=self._rate_value, width=60)
        self.lbl_rate.grid(row=0, column=5, padx=(0, 12), pady=12, sticky="w")

        ctk.CTkLabel(controls, text="Volume").grid(
            row=0,
            column=6,
            padx=(0, 6),
            pady=12,
            sticky="w",
        )
        self.slider_volume = ctk.CTkSlider(
            controls,
            from_=20.0,
            to=200.0,
            number_of_steps=180,
            command=self._on_volume_change,
        )
        self.slider_volume.set(100.0)
        self.slider_volume.grid(row=0, column=7, padx=(0, 10), pady=12, sticky="ew")
        self.lbl_volume = ctk.CTkLabel(controls, textvariable=self._volume_value, width=60)
        self.lbl_volume.grid(row=0, column=8, padx=(0, 12), pady=12, sticky="w")

    def _build_status_and_text(self):
        """Cria status, caixa de texto e barra de progresso."""
        self.status = ctk.CTkLabel(self, text="Pronto.")
        self.status.grid(row=4, column=0, sticky="w", padx=16, pady=(8, 0))

        self.txt_input = ctk.CTkTextbox(self, wrap="word")
        self.txt_input.grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 0))

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0.0)
        self.progress.grid(row=6, column=0, sticky="ew", padx=16, pady=(8, 14))

    def on_language_change(self, selected_language: str):
        """Callback do ComboBox de idioma: recarrega as vozes do idioma."""
        voices = list(VOICE_CATALOG[selected_language]["voices"].keys())
        self._voice_label_to_id = {
            label: VOICE_CATALOG[selected_language]["voices"][label] for label in voices
        }
        labels = voices if voices else ["(sem vozes)"]
        self.combo_voice.configure(values=labels)
        self.combo_voice.set(labels[0])

    @staticmethod
    def _pct_to_edge_delta_str(value_pct: float, clamp_min: int, clamp_max: int) -> str:
        delta = int(round(float(value_pct) - 100.0))
        delta = max(clamp_min, min(clamp_max, delta))
        return f"{delta:+d}%"

    @staticmethod
    def _pitch_to_edge_hz_str(pitch_slider_value: float) -> str:
        hz = int(round(float(pitch_slider_value)))
        hz = max(-20, min(20, hz))
        return f"{hz:+d}Hz"

    def _get_audio_settings(self) -> EdgeAudioSettings:
        rate_factor = float(self.slider_rate.get())
        rate_pct = max(10.0, min(400.0, rate_factor * 100.0))
        rate_str = self._pct_to_edge_delta_str(rate_pct, clamp_min=-90, clamp_max=200)
        volume_str = self._pct_to_edge_delta_str(
            float(self.slider_volume.get()),
            clamp_min=-90,
            clamp_max=100,
        )
        pitch_str = self._pitch_to_edge_hz_str(float(self.slider_pitch.get()))
        return EdgeAudioSettings(rate=rate_str, volume=volume_str, pitch=pitch_str)

    def _on_pitch_change(self, v: float):
        self._pitch_value.set(f"{int(round(float(v)))}")

    def _on_rate_change(self, v: float):
        self._rate_value.set(f"{float(v):.2f}")

    def _on_volume_change(self, v: float):
        self._volume_value.set(f"{int(round(float(v)))}%")

    def _set_running_state(self, running: bool):
        self._is_running = running
        state = "disabled" if running else "normal"
        self.btn_generate.configure(state=state)
        self.btn_preview.configure(state=state)
        self.combo_language.configure(state=state)
        self.combo_voice.configure(state=state)
        self.slider_pitch.configure(state=state)
        self.slider_rate.configure(state=state)
        self.slider_volume.configure(state=state)
        if not running:
            self.progress.set(0.0)

    def _queue_ui(self, event: str, payload: object):
        """Enfileira atualizações para o thread principal."""
        self._ui_queue.put((event, payload))

    def _drain_ui_queue(self):
        """Processa eventos de UI enfileirados pelo worker."""
        try:
            while True:
                event, payload = self._ui_queue.get_nowait()
                if event == "progress":
                    self.progress.set(float(payload))
                elif event == "status":
                    self.status.configure(text=str(payload))
                elif event == "done":
                    self._set_running_state(False)
                    messagebox.showinfo("Sucesso", f"Áudio MP3 gerado com sucesso:\n{payload}")
                elif event == "preview_done":
                    self._set_running_state(False)
                elif event == "error":
                    self._set_running_state(False)
                    messagebox.showerror("Erro", str(payload))
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_ui_queue)

    def on_click_generate(self):
        """Valida entrada e inicia a geração do MP3 em background."""
        if self._is_running:
            return

        text = self.txt_input.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Aviso", "O texto está vazio.")
            return

        if len(text) > MAX_INPUT_CHARS:
            messagebox.showerror(
                "Erro",
                f"Texto excede o limite de {MAX_INPUT_CHARS} caracteres (atual: {len(text)}).",
            )
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".mp3",
            filetypes=[("Arquivo MP3", "*.mp3")],
            title="Salvar MP3",
        )
        if not save_path:
            return

        selected_voice_label = self.combo_voice.get()
        voice_id = self._voice_label_to_id.get(selected_voice_label)
        if not voice_id:
            messagebox.showerror("Erro", "Seleção de voz inválida.")
            return

        settings = self._get_audio_settings()

        chunks = split_text_into_chunks(text, MAX_CHUNK_CHARS)
        if not chunks:
            messagebox.showwarning("Aviso", "Nenhum conteúdo válido para converter.")
            return

        self._set_running_state(True)
        msg = f"Iniciando… {len(chunks)} bloco(s) de até {MAX_CHUNK_CHARS} caracteres."
        self._queue_ui("status", msg)
        self._queue_ui("progress", 0.0)

        # Executa em thread para não travar a UI
        self._worker_thread = threading.Thread(
            target=self._run_worker,
            args=(chunks, voice_id, save_path, settings),
            daemon=True,
        )
        self._worker_thread.start()

    def _run_worker(
        self,
        chunks: list[str],
        voice_id: str,
        save_path: str,
        settings: EdgeAudioSettings,
    ):
        """Worker: executa síntese (asyncio) fora da UI."""
        try:
            asyncio.run(self._async_generate_mp3(chunks, voice_id, save_path, settings))
            self._queue_ui("done", save_path)
        except (
            EdgeTTSException,
            WebSocketError,
            SkewAdjustmentError,
            OSError,
            RuntimeError,
            ValueError,
        ) as e:
            self._queue_ui("error", f"Falha ao gerar áudio: {e}")

    async def _async_generate_mp3(
        self,
        chunks: list[str],
        voice_id: str,
        save_path: str,
        settings: EdgeAudioSettings,
    ):
        """Gera MP3 temporários por chunk e concatena em um único arquivo."""
        # Gera MP3 temporários por bloco e depois concatena
        with tempfile.TemporaryDirectory(prefix="edge_tts_chunks_") as tmpdir:
            temp_mp3s: list[str] = []
            total = len(chunks)

            for idx, chunk in enumerate(chunks, start=1):
                self._queue_ui("status", f"Convertendo bloco {idx}/{total}…")
                temp_path = os.path.join(tmpdir, f"chunk_{idx:04d}.mp3")

                communicate = edge_tts.Communicate(
                    chunk,
                    voice_id,
                    rate=settings.rate,
                    volume=settings.volume,
                    pitch=settings.pitch,
                )
                await communicate.save(temp_path)
                temp_mp3s.append(temp_path)

                self._queue_ui("progress", idx / total)

            self._queue_ui("status", "Concatenando blocos em um único MP3…")
            concatenate_mp3_safely(temp_mp3s, save_path)
            self._queue_ui("status", "Concluído.")

    def on_preview(self):
        """Gera uma prévia curta e abre no player padrão do Windows."""
        if self._is_running:
            return

        full_text = self.txt_input.get("1.0", "end-1c").strip()
        if not full_text:
            messagebox.showwarning("Aviso", "O texto está vazio.")
            return

        selected_voice_label = self.combo_voice.get()
        voice_id = self._voice_label_to_id.get(selected_voice_label)
        if not voice_id:
            messagebox.showerror("Erro", "Seleção de voz inválida.")
            return

        preview_text = full_text[:450]
        chunks = split_text_into_chunks(preview_text, MAX_CHUNK_CHARS)
        if not chunks:
            messagebox.showwarning("Aviso", "Nenhum conteúdo válido para prévia.")
            return

        settings = self._get_audio_settings()

        self._set_running_state(True)
        self._queue_ui("status", "Gerando prévia…")
        self._queue_ui("progress", 0.0)

        threading.Thread(
            target=self._preview_worker,
            args=(chunks[0], voice_id, settings),
            daemon=True,
        ).start()

    def _preview_worker(self, text_chunk: str, voice_id: str, settings: EdgeAudioSettings):
        """Worker: sintetiza a prévia e abre no player."""
        try:
            async def _run() -> str:
                communicate = edge_tts.Communicate(
                    text_chunk,
                    voice_id,
                    rate=settings.rate,
                    volume=settings.volume,
                    pitch=settings.pitch,
                )
                fd, path = tempfile.mkstemp(prefix="gomezztts_preview_", suffix=".mp3")
                os.close(fd)
                await communicate.save(path)
                return path

            path = asyncio.run(_run())
            self._queue_ui("status", "Prévia gerada. Abrindo no player padrão…")
            self._queue_ui("progress", 1.0)
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except OSError:
                self._queue_ui("status", f"Prévia gerada em: {path}")
            self._queue_ui("preview_done", None)
        except (
            EdgeTTSException,
            WebSocketError,
            SkewAdjustmentError,
            OSError,
            RuntimeError,
            ValueError,
        ) as e:
            self._queue_ui("error", f"Falha ao gerar prévia: {e}")


if __name__ == "__main__":
    app = GeradorTTS()
    app.mainloop()
