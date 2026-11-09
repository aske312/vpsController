import type { ReactNode } from "react";
import { NetworkRouteSummaryBadge } from "./network-route-badge";
import { RouteResourceIcon } from "./route-resource-icon";
import type { resourceCountry } from "./resource-country";
import type { RouteIconKind } from "./route-type-icon";
import type { RouteSummary } from "./route-summary";

type Props = {
  title: string;
  address?: string;
  types: string[];
  icon: RouteIconKind;
  country?: ReturnType<typeof resourceCountry>;
  cdn?: boolean;
  summary: RouteSummary;
  details: { label: string; value: ReactNode }[];
  onOpen: () => void;
};

export function RouteStatusCard({ title, address, types, icon, country, cdn, summary, details, onOpen }: Props) {
  return <button type="button" className="routeLibraryRow" data-state={summary.state} data-kind={icon} aria-haspopup="dialog" aria-label={`Открыть ${title}`} onClick={onOpen}>
    <span className="routeCardHeading">
      <span className="routeCardIcon" aria-hidden="true"><RouteResourceIcon kind={icon} country={country} cdn={cdn} /></span>
      <span className="routeCardTypes" aria-label="Тип адреса">{types.map(type => <span key={type}>{type}</span>)}</span>
    </span>
    <span className="routeCardIdentity">
      <strong title={title}>{title}</strong>
      {address && address !== title && <code>{address}</code>}
    </span>
    <span className="routeCardFacts">{details.map(detail => <span className="routeCardFact" key={detail.label}>
      <span>{detail.label}</span><span>{detail.value}</span>
    </span>)}</span>
    <span className="routeCardFooter">
      <span className="routeCardStatus"><span className="routeCardStatusDot" aria-hidden="true" /><NetworkRouteSummaryBadge summary={summary} /></span>
      <span className="routeCardOpen" aria-hidden="true">Подробнее <span>→</span></span>
    </span>
  </button>;
}
