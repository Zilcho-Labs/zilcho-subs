# Third-party notices and acknowledgements

Zilcho Subs is developed by Zilcho Labs. Zilcho's own code is licensed under MIT; see LICENSE. That license does not replace any third-party license. Upstream authors retain their copyright and credit. The application has an About & credits page with local copies of primary license texts and upstream links.

## Primary components

| Component / author | Purpose | License reference |
|---|---|---|
| OpenAI Whisper / OpenAI | Original speech model family and research | [MIT](zilcho_subs/static/licenses/whisper.txt), [upstream](https://github.com/openai/whisper) |
| faster-whisper 1.2.1 / SYSTRAN | Local Whisper inference | [MIT](zilcho_subs/static/licenses/faster-whisper.txt), [upstream version](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1) |
| CTranslate2 / SYSTRAN and OpenNMT authors | Speech and translation inference runtime | [MIT](zilcho_subs/static/licenses/ctranslate2.txt), [upstream](https://github.com/OpenNMT/CTranslate2) |
| Argos Translate 1.11.0 / Argos Open Technologies, LLC | Compatible packs and adapted whole-cue algorithm (full runtime not installed) | [MIT option](zilcho_subs/static/licenses/argos-translate.txt), [upstream version](https://github.com/argosopentech/argos-translate/tree/v1.11.0) |
| FFmpeg 8.1.3 / FFmpeg contributors | Media inspection and audio extraction, dynamically linked with PyAV | [LGPL-2.1-or-later](https://ffmpeg.org/legal.html) for the controlled container build; full source, configuration and license texts accompany the paired release materials |
| Silero VAD / Silero Team | Small voice-activity model bundled by faster-whisper | [MIT](zilcho_subs/static/licenses/silero-vad.txt), [upstream](https://github.com/snakers4/silero-vad) |
| Audio Spectrogram Transformer / Yuan Gong, MIT CSAIL; ONNX conversion by the ONNX Community | Optional sound-effect captions, downloaded by the user (not bundled) | [BSD-3-Clause](zilcho_subs/static/licenses/ast.txt), [model](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593), [ONNX revision](https://huggingface.co/onnx-community/ast-finetuned-audioset-10-10-0.4593-ONNX/tree/2fe67046c486b8b8bc90a7b469239b2dec822f72) |
| AudioSet ontology / Google | Sound label names used for captions | [CC BY 4.0](https://research.google.com/audioset/download.html) |
| Rico Sennrich; copyright Joerg Tiedemann (2004–2020) | Vendored legacy BPE implementation from Argos 1.11.0 | [MIT](zilcho_subs/static/licenses/argos-bpe.txt); full original header retained in zilcho_subs/vendor/argos_bpe.py |
| FastAPI / Sebastian Ramirez and contributors | Web API | [MIT](zilcho_subs/static/licenses/fastapi.txt), [upstream](https://github.com/fastapi/fastapi) |
| Uvicorn / Encode OSS Ltd and contributors | Application server | [BSD-3-Clause](zilcho_subs/static/licenses/uvicorn.txt), [upstream](https://github.com/encode/uvicorn) |

These are the main visible components, not the entire dependency tree. Python, PyAV, NumPy, tokenizers, ONNX Runtime, text processing libraries, C/C++ runtime libraries, Debian packages and transitive dependencies also retain their own notices and terms. For unpinned dependencies the installed release inventory takes precedence over this high-level table. Verify bundled upstream license texts against installed versions before public release.

## Notices in the container

Each image build runs tools/collect_licenses.py. It records installed Python/Debian versions, available Python license/notice files and package metadata, Debian copyright/common-license files, and FFmpeg build/license output under /usr/share/zilcho-subs/dependencies. The top-level LICENSE and this file are also shipped under /usr/share/zilcho-subs. This preserves available evidence; metadata can be incomplete and collection alone does not establish permission to distribute. Review entries missing notices and bundled native-library notices before publishing.

## Downloaded models and language packs

Whisper speech weights and translation packs are not bundled in the image. Users download them explicitly. The small Silero VAD asset is included by faster-whisper and retains its MIT attribution; ONNX Runtime executes it locally. The [Whisper project](https://github.com/openai/whisper#license) states that its code and weights use MIT; the [SYSTRAN medium conversion](https://huggingface.co/Systran/faster-whisper-medium) also declares MIT. Verify the exact model revision, license and tokenizer assets for every supported download before redistributing weights. Argos [maintainer clarification](https://github.com/argosopentech/argos-translate/issues/533) describes their model binaries as MIT/CC0, but check the actual selected package and provider, including future third-party catalog entries. Do not assume one library's license licenses every possible downloaded asset or training dataset.

## Distribution and source availability

The Docker image contains FFmpeg binaries plus other separately licensed components. Do not describe the complete image as MIT-only. FFmpeg's applicable LGPL/GPL terms depend on build configuration, including native libraries in binary wheels such as PyAV. Review corresponding-source and build/relink obligations for the exact release; merely linking to an upstream homepage or publishing Zilcho's Python files is not a substitute. Matching source archives, patches and required build information must be provided through an appropriate license-compliant mechanism before distributing a release image. See docs/RELEASE.md for the release gate. No public image has been published by this change.

## Independence

Thanks to all upstream maintainers and contributors. Attribution does not imply endorsement or affiliation with OpenAI, SYSTRAN, Argos, FFmpeg, Unraid or Jellyfin. The application runs local models and does not use the OpenAI API. User media and resulting subtitles have separate rights; the software license does not grant rights to distribute shows, audio or translated subtitles.

## Exact-version notice review

The four wheel-notice gaps from run 43738622700 were reviewed against exact upstream tags. `third_party/upstream-notices/review.json` records repository commit/tree references, searched filename terms, all collected license/notice paths, SHA256 hashes and unexpanded Git submodules. The untruncated repository trees for CTranslate2 4.9.0, FlatBuffers 25.12.19, SentencePiece 0.2.3 and Tokenizers 0.23.3 contained no filenames matching NOTICE. Their actual license texts, including matching third-party license files visible in the trees, are preserved in that directory and copied into the image. CTranslate2 is MIT; the other three primary licenses are Apache-2.0. The app links the primary license texts.

This resolves the named upstream-tree NOTICE question at these versions, not all native/dependency licensing. The controlled build initializes only the required CTranslate2 submodules (spdlog, cpu_features and recursive ruy dependencies), preserves their sources, exact revisions and license files, and disables CUDA, cuDNN, MKL and DNNL. The older opaque wheel is not used. Future version changes require a fresh review. See docs/ARTIFACT-REVIEW.md and docs/RELEASE.md. No statement here clears the whole image for public distribution.

## Controlled release sources

The container compiles FFmpeg 8.1.3 with GPL/nonfree and external autodetection disabled; PyAV 18.1.0 links dynamically to that same prefix. NumPy 2.4.6 and CTranslate2 4.9.0 use Debian OpenBLAS. Sources are checksum-pinned in third_party/native-lock.json, FFmpeg's release signature is verified, and configure/compiler/build records are retained. Runtime mappings and hashes must confirm this for each candidate. No PyTorch, minisbd, Stanza, spaCy or Argos runtime is installed.

Candidate preparation mirrors exact Debian source packages (including upstream archives and Debian patches), Python sdists/upstream sources, Rust Cargo.lock sources and ONNX Runtime's pinned Eigen source. Full available license/copyright/NOTICE texts accompany the candidate image and source archive. Snapshot SHA1 identifiers and DSC SHA256 values are checked; Debian OpenPGP verification is not claimed. The source bundle includes the application, build scripts and logs needed to understand the builds. Dynamic libraries can be replaced by rebuilding or mounting compatible replacements; Zilcho imposes no reverse-engineering or relinking restriction.

Public distribution still requires approval and durable delivery of the paired sources/notices. Each preserved image embeds /usr/share/zilcho-subs/source-delivery.json with its source archive hash and proposed download URL. An unverified proposed URL is not a delivered source package. Historical GPL-enabled Debian FFmpeg/PyAV-wheel images are not cleared by this newer build.
