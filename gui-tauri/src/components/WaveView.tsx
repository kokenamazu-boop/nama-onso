import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin, { type Region } from "wavesurfer.js/dist/plugins/regions.esm.js";
import {
  clampEnd,
  clampStart,
  flattenPeaks,
  fmtTime,
  MIN_VIEW_SECONDS,
  nearestEdge,
  niceStep,
  unitAt,
  type Edge,
} from "../lib/wave";
import type { Theme, Unit, WaveFacts, WavePeaks } from "../lib/types";

export type WaveHandle = {
  playRange: (start: number, end: number, loop: boolean) => Promise<void>;
  stop: () => void;
  seek: (time: number) => void;
  showAll: () => void;
};

type WaveViewProps = {
  facts: WaveFacts;
  peaks: WavePeaks;
  mediaUrl: string;
  units: Unit[];
  selected: number;
  edge: Edge;
  theme: Theme;
  onSelect: (index: number, edge?: Edge) => void;
  onEdge: (edge: Edge) => void;
  onBeginDrag: () => void;
  onMoveBoundary: (index: number, edge: Edge, value: number) => void;
  onEndDrag: () => void;
  onPlayState: (playing: boolean) => void;
  onFinish: () => void;
  onError: (message: string) => void;
};

type ViewState = { start: number; span: number; width: number };

function palette(theme: Theme) {
  if (theme === "dark") {
    return {
      wave: "#93aaa5",
      grid: "rgba(232,237,243,0.09)",
      label: "#9a958c",
      checked: "rgba(76,195,138,0.22)",
      pending: "rgba(217,164,65,0.22)",
      boundaryChecked: "#4cc38a",
      boundaryPending: "#d9a441",
      playhead: "#f07f6e",
      selected: "#a9c0b3",
    };
  }
  return {
    wave: "#647570",
    grid: "rgba(46,45,41,0.10)",
    label: "#8a857b",
    checked: "rgba(47,143,78,0.18)",
    pending: "rgba(194,116,28,0.18)",
    boundaryChecked: "#2f8f4e",
    boundaryPending: "#c2741c",
    playhead: "#b23b2e",
    selected: "#5f7565",
  };
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, value));
}

