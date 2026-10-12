# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React, Vite, TypeScript, FastAPI, isolated Python faster-whisper worker, SQLite, Docker Compose. Confirmed by the user. Code-first implementation.

## Users

One local user transcribing audio and video recordings and correcting subtitles with synchronized playback.

## Product Purpose

Upload a recording, transcribe locally, edit text and timing against playback, and export SRT, TXT, JSON, or VTT.

## Operating Context

Docker Desktop on Windows. Portable CPU setup and optional NVIDIA GTX 1060 with 6 GB VRAM. Up to 2 GB and 2 hours per source. Internet is needed for initial images and model downloads.

## Capabilities and Constraints

File uploads only. Multilingual small default with base and medium choices, language override, full subtitle editing, waveform, undo/redo, serialized autosave, local drafts, persistent history, cancellation, retry, and explicit CPU recovery. Localhost only. Retain data until manual deletion.

## Brand Commitments

Quiet light workspace, dark playback surface, system fonts, monospaced times, one blue accent. Playback left, transcript right, shared timeline below. Short plain labels without decorative symbols. Preserve transcript punctuation and Unicode.

## Product Principles

- Editing and playback share one time origin.
- Version conflicts and runtime failures explain the fix.
- An unavailable worker must not prevent the web interface from loading.
- Preserve edits before exporting or navigating away.

## Accessibility & Inclusion

Keyboard-accessible controls, visible focus, readable contrast, responsive stacked layout, Unicode text, and reduced motion.
