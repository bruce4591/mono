import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  GestureResponderEvent,
  NativeScrollEvent,
  NativeSyntheticEvent,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View
} from "react-native";
import Svg, { Line, Path, Rect, Text as SvgText } from "react-native-svg";

import type { MobileBar } from "../api/types";
import { EmptyState } from "./EmptyState";

const PRICE_CHART_HEIGHT = 238;
const VOLUME_CHART_HEIGHT = 48;
const MACD_CHART_HEIGHT = 58;
const RSI_CHART_HEIGHT = 62;
const X_AXIS_HEIGHT = 22;
const CANDLE_WIDTH = 6;
const CANDLE_GAP = 3;
const LEFT_PADDING = 8;
const RIGHT_PADDING = 64;
const UP_COLOR = "#0ecb81";
const DOWN_COLOR = "#f6465d";
const GRID_COLOR = "#dddddd";
const AXIS_COLOR = "#3f3f46";
const MA_PERIODS = [5, 11, 22, 60, 120] as const;
const MA_COLORS: Record<(typeof MA_PERIODS)[number], string> = {
  5: "#f0b90b",
  11: "#ec4899",
  22: "#8b5cf6",
  60: "#22c55e",
  120: "#a16207"
};

export function NativeKLineChart({
  bars,
  period,
  resetKey,
  onSelectedBarChange,
  onReachStart
}: {
  bars: MobileBar[];
  period: string;
  resetKey?: string;
  onSelectedBarChange?: (bar: MobileBar | null) => void;
  onReachStart?: () => void;
}) {
  const scrollRef = useRef<ScrollView>(null);
  const autoScrolledRef = useRef(false);
  const resetKeyRef = useRef(resetKey);
  const { width } = useWindowDimensions();
  const [scrollX, setScrollX] = useState(0);
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  if (resetKeyRef.current !== resetKey) {
    resetKeyRef.current = resetKey;
    autoScrolledRef.current = false;
  }
  const visibleBars = bars.filter(isValidBar);
  const slotWidth = CANDLE_WIDTH + CANDLE_GAP;
  const viewportWidth = Math.max(width, 320);
  const dataWidth = visibleBars.length * slotWidth + LEFT_PADDING;
  const plotWidth = Math.max(viewportWidth, dataWidth + RIGHT_PADDING);
  const maxScrollX = Math.max(plotWidth - viewportWidth, 0);
  const effectiveScrollX = clamp(scrollX, 0, maxScrollX);
  const axisX = effectiveScrollX + viewportWidth - RIGHT_PADDING + 6;
  const visibleWindow = useMemo(() => {
    return getVisibleWindow(visibleBars, effectiveScrollX, viewportWidth, slotWidth);
  }, [effectiveScrollX, slotWidth, viewportWidth, visibleBars]);
  const latest = visibleBars[visibleBars.length - 1] ?? null;
  const selectedIndex = Math.max(
    selectedTime ? visibleBars.findIndex((bar) => bar.time === selectedTime) : -1,
    -1
  );
  const selectedBar = selectedIndex >= 0 ? visibleBars[selectedIndex] : latest;
  const selectedX =
    selectedIndex >= 0
      ? xForIndex(selectedIndex, slotWidth)
      : xForIndex(Math.max(visibleBars.length - 1, 0), slotWidth);

  useEffect(() => {
    setSelectedTime(null);
  }, [resetKey]);

  useEffect(() => {
    onSelectedBarChange?.(selectedBar);
  }, [onSelectedBarChange, selectedBar]);

  if (visibleBars.length === 0 || visibleWindow.length === 0) {
    return <EmptyState title="暂无 K 线" message="当前周期没有可展示的数据" />;
  }

  const chartBar = selectedBar ?? visibleBars[visibleBars.length - 1]!;
  const rawHigh = Math.max(...visibleWindow.map((bar) => bar.high));
  const rawLow = Math.min(...visibleWindow.map((bar) => bar.low));
  const pricePadding = Math.max((rawHigh - rawLow) * 0.12, Math.abs(rawHigh) * 0.001, 0.000001);
  const high = rawHigh + pricePadding;
  const low = rawLow - pricePadding;
  const range = Math.max(high - low, 0.000001);
  const maxVolume = Math.max(...visibleWindow.map((bar) => bar.volume ?? 0), 0.000001);
  const movingAverages = MA_PERIODS.map((periodValue) => ({
    period: periodValue,
    color: MA_COLORS[periodValue],
    values: movingAverage(visibleBars, periodValue)
  }));
  const volumeMa5 = movingAverageByValue(
    visibleBars.map((bar) => bar.volume ?? 0),
    5
  );
  const volumeMa10 = movingAverageByValue(
    visibleBars.map((bar) => bar.volume ?? 0),
    10
  );
  const macd = buildMacd(visibleBars.map((bar) => bar.close));
  const rsi7 = buildRsi(visibleBars.map((bar) => bar.close), 7);
  const rsi14 = buildRsi(visibleBars.map((bar) => bar.close), 14);
  const rsi28 = buildRsi(visibleBars.map((bar) => bar.close), 28);
  const movingAveragePaths = movingAverages.map((item) => ({
    period: item.period,
    color: item.color,
    path: buildLinePath(item.values, high, range, slotWidth)
  }));
  const volumeMa5Path = buildScaledLinePath(volumeMa5, 0, maxVolume, VOLUME_CHART_HEIGHT, slotWidth);
  const volumeMa10Path = buildScaledLinePath(volumeMa10, 0, maxVolume, VOLUME_CHART_HEIGHT, slotWidth);
  const macdMax = Math.max(
    ...macd.histogram.map((value) => Math.abs(value)),
    ...macd.dif.map((value) => Math.abs(value)),
    ...macd.dea.map((value) => Math.abs(value)),
    0.000001
  );
  const macdDifPath = buildCenteredLinePath(macd.dif, macdMax, MACD_CHART_HEIGHT, slotWidth);
  const macdDeaPath = buildCenteredLinePath(macd.dea, macdMax, MACD_CHART_HEIGHT, slotWidth);
  const rsi7Path = buildScaledLinePath(rsi7, 0, 100, RSI_CHART_HEIGHT, slotWidth);
  const rsi14Path = buildScaledLinePath(rsi14, 0, 100, RSI_CHART_HEIGHT, slotWidth);
  const rsi28Path = buildScaledLinePath(rsi28, 0, 100, RSI_CHART_HEIGHT, slotWidth);
  const priceMarks = buildPriceMarks(high, low);
  const latestMacd = macd.histogram[macd.histogram.length - 1];
  const xLabels = buildXAxisLabels(visibleWindow, effectiveScrollX, viewportWidth, period);

  function handleScroll(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const nextScrollX = event.nativeEvent.contentOffset.x;
    setScrollX(nextScrollX);
    if (nextScrollX <= 16) {
      onReachStart?.();
    }
  }

  function handlePriceChartPress(event: GestureResponderEvent) {
    const nextIndex = Math.round(
      clamp(
        (event.nativeEvent.locationX - LEFT_PADDING - CANDLE_WIDTH / 2) / slotWidth,
        0,
        Math.max(visibleBars.length - 1, 0)
      )
    );
    const nextBar = visibleBars[nextIndex] ?? chartBar;
    setSelectedTime(nextBar.time);
    onSelectedBarChange?.(nextBar);
  }

  return (
    <View style={styles.root}>
      <View style={styles.headerRow}>
        <Text style={styles.latestText}>{formatSelectedTime(chartBar.time, period)}</Text>
      </View>
      <View style={styles.legendRow}>
        {movingAverages.map((item) => (
          <Text key={`ma:${item.period}`} style={[styles.legendText, { color: item.color }]}>
            MA{item.period} {formatOptionalPrice(item.values[item.values.length - 1])}
          </Text>
        ))}
        <Text style={styles.legendText}>VOL {formatMetric(chartBar.volume ?? null)}</Text>
      </View>
      <View style={styles.chartViewport}>
        <ScrollView
          horizontal
          ref={scrollRef}
          bounces={false}
          showsHorizontalScrollIndicator={false}
          scrollEventThrottle={32}
          onScroll={handleScroll}
          onContentSizeChange={() => {
            if (autoScrolledRef.current) return;
            scrollRef.current?.scrollToEnd({ animated: false });
            setScrollX(maxScrollX);
            autoScrolledRef.current = true;
          }}
        >
          <View style={styles.chartPanel}>
            <Svg width={plotWidth} height={PRICE_CHART_HEIGHT} onPress={handlePriceChartPress}>
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
              const y = ratio * PRICE_CHART_HEIGHT;
              return (
                <Line
                  key={`grid:${ratio}`}
                  x1={0}
                  y1={y}
                  x2={plotWidth}
                  y2={y}
                  stroke={GRID_COLOR}
                  strokeWidth={StyleSheet.hairlineWidth}
                />
              );
            })}
            {visibleBars.map((bar, index) => {
              const x = LEFT_PADDING + index * slotWidth;
              const centerX = x + CANDLE_WIDTH / 2;
              const rising = bar.close >= bar.open;
              const color = rising ? UP_COLOR : DOWN_COLOR;
              const wickY1 = yForPrice(bar.high, high, range);
              const wickY2 = yForPrice(bar.low, high, range);
              const bodyY = yForPrice(Math.max(bar.open, bar.close), high, range);
              const bodyBottom = yForPrice(Math.min(bar.open, bar.close), high, range);
              const bodyHeight = Math.max(bodyBottom - bodyY, 2);
              return (
                <React.Fragment key={`${bar.time}:${index}`}>
                  <Line
                    x1={centerX}
                    y1={wickY1}
                    x2={centerX}
                    y2={wickY2}
                    stroke={color}
                    strokeWidth={1.2}
                  />
                  <Rect
                    x={x}
                    y={bodyY}
                    width={CANDLE_WIDTH}
                    height={bodyHeight}
                    stroke={color}
                    strokeWidth={1}
                    fill={rising ? color : "#ffffff"}
                  />
                </React.Fragment>
              );
            })}
            {movingAveragePaths.map((item) =>
              item.path ? (
                <Path key={`ma-path:${item.period}`} d={item.path} stroke={item.color} strokeWidth={1.4} fill="none" />
              ) : null
            )}
            <Line
              x1={selectedX}
              y1={0}
              x2={selectedX}
              y2={PRICE_CHART_HEIGHT}
              stroke="#18181b"
              strokeWidth={1}
              strokeDasharray="4 4"
              opacity={0.5}
            />
          </Svg>
          <Svg width={plotWidth} height={X_AXIS_HEIGHT}>
            <Line
              x1={0}
              y1={0}
              x2={plotWidth}
              y2={0}
              stroke={GRID_COLOR}
              strokeWidth={StyleSheet.hairlineWidth}
            />
            {xLabels.map((label) => (
              <SvgText
                key={`${label.text}:${label.x}`}
                x={label.x}
                y={18}
                fill={AXIS_COLOR}
                fontSize="10"
                fontWeight="700"
                textAnchor={label.anchor}
              >
                {label.text}
              </SvgText>
            ))}
          </Svg>
          <Svg width={plotWidth} height={VOLUME_CHART_HEIGHT} style={styles.volumeSvg}>
            <Line
              x1={0}
              y1={0}
              x2={plotWidth}
              y2={0}
              stroke={GRID_COLOR}
              strokeWidth={StyleSheet.hairlineWidth}
            />
            {visibleBars.map((bar, index) => {
              const x = LEFT_PADDING + index * slotWidth;
              const rising = bar.close >= bar.open;
              const height = Math.max(((bar.volume ?? 0) / maxVolume) * (VOLUME_CHART_HEIGHT - 8), 1);
              return (
                <Rect
                  key={`volume:${bar.time}:${index}`}
                  x={x}
                  y={VOLUME_CHART_HEIGHT - height}
                  width={CANDLE_WIDTH}
                  height={height}
                  fill={rising ? "rgba(14, 203, 129, 0.42)" : "rgba(246, 70, 93, 0.42)"}
                />
              );
            })}
            {volumeMa5Path ? <Path d={volumeMa5Path} stroke={MA_COLORS[5]} strokeWidth={1.2} fill="none" /> : null}
            {volumeMa10Path ? <Path d={volumeMa10Path} stroke={MA_COLORS[11]} strokeWidth={1.2} fill="none" /> : null}
            <SvgText x={axisX} y={12} fill={AXIS_COLOR} fontSize="10" fontWeight="700">
              {formatMetric(maxVolume)}
            </SvgText>
          </Svg>
          <IndicatorLegend
            items={[
              { label: `DIF: ${formatIndicator(macd.dif[macd.dif.length - 1])}`, color: MA_COLORS[5] },
              { label: `DEA: ${formatIndicator(macd.dea[macd.dea.length - 1])}`, color: MA_COLORS[22] },
              { label: `MACD: ${formatIndicator(latestMacd)}`, color: MA_COLORS[5] }
            ]}
          />
          <Svg width={plotWidth} height={MACD_CHART_HEIGHT}>
            <Line
              x1={0}
              y1={MACD_CHART_HEIGHT / 2}
              x2={plotWidth}
              y2={MACD_CHART_HEIGHT / 2}
              stroke={GRID_COLOR}
              strokeWidth={StyleSheet.hairlineWidth}
            />
            {macd.histogram.map((value, index) => {
              const x = LEFT_PADDING + index * slotWidth;
              const zeroY = MACD_CHART_HEIGHT / 2;
              const barHeight = Math.abs(value / macdMax) * (MACD_CHART_HEIGHT / 2 - 4);
              const positive = value >= 0;
              return (
                <Rect
                  key={`macd:${index}`}
                  x={x}
                  y={positive ? zeroY - barHeight : zeroY}
                  width={CANDLE_WIDTH}
                  height={Math.max(barHeight, 1)}
                  fill={positive ? "rgba(14, 203, 129, 0.62)" : "rgba(246, 70, 93, 0.62)"}
                />
              );
            })}
            {macdDifPath ? <Path d={macdDifPath} stroke={MA_COLORS[5]} strokeWidth={1.3} fill="none" /> : null}
            {macdDeaPath ? <Path d={macdDeaPath} stroke={MA_COLORS[22]} strokeWidth={1.3} fill="none" /> : null}
            <SvgText x={axisX} y={16} fill={AXIS_COLOR} fontSize="10" fontWeight="700">
              {formatIndicator(macdMax)}
            </SvgText>
            <SvgText x={axisX} y={MACD_CHART_HEIGHT - 6} fill={AXIS_COLOR} fontSize="10" fontWeight="700">
              0.0000
            </SvgText>
          </Svg>
          <IndicatorLegend
            items={[
              { label: `RSI(7): ${formatIndicator(rsi7[rsi7.length - 1])}`, color: MA_COLORS[5] },
              { label: `RSI(14): ${formatIndicator(rsi14[rsi14.length - 1])}`, color: "#ec4899" },
              { label: `RSI(28): ${formatIndicator(rsi28[rsi28.length - 1])}`, color: MA_COLORS[22] }
            ]}
          />
          <Svg width={plotWidth} height={RSI_CHART_HEIGHT}>
            {[30, 70].map((mark) => {
              const y = RSI_CHART_HEIGHT - (mark / 100) * RSI_CHART_HEIGHT;
              return (
                <Line
                  key={`rsi-line:${mark}`}
                  x1={0}
                  y1={y}
                  x2={plotWidth}
                  y2={y}
                  stroke={GRID_COLOR}
                  strokeWidth={StyleSheet.hairlineWidth}
                />
              );
            })}
            {rsi7Path ? <Path d={rsi7Path} stroke={MA_COLORS[5]} strokeWidth={1.3} fill="none" /> : null}
            {rsi14Path ? <Path d={rsi14Path} stroke="#ec4899" strokeWidth={1.3} fill="none" /> : null}
            {rsi28Path ? <Path d={rsi28Path} stroke={MA_COLORS[22]} strokeWidth={1.3} fill="none" /> : null}
            <SvgText x={axisX} y={18} fill={AXIS_COLOR} fontSize="10" fontWeight="700">
              70.0
            </SvgText>
            <SvgText x={axisX} y={RSI_CHART_HEIGHT - 8} fill={AXIS_COLOR} fontSize="10" fontWeight="700">
              30.0
            </SvgText>
          </Svg>
          </View>
        </ScrollView>
        <View pointerEvents="none" style={styles.priceAxisOverlay}>
          <Svg width={RIGHT_PADDING} height={PRICE_CHART_HEIGHT}>
            <Rect x={0} y={0} width={RIGHT_PADDING} height={PRICE_CHART_HEIGHT} fill="#ffffff" opacity={0.96} />
            {priceMarks.map((mark, index) => {
              const y = yForPrice(mark, high, range);
              return (
                <SvgText
                  key={`price-axis:${index}`}
                  x={6}
                  y={clamp(y + 4, 12, PRICE_CHART_HEIGHT - 4)}
                  fill={AXIS_COLOR}
                  fontSize="11"
                  fontWeight="800"
                >
                  {formatPrice(mark)}
                </SvgText>
              );
            })}
          </Svg>
        </View>
      </View>
    </View>
  );
}

