export const ROUTES_PER_PAGE = 15;

export function routePage<T>(items: T[], requested: number) {
  const pages = Math.max(1, Math.ceil(items.length / ROUTES_PER_PAGE));
  const page = Math.max(1, Math.min(pages, requested));
  const start = (page - 1) * ROUTES_PER_PAGE;
  return { items: items.slice(start, start + ROUTES_PER_PAGE), page, pages, count: items.length, from: items.length ? start + 1 : 0, to: Math.min(start + ROUTES_PER_PAGE, items.length) };
}

export function RoutePagination({ page, pages, count, from, to, label, onChange }: { page: number; pages: number; count: number; from: number; to: number; label: string; onChange: (page: number) => void }) {
  if (count <= ROUTES_PER_PAGE) return null;
  const numbers = [...new Set([1, page - 1, page, page + 1, pages])].filter(value => value >= 1 && value <= pages).sort((a, b) => a - b);
  return <nav className="routePagination" aria-label={`Страницы: ${label}`}><span>{from}–{to} из {count}</span><div>
    <button type="button" disabled={page === 1} aria-label={`${label}: предыдущая страница`} onClick={() => onChange(page - 1)}>‹</button>
    {numbers.map((number, index) => <span key={number}>{index > 0 && number - numbers[index - 1] > 1 && <span className="routePageGap" aria-hidden="true">…</span>}<button type="button" aria-label={`${label}: страница ${number}`} aria-current={number === page ? "page" : undefined} onClick={() => onChange(number)}>{number}</button></span>)}
    <button type="button" disabled={page === pages} aria-label={`${label}: следующая страница`} onClick={() => onChange(page + 1)}>›</button>
  </div></nav>;
}
