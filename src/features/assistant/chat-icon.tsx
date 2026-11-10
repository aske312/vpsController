const paths = {
  plus: 'M12 5v14M5 12h14', send: 'm6 11 6-6 6 6M12 5v14',
  close: 'm6 6 12 12M18 6 6 18', chevron: 'm6 9 6 6 6-6',
  refresh: 'M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 12M4 12l2.3 6A7 7 0 0 0 18 17',
  new: 'M12 5H5v14h14v-7M10 14l1-4 7-7 3 3-7 7-4 1Z',
  file: 'M14 3H6v18h12V7l-4-4ZM14 3v5h4M9 12h6M9 16h6',
  copy: 'M9 9h12v12H9ZM15 9V3H3v12h6', check: 'm5 12 4 4L19 6',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  module: 'M4 4h6v6H4ZM14 4h6v6h-6ZM4 14h6v6H4ZM14 14h6v6h-6Z',
  chat: 'M4 4h16v12H9l-5 4V4Z', settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
  users: 'M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM2 21v-2a7 7 0 0 1 14 0v2M16 4a4 4 0 0 1 0 8M18 15a5 5 0 0 1 4 4v2',
  person: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21v-2a8 8 0 0 1 16 0v2',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
};
export function ChatIcon({ name }: { name: keyof typeof paths }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
