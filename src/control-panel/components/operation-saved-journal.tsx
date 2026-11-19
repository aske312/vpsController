import { safeDateTime } from "../../shared/lib/control-plane-ui";

export type SavedOperationJournal = {
  captured_at: string;
  available: boolean;
  reason?: string;
  truncated?: boolean;
  unit?: string;
  lines: { at: string; message: string; priority?: string }[];
};

function level(priority?: string) {
  if (priority && ["0", "1", "2", "3"].includes(priority)) return { label: "ERR", tone: "error" };
  if (priority === "4") return { label: "WARN", tone: "warning" };
  if (priority === "7") return { label: "DBG", tone: "debug" };
  return { label: priority ? "INFO" : "LOG", tone: "info" };
}

export function OperationSavedJournal({ evidence }: { evidence: SavedOperationJournal }) {
  return <details className="operationSavedEvidence" open>
    <summary>
      <span className="operationSavedHeading"><span className="operationSavedIcon" aria-hidden="true">≡</span><strong>Журнал службы</strong><span className="operationSavedCount">{evidence.lines.length}</span></span>
      <span className="operationSavedMeta">{evidence.truncated && <span className="operationSavedTruncated">Фрагмент</span>}<time dateTime={evidence.captured_at}>{safeDateTime(evidence.captured_at)}</time><span className="operationSavedChevron" aria-hidden="true">⌄</span></span>
    </summary>
    {evidence.unit && <div className="operationSavedUnit"><span aria-hidden="true" /><code>{evidence.unit}</code></div>}
    {evidence.available && evidence.lines.length ? <div className="operationSavedLines" role="region" aria-label="Записи журнала" tabIndex={0}>
      {evidence.lines.map((line, index) => {
        const severity = level(line.priority);
        return <div className={`operationSavedLine ${severity.tone}`} key={`${line.at}:${index}`}>
          <time dateTime={line.at}>{safeDateTime(line.at)}</time><span className="operationSavedLevel">{severity.label}</span><code>{line.message}</code>
        </div>;
      })}
    </div> : <div className="operationSavedEmpty">Нет записей</div>}
  </details>;
}