function IndicatorLegend({
  items
}: {
  items: Array<{ label: string; color: string }>;
}) {
  return (
    <View style={styles.indicatorLegend}>
      {items.map((item) => (
        <Text key={item.label} style={[styles.indicatorText, { color: item.color }]}>
          {item.label}
        </Text>
      ))}
    </View>
  );
}

function isValidBar(bar: MobileBar): boolean {
  return (
    Number.isFinite(bar.open) &&
    Number.isFinite(bar.high) &&
    Number.isFinite(bar.low) &&
    Number.isFinite(bar.close)
  );
}

function yForPrice(price: number, high: number, range: number): number {
  return ((high - price) / range) * PRICE_CHART_HEIGHT;
}

function xForIndex(index: number, slotWidth: number): number {
  return LEFT_PADDING + index * slotWidth + CANDLE_WIDTH / 2;
}

function movingAverage(bars: MobileBar[], period: number): Array<number | null> {
  return movingAverageByValue(
    bars.map((bar) => bar.close),
    period
  );
}

function movingAverageByValue(values: number[], period: number): Array<number | null> {
  let sum = 0;
  return values.map((value, index) => {
    sum += value;
    if (index >= period) {
      sum -= values[index - period];
    }
    if (index < period - 1) {
      return null;
    }
    return sum / period;
  });
}

