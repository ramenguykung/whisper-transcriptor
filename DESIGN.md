---
name: Whisper Transcriptor
description: Quiet local transcription and subtitle editing workspace.
colors:
  accent: "#245bd6"
  accent-hover: "#1947ac"
  selected: "#e9f0ff"
  active: "#f2f6ff"
  canvas: "#f3f4f6"
  surface: "#ffffff"
  panel: "#f7f8fa"
  hover: "#e9edf2"
  pressed: "#dce3ed"
  disabled: "#edf0f4"
  text: "#192332"
  muted: "#526072"
  label: "#35465c"
  placeholder: "#596677"
  disabled-text: "#677384"
  border: "#cdd4df"
  row-separator: "#dce1e9"
  playback: "#101722"
  playback-text: "#e9edf2"
  playback-muted: "#abbad0"
  waveform: "#697d98"
  error: "#a52b31"
  error-hover: "#84242a"
  success: "#226742"
  warning: "#7b4523"
  warning-surface: "#fff5e9"
  error-surface: "#fff6f5"
  text-selection: "#dce7ff"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.25
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.25
  heading:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.45
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  control: "6px"
  flat: "0"
spacing:
  xs: "4px"
  compact: "6px"
  sm: "8px"
  control: "10px"
  md: "12px"
  lg: "16px"
  panel: "20px"
  xl: "24px"
  section: "32px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-active:
    backgroundColor: "{colors.pressed}"
  button-disabled:
    backgroundColor: "{colors.disabled}"
    textColor: "{colors.disabled-text}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-primary-disabled:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.disabled-text}"
  button-danger:
    backgroundColor: "{colors.error}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "7px 12px"
  button-danger-hover:
    backgroundColor: "{colors.error-hover}"
  button-small:
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "5px 8px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "7px 9px"
  time-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.mono}"
    rounded: "{rounded.control}"
    padding: "4px 6px"
    width: "117px"
  library-entry:
    textColor: "{colors.text}"
    rounded: "{rounded.flat}"
    padding: "12px 16px"
    width: "100%"
  library-entry-current:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.accent-hover}"
  media-surface:
    backgroundColor: "{colors.playback}"
    textColor: "{colors.playback-text}"
    rounded: "{rounded.control}"
  transcript-row:
    backgroundColor: "{colors.surface}"
    padding: "10px 16px 5px"
    height: "148px"
  transcript-row-active:
    backgroundColor: "{colors.active}"
  transcript-row-selected:
    backgroundColor: "{colors.selected}"
  timeline-panel:
    backgroundColor: "{colors.panel}"
    padding: "16px 24px"
---

# Design System: Whisper Transcriptor

## Overview

**Creative North Star: "Local workspace"**

The code-first transcription workspace is an Operate surface: quiet, light, compact, and focused on listening and correcting text. The user-pinned visual world combines a light gray workspace, a dark playback surface, system fonts, monospaced times, and one blue accent.

Flat panels and separators organize the work. The visual system uses plain labels, readable text, and visible interaction states. The implementation is the reference for measured values; the source files are `web/src/style.css`, `web/src/App.tsx`, `web/src/Editor.tsx`, `web/src/SegmentList.tsx`, and `web/src/Waveform.tsx`.

**Key Characteristics:**

- Quiet light workspace with a dark player.
- System sans type and tabular monospaced times.
- One blue accent for action, selection, and focus.
- Compact controls and flat panel separators.
- Responsive playback, waveform, and transcript layout.

## Colors

The palette combines cool gray surfaces and dark text with blue interaction states. Frontmatter values are normative.

### Primary

- **Blue accent** (`accent`): primary actions, keyboard focus, native checkbox and range accents, waveform progress, and timing handles.
- **Deep blue** (`accent-hover`): primary-button hover and the selected file label.
- **Selection wash** (`selected`): selected files and transcript rows.
- **Playback wash** (`active`): the transcript row covering the current playback time.
- **Text selection wash** (`text-selection`): native selected text.

### Neutral

- **Workspace gray** (`canvas`), **white surface** (`surface`), and **panel gray** (`panel`): page background, editing surfaces, and supporting tool panels.
- **Hover gray** (`hover`), **pressed gray** (`pressed`), and **disabled gray** (`disabled`): neutral control states.
- **Ink** (`text`), **muted ink** (`muted`), and **label ink** (`label`): primary content, supporting text, and field labels.
- **Placeholder ink** (`placeholder`) and **disabled ink** (`disabled-text`): secondary input and unavailable-action states.
- **Panel separator** (`border`) and **row separator** (`row-separator`): one-pixel structural lines.
- **Playback dark** (`playback`), **playback light** (`playback-text`), and **playback muted** (`playback-muted`): media surface and its audio labels.
- **Waveform gray** (`waveform`): unplayed waveform peaks.

### Status

- **Error red** (`error`) and **deep error red** (`error-hover`): errors and the explicit destructive action.
- **Success green** (`success`): ready runtime, completed files, and saved edits.
- **Warning brown** (`warning`): runtime attention status.
- **Warning wash** (`warning-surface`) and **error wash** (`error-surface`): recovery and error notices.

**The One Accent Rule.** Use blue for interactive emphasis; reserve red, green, and brown for their existing status meanings.

## Typography

**Body and heading font:** the system sans stack in frontmatter.
**Time and diagnostic font:** the system monospace stack in frontmatter.

The type remains familiar and compact. There are no downloaded fonts. Timing controls use tabular numerals; transcript text retains Unicode and punctuation.

### Hierarchy

