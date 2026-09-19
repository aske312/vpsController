export const PROFILES_PER_PAGE = 10;

export function profilePage(items: number, requested: number) {
  const pages = Math.max(1, Math.ceil(items / PROFILES_PER_PAGE));
  const page = Math.max(1, Math.min(pages, requested));
  const offset = (page - 1) * PROFILES_PER_PAGE;
  return { page, pages, offset, start: items ? offset + 1 : 0, end: Math.min(offset + PROFILES_PER_PAGE, items) };
}