function buildMacd(values: number[]) {
  const ema12 = exponentialMovingAverage(values, 12);
  const ema26 = exponentialMovingAverage(values, 26);
  const dif = values.map((_, index) => ema12[index] - ema26[index]);
  const dea = exponentialMovingAverage(dif, 9);
  const histogram = dif.map((value, index) => (value - dea[index]) * 2);
  return { dif, dea, histogram };
}

function exponentialMovingAverage(values: number[], period: number): number[] {
  const multiplier = 2 / (period + 1);
  let previous = values[0] ?? 0;
  return values.map((value, index) => {
    if (index === 0) return value;
    previous = value * multiplier + previous * (1 - multiplier);
    return previous;
  });
}

function buildRsi(values: number[], period: number): Array<number | null> {
  let gainSum = 0;
  let lossSum = 0;
  return values.map((value, index) => {
    if (index === 0) return null;
    const change = value - values[index - 1];
    gainSum += Math.max(change, 0);
    lossSum += Math.max(-change, 0);
    if (index > period) {
      const oldChange = values[index - period] - values[index - period - 1];
      gainSum -= Math.max(oldChange, 0);
      lossSum -= Math.max(-oldChange, 0);
    }
    if (index < period) return null;
    if (lossSum === 0) return 100;
    const rs = gainSum / lossSum;
    return 100 - 100 / (1 + rs);
  });
}