export const WaveView = forwardRef<WaveHandle, WaveViewProps>(function WaveView(
  {
    facts,
    peaks,
    mediaUrl,
    units,
    selected,
    edge,
    theme,
    onSelect,
    onEdge,
    onBeginDrag,
    onMoveBoundary,
    onEndDrag,
    onPlayState,
    onFinish,
    onError,
  },
  ref,
) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const playheadRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const regionsRef = useRef<InstanceType<typeof RegionsPlugin> | null>(null);
  const regionMap = useRef<Map<string, Region>>(new Map());
  const unitsRef = useRef<Unit[]>(units);
  const viewRef = useRef<ViewState>({ start: 0, span: facts.seconds, width: 1 });
  const rafRef = useRef<number | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const playWindow = useRef<{ start: number; end: number } | null>(null);
  const looping = useRef(false);
  const dragRef = useRef<{ index: number; edge: Edge; pushed: boolean } | null>(null);
  const [view, setView] = useState<ViewState>({ start: 0, span: facts.seconds, width: 1 });
  const [ready, setReady] = useState(false);

  unitsRef.current = units;
  viewRef.current = view;

  const colors = palette(theme);

  const setPlayhead = (time: number) => {
    const element = playheadRef.current;
    if (!element) return;
    const { start, span, width } = viewRef.current;
    const pxPerSecond = width / Math.max(1e-9, span);
    const x = (time - start) * pxPerSecond;
    if (x < -2 || x > width + 2) {
      element.style.opacity = "0";
    } else {
      element.style.opacity = "1";
      element.style.left = `${x}px`;
    }
  };

  const cancelRaf = () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  };

  const tick = () => {
    const ws = wsRef.current;
    if (!ws) return;
    const windowRange = playWindow.current;
    if (looping.current && windowRange && ws.isPlaying() && ws.getCurrentTime() >= windowRange.end - 0.012) {
      ws.setTime(windowRange.start);
      void ws.play(windowRange.start, windowRange.end);
    }
    setPlayhead(ws.getCurrentTime());
    rafRef.current = requestAnimationFrame(tick);
  };

  const startRaf = () => {
    cancelRaf();
    rafRef.current = requestAnimationFrame(tick);
  };

  const applyView = (start: number, span: number, width: number) => {
    const ws = wsRef.current;
    if (!ws || width <= 1 || !facts.seconds) return;
    const safeSpan = clamp(span, Math.min(MIN_VIEW_SECONDS, facts.seconds), facts.seconds);
    const safeStart = clamp(start, 0, Math.max(0, facts.seconds - safeSpan));
    const pxPerSecond = width / safeSpan;
    ws.zoom(pxPerSecond);
    ws.setScroll(safeStart * pxPerSecond);
    const next = { start: safeStart, span: safeSpan, width };
    viewRef.current = next;
    setView(next);
    setPlayhead(ws.getCurrentTime());
  };

  const timeAt = (clientX: number): number => {
    const stage = stageRef.current;
    if (!stage) return 0;
    const rect = stage.getBoundingClientRect();
    const { start, span, width } = viewRef.current;
    const pxPerSecond = width / Math.max(1e-9, span);
    return clamp(start + (clientX - rect.left) / pxPerSecond, 0, facts.seconds);
  };

  const stopInternal = () => {
    looping.current = false;
    playWindow.current = null;
    wsRef.current?.pause();
    cancelRaf();
    onPlayState(false);
    onFinish();
  };

  useImperativeHandle(ref, () => ({
    playRange: async (start, end, loop) => {
      const ws = wsRef.current;
      if (!ws) return;
      cancelRaf();
      looping.current = loop;
      playWindow.current = { start, end };
      ws.setTime(start);
      setPlayhead(start);
      try {
        await ws.play(start, end);
        startRaf();
      } catch (reason) {
        onError(`再生できません: ${String(reason)}`);
      }
    },
    stop: stopInternal,
    seek: (time) => {
      const ws = wsRef.current;
      if (!ws) return;
      const apply = () => {
        const windowRange = playWindow.current;
        if (ws.isPlaying() && windowRange && (time < windowRange.start || time >= windowRange.end)) {
          stopInternal();
        }
        ws.setTime(clamp(time, 0, facts.seconds));
        setPlayhead(time);
      };
      const media = ws.getMediaElement();
      if (media && media.readyState === 0) {
        pendingSeekRef.current = time;
        media.addEventListener(
          "loadedmetadata",
          () => {
            if (pendingSeekRef.current !== null) {
              const pending = pendingSeekRef.current;
              pendingSeekRef.current = null;
              apply();
              setPlayhead(pending);
            }
          },
          { once: true },
        );
        return;
      }
      apply();
    },
    showAll: () => {
      const ws = wsRef.current;
      if (!ws) return;
      applyView(0, facts.seconds, ws.getWidth());
    },
  }));

  // ----------------------------------------------------------- wavesurfer
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !mediaUrl) return;
    setReady(false);
    const regions = RegionsPlugin.create();
    const flat = flattenPeaks(peaks);
    const ws = WaveSurfer.create({
      container,
      url: mediaUrl,
      peaks: [flat],
      duration: facts.seconds,
      height: "auto",
      waveColor: palette(theme).wave,
      progressColor: "transparent",
      cursorColor: "transparent",
      cursorWidth: 0,
      interact: false,
      dragToSeek: false,
      hideScrollbar: true,
      fillParent: false,
      normalize: false,
      backend: "MediaElement",
      minPxPerSec: 1,
      plugins: [regions],
    });
    wsRef.current = ws;
    regionsRef.current = regions;
    regionMap.current.clear();

    const removeReady = ws.on("ready", () => {
      const width = ws.getWidth();
      const next = { start: 0, span: facts.seconds, width };
      viewRef.current = next;
      setView(next);
      ws.zoom(width / facts.seconds);
      ws.setScroll(0);
      setReady(true);
      setPlayhead(ws.getCurrentTime());
    });
    const removeScroll = ws.on("scroll", (pixels) => {
      const current = viewRef.current;
      const pxPerSecond = current.width / Math.max(1e-9, current.span);
      const next = {
        ...current,
        start: clamp(pixels / pxPerSecond, 0, Math.max(0, facts.seconds - current.span)),
      };
      viewRef.current = next;
      setView(next);
      setPlayhead(ws.getCurrentTime());
    });
    const removeTime = ws.on("timeupdate", (time) => setPlayhead(time));
    const removePlay = ws.on("play", () => onPlayState(true));
    const removePause = ws.on("pause", () => {
      if (looping.current && playWindow.current) {
        const { start, end } = playWindow.current;
        window.setTimeout(() => {
          if (looping.current && playWindow.current) {
            ws.setTime(start);
            void ws.play(start, end);
          }
        }, 40);
        return;
      }
      onPlayState(false);
    });
    const removeFinish = ws.on("finish", () => {
      if (looping.current && playWindow.current) {
        const { start, end } = playWindow.current;
        void ws.play(start, end);
        return;
      }
      playWindow.current = null;
      cancelRaf();
      onPlayState(false);
      onFinish();
    });
    const removeError = ws.on("error", (error) => onError(`波形を読み込めません: ${String(error)}`));

    return () => {
      removeReady();
      removeScroll();
      removeTime();
      removePlay();
      removePause();
      removeFinish();
      removeError();
      cancelRaf();
      ws.destroy();
      wsRef.current = null;
      regionsRef.current = null;
      regionMap.current.clear();
      setReady(false);
    };
    // The waveform instance is tied to one opened WAV (or a replaced file).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facts.path, mediaUrl, peaks]);

  useEffect(() => {
    const ws = wsRef.current;
    if (ws) ws.setOptions({ waveColor: palette(theme).wave });
  }, [theme]);

  // Keep the view span on window resize.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(() => {
      const ws = wsRef.current;
      if (!ws || !ready) return;
      const width = ws.getWidth();
      const current = viewRef.current;
      applyView(current.start, current.span, width);
    });
    observer.observe(stage);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, facts.path]);

  // Regions mirror the candidate list (display only; editing is our overlay).
  useEffect(() => {
    const regions = regionsRef.current;
    if (!regions || !ready) return;
    const seen = new Set<string>();
    units.forEach((unit, index) => {
      const id = `u${index}`;
      seen.add(id);
      const color = unit.confidence === "checked" ? palette(theme).checked : palette(theme).pending;
      let region = regionMap.current.get(id);
      if (!region) {
        const created = regions.addRegion({
          id,
          start: unit.start,
          end: unit.end,
          content: unit.name,
          color,
          drag: false,
          resize: false,
          minLength: 0.005,
        });
        if (!created) return;
        regionMap.current.set(id, created);
      } else {
        if (region.getContent() !== unit.name) region.setOptions({ content: unit.name });
        const update: { start?: number; end?: number; color?: string } = {};
        if (Math.abs(region.start - unit.start) > 1e-9) update.start = unit.start;
        if (Math.abs(region.end - unit.end) > 1e-9) update.end = unit.end;
        if (region.color !== color) update.color = color;
        if (Object.keys(update).length) region.setOptions(update);
      }
    });
    for (const [id, region] of [...regionMap.current]) {
      if (!seen.has(id)) {
        region.remove();
        regionMap.current.delete(id);
      }
    }
  }, [units, ready, theme]);

  // Wheel: move the view; Alt+wheel zooms around the cursor (gui.py on_wheel).
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      const ws = wsRef.current;
      if (!ws) return;
      event.preventDefault();
      const current = viewRef.current;
      const width = current.width;
      const pxPerSecond = width / Math.max(1e-9, current.span);
      if (event.altKey) {
        const rect = stage.getBoundingClientRect();
        const time = current.start + (event.clientX - rect.left) / pxPerSecond;
        const factor = event.deltaY < 0 ? 0.8 : 1.25;
        const span = clamp(
          current.span * factor,
          Math.min(MIN_VIEW_SECONDS, facts.seconds),
          facts.seconds,
        );
        const start = clamp(
          time - (event.clientX - rect.left) / (width / span),
          0,
          Math.max(0, facts.seconds - span),
        );
        applyView(start, span, width);
      } else {
        const delta = width * 0.15 * (event.deltaY < 0 ? -1 : 1);
        applyView(
          clamp(current.start + delta / pxPerSecond, 0, Math.max(0, facts.seconds - current.span)),
          current.span,
          width,
        );
      }
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, facts.path]);

  // ------------------------------------------------------------- pointers
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const time = timeAt(event.clientX);
    const { span, width } = viewRef.current;
    const tolerance = 5 / Math.max(1e-9, width / span);
    const hit = nearestEdge(units, time, tolerance);
    if (hit) {
      onSelect(hit.index, hit.edge);
      onEdge(hit.edge);
      dragRef.current = { index: hit.index, edge: hit.edge, pushed: false };
      return;
    }
    dragRef.current = null;
    const index = unitAt(units, time);
    onSelect(index);
    const ws = wsRef.current;
    if (ws) {
      const windowRange = playWindow.current;
      if (ws.isPlaying() && windowRange && (time < windowRange.start || time >= windowRange.end)) {
        stopInternal();
      }
      ws.setTime(time);
      setPlayhead(time);
    }
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const unit = units[drag.index];
    if (!unit) return;
    const time = timeAt(event.clientX);
    const value = drag.edge === "start" ? clampStart(unit, time, facts.seconds) : clampEnd(unit, time, facts.seconds);
    const current = drag.edge === "start" ? unit.start : unit.end;
    if (Math.abs(value - current) < 1e-9) return;
    if (!drag.pushed) {
      onBeginDrag();
      drag.pushed = true;
    }
    onMoveBoundary(drag.index, drag.edge, value);
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current) {
      dragRef.current = null;
      onEndDrag();
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  // ----------------------------------------------------------------- view
  const pxPerSecond = view.width / Math.max(1e-9, view.span);
  const visibleStart = Math.max(0, view.start - 0.05);
  const visibleEnd = Math.min(facts.seconds, view.start + view.span + 0.05);
  const step = niceStep(view.span);
  const gridTimes: number[] = [];
  for (let t = Math.floor(visibleStart / step) * step; t <= visibleEnd + 1e-9; t += step) {
    gridTimes.push(t);
  }
  const selectedUnit = selected >= 0 ? units[selected] : null;
  const selectedX = selectedUnit ? (selectedUnit.start - view.start) * pxPerSecond : 0;
  const selectedW = selectedUnit ? (selectedUnit.end - selectedUnit.start) * pxPerSecond : 0;
  const showHint = units.length === 0;

  return (
    <div className="wave-stage" ref={stageRef}>
      <div className="wave-canvas" ref={containerRef} />
      <div
        className="wave-overlay"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {gridTimes.map((t) => {
          const x = (t - view.start) * pxPerSecond;
          if (x < -1 || x > view.width + 1) return null;
          return (
            <div key={`grid-${t.toFixed(4)}`} className="wave-grid-line" style={{ left: `${x}px` }}>
              <span className="wave-time-label">{fmtTime(t)}</span>
            </div>
          );
        })}

        {units.map((unit, index) => {
          if (unit.end < visibleStart || unit.start > visibleEnd) return null;
          const left = (unit.start - view.start) * pxPerSecond;
          const right = (unit.end - view.start) * pxPerSecond;
          const checked = unit.confidence === "checked";
          return (
            <div key={`bounds-${index}`}>
              <div
                className="wave-boundary"
                style={{
                  left: `${left}px`,
                  backgroundColor: checked ? colors.boundaryChecked : colors.boundaryPending,
                }}
              />
              <div
                className="wave-boundary"
                style={{
                  left: `${right}px`,
                  backgroundColor: checked ? colors.boundaryChecked : colors.boundaryPending,
                }}
              />
              {unit.phonemes.map((phoneme, phonemeIndex) => {
                if (phonemeIndex === 0) return null;
                const at = unit.phoneme_starts[phonemeIndex];
                if (at === undefined) return null;
                const x = (at - view.start) * pxPerSecond;
                if (x < -20 || x > view.width + 20) return null;
                return (
                  <div key={`ph-${index}-${phonemeIndex}`}>
                    <div className="wave-phoneme-tick" style={{ left: `${x}px` }} />
                    <span className="wave-phoneme-label" style={{ left: `${x + 2}px` }}>
                      {phoneme}
                    </span>
                  </div>
                );
              })}
            </div>
          );
        })}

        {selectedUnit ? (
          <div
            className="wave-selected-frame"
            style={{ left: `${selectedX}px`, width: `${Math.max(1, selectedW)}px`, borderColor: colors.selected }}
          />
        ) : null}

        <div className="wave-playhead" ref={playheadRef} style={{ backgroundColor: colors.playhead }} />

        {showHint ? <div className="wave-empty-hint">歌詞を入力して「自動で割り当て」</div> : null}
        <div className="wave-view-hint">
          Alt+ホイール:拡大　ホイール:移動　橙=要確認 / 緑=確認済み
        </div>
        {selectedUnit ? (
          <div className="wave-edge-badge">{edge === "start" ? "開始を移動" : "終了を移動"}</div>
        ) : null}
      </div>
    </div>
  );
});
