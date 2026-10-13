import { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js'
import TimelinePlugin from 'wavesurfer.js/dist/plugins/timeline.esm.js'
import type { Segment, Waveform as WaveformData } from './types'
import { formatTime } from './edit'

type Props = {
  data: WaveformData
  media: HTMLMediaElement
  segments: Segment[]
  selected: string[]
  currentMs: number
  onSelect: (id: string, extend: boolean) => void
  onTiming: (id: string, start: number, end: number) => void
}

export default function Waveform(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const wave = useRef<WaveSurfer | null>(null)
  const regions = useRef<RegionsPlugin | null>(null)
  const latest = useRef(props)
  latest.current = props
  const [zoom, setZoom] = useState(8)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [visible, setVisible] = useState([0, 30])
  const duration = props.data.duration_ms / 1000
  useEffect(() => {
    let disposed = false
    const plugin = RegionsPlugin.create()
    const instance = WaveSurfer.create({
      container: container.current!, media: props.media,
      peaks: [props.data.peaks], duration,
      minPxPerSec: 8, height: 100, waveColor: '#697d98', progressColor: '#245bd6', cursorColor: '#192332',
      normalize: false, autoScroll: false, autoCenter: false, dragToSeek: true,
      plugins: [plugin, TimelinePlugin.create({ height: 22, style: { fontSize: '11px', color: '#526072' } })],
    })
    wave.current = instance; regions.current = plugin
    let viewPixels = 8
    function refreshBounds() {
      if (disposed || !container.current) return
      const pixels = Math.max(viewPixels, container.current.clientWidth / Math.max(duration, 0.001))
      const start = instance.getScroll() / pixels
      const end = Math.min(duration, start + container.current.clientWidth / pixels)
      setVisible(previous => previous[0] === start && previous[1] === end ? previous : [start, end])
    }
    const observer = new ResizeObserver(refreshBounds)
    observer.observe(container.current!)
    instance.on('ready', () => { if (!disposed) { setReady(true); refreshBounds() } })
    instance.on('scroll', (start, end) => { if (!disposed) setVisible([start, end]) })
    instance.on('zoom', pixels => {
      if (!disposed) {
        viewPixels = pixels
        setZoom(pixels)
        refreshBounds()
      }
    })
    instance.on('error', failure => { if (!disposed) setError(failure.message) })
    const clearError = () => { if (!disposed) setError('') }
    props.media.addEventListener('loadeddata', clearError)
    plugin.on('region-clicked', (region, event) => {
      event.stopPropagation()
      latest.current.onSelect(region.id, event.shiftKey)
      latest.current.media.currentTime = region.start
    })
    plugin.on('region-updated', region => {
      try {
        latest.current.onTiming(region.id, Math.round(region.start * 1000), Math.round(region.end * 1000))
        setError('')
      } catch (failure) {
        setError((failure as Error).message)
        const original = latest.current.segments.find(segment => segment.id === region.id)
        if (original) region.setOptions({ start: original.start_ms / 1000, end: original.end_ms / 1000 })
      }
    })
    return () => { disposed = true; props.media.removeEventListener('loadeddata', clearError); observer.disconnect(); instance.destroy(); wave.current = null; regions.current = null }
  }, [props.media, props.data, duration])

  const allVisible = props.segments.filter(segment => segment.end_ms >= visible[0] * 1000 && segment.start_ms <= visible[1] * 1000)
  // shortcut: draw at most 180 dense regions; use density rendering if Fit view needs every region editable.
  const bounded = allVisible.length <= 180 ? allVisible : [...allVisible].sort((a, b) => {
    const rank = (segment: Segment) => props.selected.includes(segment.id) ? -1 : Math.abs(segment.start_ms - props.currentMs)
    return rank(a) - rank(b)
  }).slice(0, 180)
  useEffect(() => {
    if (!ready || !regions.current) return
    const plugin = regions.current
    const shown = new Set(bounded.map(segment => segment.id))
    plugin.getRegions().filter(region => !shown.has(region.id)).forEach(region => region.remove())
    const existing = new Map(plugin.getRegions().map(region => [region.id, region]))
    for (const segment of bounded) {
      const selected = props.selected.includes(segment.id)
      const color = selected ? 'rgba(36, 91, 214, 0.20)' : 'rgba(36, 91, 214, 0.06)'
      const region = existing.get(segment.id)
      if (region) region.setOptions({ start: segment.start_ms / 1000, end: segment.end_ms / 1000, color })
      else plugin.addRegion({ id: segment.id, start: segment.start_ms / 1000, end: segment.end_ms / 1000, color, drag: true, resize: true, minLength: 0.001 })
    }
  }, [props.segments, props.selected, visible, ready])

  function pan(direction: number) {
    const span = visible[1] - visible[0]
    wave.current?.setScrollTime(Math.max(0, Math.min(duration, visible[0] + direction * span * 0.75)))
  }
  return <section className="waveform-panel" aria-label="Waveform">
    <div className="waveform-toolbar">
      <h2>Timeline</h2>
      <span className="mono current-time">{formatTime(props.currentMs)}</span>
      <div className="waveform-tools">
        <button className="small-button" disabled={!ready} onClick={() => pan(-1)} aria-label="Pan timeline back">Back</button>
        <button className="small-button" disabled={!ready} onClick={() => pan(1)} aria-label="Pan timeline forward">Forward</button>
        <button className="small-button" disabled={!ready} onClick={() => wave.current?.setScrollTime(Math.max(0, props.currentMs / 1000 - (visible[1] - visible[0]) / 2))}>At playback</button>
        <button className="small-button" disabled={!ready} onClick={() => wave.current?.zoom(Math.max(0.01, container.current!.clientWidth / duration))}>Fit</button>
        <label className="zoom-control">Zoom <input type="range" min="0.02" max="80" step="0.02" value={zoom} disabled={!ready}
          onChange={event => wave.current?.zoom(Number(event.target.value))} /></label>
      </div>
    </div>
    <div ref={container} className="waveform" />
    <div className="waveform-hint">{error ? <span role="alert" className="error-text">{error}</span> : !ready ? 'Loading waveform' : allVisible.length > 180 ? 'Zoom in to edit all regions.' : 'Drag a region to move it. Drag an edge to change timing.'}</div>
  </section>
}