function buildLinePath(
  values: Array<number | null>,
  high: number,
  range: number,
  slotWidth: number
): string {
  const segments: string[] = [];
  values.forEach((value, index) => {
    if (value === null) return;
    const command = segments.length === 0 ? "M" : "L";
    const y = clamp(yForPrice(value, high, range), 0, PRICE_CHART_HEIGHT);
    segments.push(`${command}${xForIndex(index, slotWidth).toFixed(2)},${y.toFixed(2)}`);
  });
  return segments.join(" ");
}

function getVisibleWindow(
  bars: MobileBar[],
  scrollX: number,
  viewportWidth: number,
  slotWidth: number
): MobileBar[] {
  const left = Math.max(scrollX - LEFT_PADDING, 0);
  const right = Math.max(scrollX + viewportWidth - RIGHT_PADDING, left);
  const startIndex = clamp(Math.floor(left / slotWidth) - 2, 0, Math.max(bars.length - 1, 0));
  const endIndex = clamp(Math.ceil(right / slotWidth) + 2, startIndex + 1, bars.length);
  return bars.slice(startIndex, endIndex);
}

function buildPriceMarks(high: number, low: number): number[] {
  const range = Math.max(high - low, 0.000001);
  return [high, high - range * 0.25, high - range * 0.5, high - range * 0.75, low];
}

