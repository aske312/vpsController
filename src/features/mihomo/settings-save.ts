/** Preserve the order of explicit edits even when requests take different times. */
export function createSettingsSaveQueue<T, R>(save: (value: T) => Promise<R>) {
  let tail = Promise.resolve();
  return (value: T): Promise<R> => {
    const result = tail.then(() => save(value));
    // An error must not replay the failed write or discard the user's next edit.
    tail = result.then(() => undefined, () => undefined);
    return result;
  };
}
