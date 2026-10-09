export function TrendGraph({ values, secondary, relative = false, resolutionSeconds = 1, formatValue = (value) => `${Math.round(value)}%`, ariaLabel }: {
  values: Array<number | null>; secondary?: Array<number | null>; relative?: boolean; resolutionSeconds?: number; formatValue?: (value: number) => string; ariaLabel: string;
}) {
  const width = 240;
  const height = 72;
  const known = (value: number | null): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const all = (secondary ? [...values, ...secondary] : values).filter(known);
  const ceiling = relative ? Math.max(1, ...all) : 100;
  const coordinates = (series: Array<number | null>) => {
    const segments: Array<Array<{ x: number; y: number }>> = [];
    let segment: Array<{ x: number; y: number }> = [];
    series.forEach((value, index) => {
      if (!known(value)) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      const x = series.length > 1 ? index / (series.length - 1) * width : width;
      segment.push({ x, y: height - Math.min(value / ceiling, 1) * height });
    });
    if (segment.length) segments.push(segment);
    return segments;
  };
  const primarySegments = coordinates(values);
  const secondarySegments = secondary ? coordinates(secondary) : [];
  const stepPath = (coordinatesList: Array<{ x: number; y: number }>) => coordinatesList.reduce((path, point, index) => {
    if (!index) return `M ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    return `${path} H ${point.x.toFixed(1)} V ${point.y.toFixed(1)}`;
  }, "");
  const primaryValues = values.filter(known);
  const secondaryValues = secondary?.filter(known) || [];
  const primaryLast = primarySegments.at(-1)?.at(-1);
  const secondaryLast = secondarySegments.at(-1)?.at(-1);
  const primaryPeak = primaryValues.length ? Math.max(...primaryValues) : 0;
  const secondaryPeak = secondaryValues.length ? Math.max(...secondaryValues) : 0;
  const elapsedSeconds = Math.max(0, (values.length - 1) * resolutionSeconds);
  const elapsedLabel = elapsedSeconds >= 86400 ? `${Math.round(elapsedSeconds / 86400)} д` : elapsedSeconds >= 3600 ? `${Math.round(elapsedSeconds / 3600)} ч` : elapsedSeconds >= 60 ? `${Math.round(elapsedSeconds / 60)} мин` : `${elapsedSeconds} сек`;
  const intervalLabel = resolutionSeconds >= 3600 ? `${Math.round(resolutionSeconds / 3600)} ч` : resolutionSeconds >= 60 ? `${Math.round(resolutionSeconds / 60)} мин` : `${resolutionSeconds} сек`;
  return <div className={`trendGraph ${secondary ? "dual" : ""}`} role="img" aria-label={ariaLabel}>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {primarySegments.map((segment, index) => segment.length > 1 && <path key={`area-${index}`} className="primaryArea" d={`${stepPath(segment)} V ${height} H ${segment[0].x.toFixed(1)} Z`} />)}
      {primarySegments.map((segment, index) => segment.length > 1 && <path key={`primary-${index}`} className="primaryTrend" d={stepPath(segment)} />)}
      {secondarySegments.map((segment, index) => segment.length > 1 && <path key={`secondary-${index}`} className="secondaryTrend" d={stepPath(segment)} />)}
      {primaryLast && primaryValues.length > 1 && <circle className="primaryPoint" cx={primaryLast.x} cy={primaryLast.y} r="2.8" />}
      {secondaryLast && secondaryValues.length > 1 && <circle className="secondaryPoint" cx={secondaryLast.x} cy={secondaryLast.y} r="2.4" />}
    </svg>
    <span className="trendYAxis"><b>{formatValue(ceiling)}</b><b>{formatValue(0)}</b></span>
    <span className="trendXAxis"><b>−{elapsedLabel}</b><b>сейчас</b></span>
    <span className="trendSummary">
      <b>Сейчас {formatValue(primaryValues.at(-1) || 0)}</b>
      <b>Пик {formatValue(primaryPeak)}</b>
      {secondary && <b>TX пик {formatValue(secondaryPeak)}</b>}
    </span>
    {secondary && <span className="trendLegend"><i /> RX <i /> TX</span>}
    <small>{primaryValues.length < 2 ? "Сбор данных…" : `${primaryValues.length} замеров · интервал ${intervalLabel}`}</small>
  </div>;
}
export function Metric({ title, value, percent, detail, history, resolutionSeconds, facts }: { title: string; value: string; percent: number; detail: string; history: Array<number | null>; resolutionSeconds: number; facts: Array<[string, string]> }) {
  const normalized = Math.max(0, Math.min(100, percent));
  return <article className={`panel metricCard metric-${title.toLowerCase()}`}>
    <div className="metricCopy"><p className="eyebrow">{title.toUpperCase()}</p><h2>{value}</h2><small>{detail}</small></div>
    <TrendGraph values={history} resolutionSeconds={resolutionSeconds} ariaLabel={`${title}: ${value}, ${Math.round(normalized)} процентов`} />
    <dl className="metricFacts">{facts.map(([label, content]) => <div key={label}><dt>{label}</dt><dd>{content}</dd></div>)}</dl>
  </article>;
}
