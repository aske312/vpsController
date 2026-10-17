/** Publish one resource without waiting for unrelated reads or the next poll. */
export async function refreshResource<T>(
  read: (updates: { onRevalidated: (value: T) => void; onRevalidationError: (cause: unknown) => void }) => Promise<T>,
  accept: (value: T) => void,
  onFailure: (cause: unknown) => void,
  isCurrent: () => boolean = () => true,
) {
  const publish = (value: T) => { if (isCurrent()) accept(value); };
  const fail = (cause: unknown) => { if (isCurrent()) onFailure(cause); };
  try {
    publish(await read({ onRevalidated: publish, onRevalidationError: fail }));
  } catch (cause) { fail(cause); }
}
