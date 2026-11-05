# Credits

Hermes Voice is a thin layer. The work below is done by these projects, used as-is; none of their code is
copied into this repository.

| Project | Link | What it provides here |
|---|---|---|
| Hermes Agent (Nous Research) | https://github.com/NousResearch/hermes-agent | The agent: profiles, SOUL.md, memory, tools, and the OpenAI-compatible API server with `X-Hermes-Session-Id` sessions that hermes_voice talks to. |
| Home Assistant | https://www.home-assistant.io / https://github.com/home-assistant/core | Assist pipelines, the conversation agent API this integration implements, the config/options flow framework. |
| pytest-homeassistant-custom-component | https://github.com/MatthewFlamm/pytest-homeassistant-custom-component | Test harness used by `tests/`. |
| ESPHome | https://esphome.io | The device protocol echo-dot-assist speaks to Home Assistant; the micro_wake_word model manifest format. |
| echo-dot-assist (Gamer92000) | https://github.com/Gamer92000/echo-dot-assist | Turns an Echo Dot 2/3, Echo 2 (and some Echo Show models) into a local Assist satellite discovered as an ESPHome device. The device path this project documents. MIT. |
| amonet, kamakiri, kaeru (R0rt1z2) | https://github.com/R0rt1z2/amonet · https://github.com/R0rt1z2/kamakiri · https://github.com/R0rt1z2/kaeru | The bootloader unlock work that echo-dot-assist (and TECHO5) build on to get into the Echo. |
| TECHO5 (HuskerMinion) | https://github.com/HuskerMinion/techo5 | A separate community firmware that replaces Fire OS on older Echo devices with Linux and makes them Home Assistant voice satellites. An alternative device path; not used or tested by this project. |
| microWakeWord (Kevin Ahrendt) | https://github.com/kahrendt/microWakeWord | Wake word model architecture and training code used by `wakeword/`. |
| piper-sample-generator, Piper (Rhasspy / Michael Hansen) | https://github.com/rhasspy/piper-sample-generator · https://github.com/rhasspy/piper | Synthetic "hey hermes" training samples (`wakeword/`); Piper is also a common text-to-speech engine for Assist. |
| Whisper (OpenAI) / faster-whisper | https://github.com/openai/whisper · https://github.com/SYSTRAN/faster-whisper | Common speech-to-text engines for Assist (e.g. via Wyoming). |
| Wyoming protocol | https://github.com/OHF-Voice/wyoming | How Home Assistant talks to local speech-to-text / text-to-speech / wake word services. |
| Wyoming Whisper and Piper servers (Rhasspy / Open Home Foundation) | https://hub.docker.com/r/rhasspy/wyoming-whisper · https://hub.docker.com/r/rhasspy/wyoming-piper | The speech-to-text and text-to-speech containers `speech_setup.py` runs (pinned `3.8.1` / `2.5.2`). Used as-is. |
| Parakeet TDT 0.6B v2 (NVIDIA) via sherpa-onnx (k2-fsa) | https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2 · https://github.com/k2-fsa/sherpa-onnx | The English speech-to-text model the Whisper container picks by default; downloaded by the container, not included here. |

Training data used by `wakeword/` (downloaded at run time, never redistributed): MIT environmental impulse
responses (`davidscripka/MIT_environmental_impulse_responses`), AudioSet (`agkphysics/AudioSet`), Free Music
Archive xsmall (`mchl914/fma_xsmall`) and microWakeWord negative features (`kahrendt/microwakeword`) on
Hugging Face. They carry mixed licenses; microWakeWord's notebook says models trained with them are for
non-commercial personal use.
