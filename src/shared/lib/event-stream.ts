// SSE frames may span UTF-8 chunks and may contain several data: lines.
export async function readEventStream(response: Response, publish: (value: unknown) => void, received = () => {}) {
  if (!response.body) throw new Error("Поток ответа недоступен");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];
  let dataSize = 0;
  try {
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      received();
      buffer += decoder.decode(part.value, { stream: true });
      if (buffer.length > 1024 * 1024) throw new Error("Событие AI превышает допустимый размер");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, boundary).replace(/\r$/, "");
        buffer = buffer.slice(boundary + 1);
        if (line.startsWith("data:")) {
          dataSize += line.length;
          if (dataSize > 1024 * 1024) throw new Error("Событие AI превышает допустимый размер");
          data.push(line.slice(5).replace(/^ /, ""));
        }
        else if (!line && data.length) {
          const value = JSON.parse(data.join("\n"));
          data = [];
          dataSize = 0;
          publish(value);
        }
      }
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