function buildXAxisLabels(
  bars: MobileBar[],
  scrollX: number,
  viewportWidth: number,
  period: string
): Array<{ text: string; x: number; anchor: "start" | "middle" | "end" }> {
  if (bars.length === 0) return [];
  const middleIndex = Math.floor(bars.length / 2);
  const lastIndex = bars.length - 1;
  const leftX = scrollX + LEFT_PADDING;
  const centerX = scrollX + (viewportWidth - RIGHT_PADDING) / 2;
  const rightX = scrollX + viewportWidth - RIGHT_PADDING - 4;
  return [
    {
      text: formatAxisTime(bars[0].time, period),
      x: leftX,
      anchor: "start"
    },
    {
      text: formatAxisTime(bars[middleIndex].time, period),
      x: centerX,
      anchor: "middle"
    },
    {
      text: formatAxisTime(bars[lastIndex].time, period),
      x: rightX,
      anchor: "end"
    }
  ];
}

function buildScaledLinePath(
  values: Array<number | null>,
  min: number,
  max: number,
  height: number,
  slotWidth: number
): string {
  const range = Math.max(max - min, 0.000001);
  const segments: string[] = [];
  values.forEach((value, index) => {
    if (value === null) return;
    const x = xForIndex(index, slotWidth);
    const y = height - ((value - min) / range) * height;
    segments.push(`${segments.length === 0 ? "M" : "L"}${x.toFixed(2)},${clamp(y, 0, height).toFixed(2)}`);
  });
  return segments.join(" ");
}