- **Display:** the welcome heading; reduces to 26 px at the narrow breakpoint.
- **Title:** the source filename in the editor header; it truncates visually with the full name available as a title.
- **Heading:** panel labels. Processing headings use 24 px; the error heading uses 18 px.
- **Body:** transcript text and normal controls. Welcome prose uses 15 px with 1.6 line height, then 14 px on narrow screens; its line length is capped at 60ch.
- **Label:** field labels, hints, source metadata, playback controls, and save status. Dense timing and timeline labels use 11 px where defined.
- **Mono:** playback times, timing fields, and diagnostics. File-list durations use 11 px.

Buttons use medium weight (500). The brand uses 16 px and weight 650, reducing to 14 px at the narrow breakpoint.

**The Time Legibility Rule.** Keep time values monospaced and tabular; use system sans type for editable transcript text.

## Layout

The shell fills at least the viewport height without forcing a minimum body width. Its top bar is 60 px tall with 24 px horizontal padding. The file library occupies 260 px beside a flexible main area.

On wide screens, the editor grid places playback on the left and transcript on the right, with the waveform spanning both below. Columns are `minmax(0, 1fr)` and `minmax(460px, 1.2fr)`. Playback and header padding normally use 24 px; transcript toolbars and rows use 16 px horizontal padding.

Responsive rules apply at these inclusive maximum widths:

| Breakpoint | Implemented change |
| --- | --- |
| 1280 px | Library becomes 240 px; editor columns become `minmax(0, 0.9fr)` and `minmax(420px, 1.1fr)`; playback padding tightens and tools wrap. |
| 1080 px | Editor becomes one column in playback, waveform, transcript order. Header actions wrap; waveform tools move to a full row. |
| 760 px | Top bar becomes 56 px. The Files button reveals the library inline. Main content uses a single column, source actions span a row, and most panel padding becomes 16 px. |

Transcript rows are virtualized at 148 px each. The scroll area uses `clamp(420px, calc(100dvh - 410px), 740px)` on wide screens, 520 px at the middle breakpoint, and `min(65dvh, 560px)` with a 370 px minimum at the narrow breakpoint. Text fields fill their row; time fields are 117 px wide, reducing to 110 px on narrow screens.

Use the spacing scale for recurring gaps and padding. Controls use compact spacing; sections use the larger steps. Long source names wrap or truncate within flexible containers rather than widening the page.

## Elevation & Depth

The interface uses no shadows. White and gray tonal layers, one-pixel separators, and the dark media surface establish structure. The player is the principal dark region; supporting panels stay light.

**The Flat Panels Rule.** Separate workspace regions with tone and borders rather than decorative cards or elevation.

## Shapes

Controls, upload picker, media surface, command blocks, and loading placeholders use the control radius. Workspace panels and file navigation rows remain square. Inputs and controls have one-pixel borders; the upload picker uses a dashed border until a file is selected. The media surface clips its content to its corners.

## Components

### Buttons

Compact, plain-word actions. Primary, neutral, and danger variants share the control radius and standard padding. Small buttons use the compact size in frontmatter.

Neutral hover and press states use tonal grays. Primary hover uses deep blue; danger hover uses deep red. Disabled neutral and primary buttons use muted text and gray backgrounds. Background and text colors transition over 150 ms with the CSS default easing.

### Inputs / Fields

White fields with a panel-separator stroke. Inputs, selects, and textareas inherit the system font; timing inputs use the mono role. Native checkboxes and range inputs use the blue accent. Transcript textareas are 66 px high, use a translucent white background, and keep a blue caret.

Keyboard focus uses a two-pixel blue outline with a three-pixel offset on controls, links, disclosures, and focusable transcript containers. Timing errors appear beneath the row in red with an accessible alert.

### Navigation

The top bar combines the brand, local-workspace label, runtime text, and narrow-screen Files toggle. The file library uses full-width square rows, neutral hover, and a blue selection wash. Each row carries the filename, job status, and optional monospaced duration. The library becomes an inline disclosure at the narrow breakpoint.

### Playback Surface

A dark, softly cornered media well contains native browser video or audio controls. Audio files show the filename and a muted recording label. Wide-screen media wells have a 240 px minimum height; they reduce to 200 px at the middle breakpoint and 190 px at the narrow breakpoint. Video keeps a 16:10 aspect ratio and is capped at 420 px, then 340 px at the middle breakpoint.

### Transcript Rows

Flat, separated rows expose selection, start and end times, Seek, and editable text. Active playback and manual selection have distinct pale blue backgrounds. The selected row takes precedence when both states apply. Follow playback does not move the list while a row is being edited.

### Timeline

The shared gray timeline panel carries current time, pan, playback-centering, Fit, and Zoom controls. Waveform peaks are 100 px high with a 22 px time ruler. Progress and draggable region handles use blue; selected regions use a stronger translucent blue than unselected regions. Dense views show up to 180 regions and ask the user to zoom in when needed.

### Notices and Loading

Recovery notices occupy flat inline strips with warning or error washes and a separator. Save conflicts keep the draft and its recovery controls visible. Diagnostics live behind plain Details or Runtime details disclosures. Processing shows the actual phase and available progress; loading placeholders stay static.

The reduced-motion preference removes transitions and uses automatic scrolling behavior.

## Do's and Don'ts

### Do:

- **Do** keep the light workspace, dark playback surface, and single blue accent.
- **Do** use system fonts, tabular monospaced times, and short plain labels.
- **Do** preserve Unicode, punctuation, and visible unsaved drafts.
- **Do** use flat separators, compact controls, and the observed responsive order.
- **Do** keep focus visible and recovery actions beside the state they resolve.

### Don't:

- **Don't** introduce decorative imagery, extra accent colors, shadows, or card framing.
- **Don't** replace native media controls with decorative playback chrome.
- **Don't** use decorative symbols in UI labels or invent progress estimates.
- **Don't** let long names or fixed dimensions force horizontal page overflow.
