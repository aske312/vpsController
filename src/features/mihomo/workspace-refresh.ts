type Section = { path: string; accept: (value: unknown) => void };

/** Publish each successful section even when another read fails or stalls. */
export async function refreshWorkspaceSections(
  request: (path: string) => Promise<unknown>,
  sections: Record<string, Section>,
) {
  const failures: Record<string, unknown> = {};
  await Promise.all(Object.entries(sections).map(async ([key, section]) => {
    try { section.accept(await request(section.path)); }
    catch (cause) { failures[key] = cause; }
  }));
  return failures;
}