function buildCenteredLinePath(
  values: number[],
  maxAbs: number,
  height: number,
  slotWidth: number
): string {
  const segments: string[] = [];
  values.forEach((value, index) => {
    const x = xForIndex(index, slotWidth);
    const y = height / 2 - (value / maxAbs) * (height / 2 - 4);
    segments.push(`${segments.length === 0 ? "M" : "L"}${x.toFixed(2)},${clamp(y, 0, height).toFixed(2)}`);
  });
  return segments.join(" ");
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function formatOptionalPrice(value: number | null): string {
  return value === null ? "--" : formatPrice(value);
}

function formatPrice(value: number): string {
  const abs = Math.abs(value);
  if (abs > 0 && abs < 0.0001) return value.toFixed(8);
  if (abs > 0 && abs < 1) return value.toFixed(6);
  return value.toLocaleString(undefined, {
    maximumFractionDigits: abs >= 100 ? 2 : 4
  });
}

function formatMetric(value: number | null): string {
  if (value === null) return "--";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

function formatAxisTime(value: string, period: string): string {
  const parsed = parseTimeParts(value);
  if (parsed === null) {
    return value.length >= 10 ? value.slice(5, 10) : value;
  }
  if (period === "1d") {
    return `${parsed.month}-${parsed.day}`;
  }
  if (period === "8h") {
    return `${parsed.month}-${parsed.day} ${parsed.hour}:${parsed.minute}`;
  }
  return `${parsed.hour}:${parsed.minute}`;
}

function formatSelectedTime(value: string, period: string): string {
  const parsed = parseTimeParts(value);
  if (parsed === null) return value;
  if (period === "1d") {
    return `${parsed.year}-${parsed.month}-${parsed.day}`;
  }
  return `${parsed.month}-${parsed.day} ${parsed.hour}:${parsed.minute}`;
}

function parseTimeParts(value: string): {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
} | null {
  const normalized = value.includes("T") ? value : `${value}T00:00:00Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return {
    year: String(date.getUTCFullYear()),
    month: String(date.getUTCMonth() + 1).padStart(2, "0"),
    day: String(date.getUTCDate()).padStart(2, "0"),
    hour: String(date.getUTCHours()).padStart(2, "0"),
    minute: String(date.getUTCMinutes()).padStart(2, "0")
  };
}

function formatIndicator(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "--";
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toFixed(2);
  if (abs >= 1) return value.toFixed(4);
  return value.toFixed(8);
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    marginTop: 4
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    alignItems: "baseline",
    marginBottom: 3,
    minHeight: 14
  },
  latestText: {
    color: "#777777",
    fontSize: 10
  },
  legendRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 9,
    marginBottom: 4
  },
  legendText: {
    color: "#777777",
    fontSize: 10,
    fontWeight: "700"
  },
  chartPanel: {
    backgroundColor: "#ffffff"
  },
  chartViewport: {
    flex: 1,
    position: "relative"
  },
  priceAxisOverlay: {
    position: "absolute",
    top: 0,
    right: 0,
    width: RIGHT_PADDING,
    height: PRICE_CHART_HEIGHT,
    borderLeftWidth: StyleSheet.hairlineWidth,
    borderLeftColor: "#d4d4d8"
  },
  volumeSvg: {
    marginTop: 2
  },
  indicatorLegend: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    minHeight: 18,
    alignItems: "center",
    marginTop: 3
  },
  indicatorText: {
    fontSize: 10,
    fontWeight: "700"
  }
});
